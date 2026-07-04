"""Verrou de l'exécuteur d'ordres permanents (Phase 4) — SANS Claude Code.

On teste la brique Python « 0 LLM » : un ordre déclaratif se déclenche via la surface HTTP
enforced (build mis en file, ressources dépensées) ; une garde bloque un ordre inabordable ;
le schéma rejette les ordres invalides. Appels HTTP routés en in-process (ASGITransport).
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import httpx

from app import store

store.DB_PATH = Path(tempfile.mkdtemp()) / "playbook_test.db"
import app.main as M          # noqa: E402  (seed_world sur la DB temp)
M.seed_world()  # re-seed explicite : app.main n'est importé qu'une fois (multi-modules)
from app.agents import tools as T       # noqa: E402
from app.agents import playbook as PB    # noqa: E402
from app.engine import village as V      # noqa: E402

OUR_DB = store.DB_PATH


def _pin():
    store.DB_PATH = OUR_DB


def _agent():
    pid = store.find_player_by_name("Défenseur IA")
    vid = store.player_villages(pid)[0]
    return pid, vid


def _set_resources(vid: int, amount: float) -> None:
    v = store.load_village(vid)
    V.tick(v, time.time())
    v.resources = [float(amount)] * 4
    store.save_village(v)


def _plan(vid: int, owner: int, actions: list[dict]) -> None:
    # Registre direct (sans démarrer le runner de fond) pour un test déterministe.
    PB._PLANS[vid] = {"owner_id": owner, "orders": PB.normalize(actions)}


async def _scenario():
    T._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=M.app), base_url="http://test")
    try:
        pid, vid = _agent()

        # 1) Ordre build éligible (ressources OK) ⇒ se déclenche, chantier mis en file.
        _set_resources(vid, 200_000)
        v = store.load_village(vid)
        target = v.slots[1].level + 1
        _plan(vid, pid, [{"op": "build", "village_id": vid, "slot": 1, "level": target}])
        log = await PB.step(vid)
        assert log, "l'ordre build aurait dû se déclencher"
        assert store.load_village(vid).queue, "un chantier devrait être en file"

        # 2) Garde ressources : ordre train inabordable (0 ressource) ⇒ rien déclenché,
        #    et AUCUNE erreur (l'ordre reste pour plus tard).
        _set_resources(vid, 0)
        _plan(vid, pid, [{"op": "train", "village_id": vid,
                          "building_id": 19, "unit": 0, "count": 10}])
        log2 = await PB.step(vid)
        assert log2 == [], "train inabordable ne doit rien déclencher"
    finally:
        PB._PLANS.clear()
        await T.aclose()


def test_playbook_executes_and_guards():
    _pin()
    asyncio.run(_scenario())


def test_normalize_rejects_invalid():
    _pin()
    # op inconnu
    try:
        PB.normalize([{"op": "bogus", "village_id": 1}])
        assert False, "op invalide aurait dû lever"
    except PB.PlanError:
        pass
    # champ manquant
    try:
        PB.normalize([{"op": "build", "village_id": 1, "slot": 1}])  # level manquant
        assert False, "champ manquant aurait dû lever"
    except PB.PlanError:
        pass
    # ordre bien formé accepté
    ok = PB.normalize([{"op": "traps", "village_id": 2, "count": 5}])
    assert ok == [{"op": "traps", "village_id": 2, "count": 5}]


def test_set_plan_multi_groups_and_clears():
    _pin()
    pid, vid = _agent()
    orders = PB.set_plan_multi(pid, [{"op": "traps", "village_id": vid, "count": 5}])
    assert orders and PB.has_plan(pid) and PB.get_plan(vid)
    PB.set_plan_multi(pid, [])  # liste vide ⇒ efface
    assert not PB.has_plan(pid)
    if PB._runner_task:
        PB._runner_task.cancel()


if __name__ == "__main__":
    test_normalize_rejects_invalid()
    test_set_plan_multi_groups_and_clears()
    test_playbook_executes_and_guards()
    print("OK")
