"""Verrou de la mécanique des macros (Phase 4) — SANS Claude Code.

On ne teste pas le LLM (non déterministe) mais le **contrat des outils** : chaque
`@tool` de `app.agents.tools` forwarde-t-il bien vers l'endpoint joueur, avec l'effet
attendu, et une action illégale renvoie-t-elle l'erreur de l'endpoint (preuve « sans
tricher ») ? Les appels HTTP des outils sont routés en in-process vers l'app FastAPI via
`httpx.ASGITransport` (aucun serveur, aucun réseau).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from app import store

# Monde temporaire AVANT d'importer app.main (qui appelle seed_world à l'import).
store.DB_PATH = Path(tempfile.mkdtemp()) / "macro_test.db"
import app.main as M          # noqa: E402  (seed_world s'exécute ici, sur notre DB temp)
from app.agents import tools as T   # noqa: E402
from app.engine import village as V  # noqa: E402

OUR_DB = store.DB_PATH
HUMAN = M.HUMAN_PLAYER_ID
HOME = 1  # « Mon village » (cf. seed)


def _pin_db():
    """Réaffirme notre DB (d'autres modules de test rebindent store.DB_PATH)."""
    store.DB_PATH = OUR_DB


def _give_resources(vid: int, amount: int = 100_000) -> None:
    import time
    v = store.load_village(vid)
    V.tick(v, time.time())
    v.resources = [float(amount)] * 4
    store.save_village(v)


async def _run_tool(tool, args: dict) -> str:
    """Exécute le handler d'un outil et renvoie son texte."""
    res = await tool.handler(args)
    return res["content"][0]["text"]


async def _scenario():
    # Router les appels HTTP des outils dans l'app en in-process.
    T._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=M.app), base_url="http://test")
    try:
        # 1) get_state forwarde vers GET /api/village/{id} et renvoie l'état du village.
        state_txt = await _run_tool(T.get_state, {"village_id": HOME})
        assert "ERREUR" not in state_txt, state_txt
        state = json.loads(state_txt)
        assert state["id"] == HOME
        before = sum(state["resources"])

        # Choisir un champ de ressources améliorable (slot_type res, non vide, < max).
        slot = next(s for s in state["slots"]
                    if not s.get("empty") and s.get("slot_type") == "res"
                    and s["level"] < s["max_level"])

        # 2) Donner des ressources puis build → effet réel (ok + ressources dépensées).
        _give_resources(HOME)
        build_txt = await _run_tool(T.build, {"village_id": HOME, "slot_index": slot["index"]})
        assert "ERREUR" not in build_txt, build_txt
        build = json.loads(build_txt)
        assert build["ok"] is True and "finish_in" in build
        # La construction a bien été mise en file (un chantier dans le village).
        assert build["village"]["queue_len"] >= 1
        # Les ressources ont été dépensées (coût déduit).
        assert sum(build["village"]["resources"]) < before + 1

        # 3) Action ILLÉGALE : build sur un village qui ne t'appartient pas → 403.
        foreign = next(r["id"] for r in store.list_villages()
                       if r["player_id"] != HUMAN)
        forbidden = await _run_tool(T.build, {"village_id": foreign, "slot_index": 1})
        assert forbidden.startswith("ERREUR (403)"), forbidden

        # 4) Action à CORPS JSON (send_army) mal formée → l'endpoint valide et renvoie 400.
        #    Prouve que le corps (kind/units) atteint bien l'endpoint via l'outil.
        bad_send = await _run_tool(T.send_army,
                                   {"village_id": HOME, "kind": "raid", "units": [0] * 10})
        assert bad_send.startswith("ERREUR (400)") and "Cible" in bad_send, bad_send

        # 5) wait borné : renvoie l'état frais sans dormir longtemps.
        waited = await _run_tool(T.wait, {"village_id": HOME, "seconds": 0})
        assert "État à jour" in waited and "ERREUR" not in waited
    finally:
        await T.aclose()


def test_tools_forward_and_guard():
    _pin_db()
    asyncio.run(_scenario())


def test_tool_names_and_gate_invariants():
    # Tous les outils exposés sont sous le préfixe mcp__travian__ (ce que le gate autorise).
    assert T.ALLOWED_TOOL_NAMES
    assert all(n.startswith(T.TOOL_PREFIX) for n in T.ALLOWED_TOOL_NAMES)
    # Le périmètre « tout » est présent (militaire inclus) ; wait/finish pilotent la boucle.
    names = {t.name for t in T.ALL_TOOLS}
    for expected in ("get_state", "build", "train", "send_army", "settle",
                     "occupy_oasis", "wait", "finish"):
        assert expected in names, expected


if __name__ == "__main__":
    test_tool_names_and_gate_invariants()
    test_tools_forward_and_guard()
    print("OK")
