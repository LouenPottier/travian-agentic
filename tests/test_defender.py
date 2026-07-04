"""Verrou du joueur IA défenseur (Phase 4) — SANS Claude Code (LLM jamais appelé).

On teste : (1) le digest compact + la décision de réveil (parité d'observation : pas de
composition ennemie) ; (2) l'identité par en-tête X-Acting-Player + l'ownership toujours
imposé ; (3) les outils défensifs (reports/trapper/set_plan) ; (4) le seeding idempotent.
Appels HTTP routés en in-process (ASGITransport).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx

from app import store

store.DB_PATH = Path(tempfile.mkdtemp()) / "defender_test.db"
import app.main as M          # noqa: E402  (seed_world sur la DB temp)
M.seed_world()  # re-seed explicite : app.main n'est importé qu'une fois (multi-modules)
from app.agents import tools as T       # noqa: E402
from app.agents import playbook as PB    # noqa: E402
from app.agents import defender as DEF   # noqa: E402
from app.engine import situation as SIT  # noqa: E402

OUR_DB = store.DB_PATH


def _pin():
    store.DB_PATH = OUR_DB


def _agent():
    pid = store.find_player_by_name("Défenseur IA")
    vid = store.player_villages(pid)[0]
    return pid, vid


def test_digest_and_should_wake():
    _pin()
    pid, vid = _agent()
    now = time.time()

    # Calme : aucune menace, plan considéré actif ⇒ pas de réveil.
    d0 = SIT.build_digest(pid, now)
    assert d0["threats"] == []
    assert SIT.should_wake(d0, plan_active=True) is None
    # Aucun plan encore posé ⇒ réveil pour (re)planifier.
    assert SIT.should_wake(d0, plan_active=False) is not None

    # Injecter une attaque ENTRANTE vers le village IA (aller).
    human = M.HUMAN_PLAYER_ID
    hv = store.player_villages(human)[0]
    store.insert_movement(hv, vid, human, "attack", "outbound",
                          [10, 5, 0, 0, 0, 0, 0, 0, 0, 0], now + 500)

    d1 = SIT.build_digest(pid, now)
    assert d1["threats"], "l'attaque entrante devrait apparaître"
    th = d1["threats"][0]
    assert th["kind"] == "attack" and th["n"] == 15
    # Parité d'observation : uniquement kind/ETA/effectif (+ localisation), JAMAIS le
    # vecteur de composition.
    assert set(th.keys()) == {"kind", "arrive_in", "n", "village_id", "name"}
    assert SIT.should_wake(d1, plan_active=True), "une menace doit réveiller"


async def _scenario_tools():
    T._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=M.app), base_url="http://test")
    try:
        pid, vid = _agent()
        human_v = store.player_villages(M.HUMAN_PLAYER_ID)[0]

        # Ownership TOUJOURS imposé : en agissant comme l'IA, build sur un village
        # HUMAIN ⇒ 403 (l'agent ne peut agir que sur ses villages).
        T.set_acting_player(pid)
        forbidden = await T._req("POST", f"/api/village/{human_v}/build/1")
        assert forbidden.startswith("ERREUR (403)"), forbidden

        # Outils défensifs forwardent au nom du bon joueur.
        reports = await T.get_reports.handler({})
        assert "reports" in reports["content"][0]["text"]
        # get_trapper forwarde bien vers l'endpoint trappeur du bon village (le village IA
        # n'a pas encore de trappeur ⇒ 400 « Pas de trappeur » : c'est la PREUVE du forward,
        # pas une erreur réseau).
        trapper = await T.get_trapper.handler({"village_id": vid})
        ttxt = trapper["content"][0]["text"]
        assert "ERREUR RÉSEAU" not in ttxt and "trappeur" in ttxt.lower()

        # set_plan (outil) : enregistre les ordres permanents du joueur agissant.
        sp = await T.set_plan.handler(
            {"actions": [{"op": "traps", "village_id": vid, "count": 3}]})
        assert "Plan enregistré" in sp["content"][0]["text"]
        assert PB.has_plan(pid)
    finally:
        PB.clear_owner(_agent()[0])
        if PB._runner_task:
            PB._runner_task.cancel()
        T.set_acting_player(None)
        await T.aclose()


def test_acting_player_and_defensive_tools():
    _pin()
    asyncio.run(_scenario_tools())


def test_unplug_keeps_stack_stop_clears():
    """Cœur du modèle : débrancher le LLM CONSERVE la pile (l'exécuteur continue) ;
    arrêter la VIDE. (Aucun LLM appelé : on manipule le registre + la pile directement.)"""
    _pin()
    pid, vid = _agent()
    PB._PLANS[vid] = {"owner_id": pid,
                      "orders": PB.normalize([{"op": "traps", "village_id": vid, "count": 3}])}
    DEF.AGENTS[vid] = DEF.DefenderRun(village_id=vid, owner_id=pid)

    asyncio.run(DEF.unplug(vid))
    assert PB.get_plan(vid), "unplug doit CONSERVER la pile d'ordres"
    assert DEF.status_for(vid)["status"] == "unplugged"

    asyncio.run(DEF.stop(vid))
    assert not PB.get_plan(vid), "stop doit VIDER la pile d'ordres"
    assert DEF.status_for(vid)["status"] == "stopped"

    DEF.AGENTS.pop(vid, None)
    if PB._runner_task:
        PB._runner_task.cancel()


def test_ensure_agent_player_idempotent():
    _pin()
    names = [p["name"] for p in store.all_players()]
    assert names.count("Défenseur IA") == 1
    M._ensure_agent_player()  # 2ᵉ appel : ne doit pas dupliquer
    names2 = [p["name"] for p in store.all_players()]
    assert names2.count("Défenseur IA") == 1


if __name__ == "__main__":
    test_digest_and_should_wake()
    test_acting_player_and_defensive_tools()
    test_ensure_agent_player_idempotent()
    print("OK")
