"""API FastAPI + UI — Phase 1/2 : villages persistés (SQLite), plusieurs joueurs.

Lancer : ./venv/bin/uvicorn app.main:app --reload
Puis ouvrir http://127.0.0.1:8000/
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import store
from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe, TRIBE_NAMES_FR
from app.data.units import UNITS
from app.engine import village as V
from app.engine import movement as M

app = FastAPI(title="Travian local — T4.6")

SERVER_SPEED = 100  # temps ×100 pour faciliter les tests
WEB = Path(__file__).resolve().parent.parent / "web"

HUMAN_PLAYER_ID: int | None = None


def seed_world() -> None:
    """Crée le monde de départ si la base est vide : toi + un voisin NPC."""
    global HUMAN_PLAYER_ID
    store.init_db()
    if not store.is_empty():
        rows = store.list_villages()
        HUMAN_PLAYER_ID = next((r["player_id"] for r in rows), None)
        return
    HUMAN_PLAYER_ID = store.create_player("Toi", Tribe.GAULS)
    store.insert_village(V.new_village(
        "Mon village", Tribe.GAULS, server_speed=SERVER_SPEED,
        x=0, y=0, player_id=HUMAN_PLAYER_ID, is_capital=True))
    npc = store.create_player("Voisin", Tribe.TEUTONS, is_npc=True)
    store.insert_village(V.new_village(
        "Camp teuton", Tribe.TEUTONS, server_speed=SERVER_SPEED,
        x=3, y=1, player_id=npc, is_capital=True))


seed_world()


def _slot_type(idx: int) -> str:
    if idx <= 18:
        return "res"
    if idx == V.RALLY_SLOT:
        return "rally"
    if idx == V.WALL_SLOT:
        return "wall"
    return "village"


def serialize(v: V.Village) -> dict:
    V.tick(v)
    store.save_village(v)  # persiste l'état avancé (ressources, file)
    prod = V.net_production(v)
    caps = V.capacities(v)
    now = time.time()
    slots = []
    for idx in range(1, 41):
        s = v.slots.get(idx)
        if s is not None:
            b = BLD.get(s.building_id)
            order = next((o for o in v.queue if o.slot_index == idx), None)
            slots.append({
                "index": idx, "empty": False,
                "building_id": s.building_id, "name": b.name,
                "level": s.level, "max_level": b.max_level, "slot_type": b.slot,
                "next_cost": b.cost_at(s.level + 1) if s.level < b.max_level else None,
                "next_time": round(V.build_time(v, b, s.level + 1)) if s.level < b.max_level else None,
                "finish_in": round(order.finish_at - now) if order else None,
                "target_level": order.target_level if order else None,
            })
        else:
            buildable = [{"id": b.id, "name": b.name, "cost": b.cost_at(1),
                          "time": round(V.build_time(v, b, 1))}
                         for b in V.available_buildings(v, idx)]
            slots.append({"index": idx, "empty": True,
                          "slot_type": _slot_type(idx), "buildable": buildable})

    # Militaire : troupes, file d'entraînement, bâtiments d'entraînement
    units = UNITS[v.tribe]
    levels = V.building_levels(v)
    troops = [{"index": i, "name": units[i].name, "count": c}
              for i, c in enumerate(v.troops)]
    training = [{"building": BLD.get(t.building_id).name, "unit": units[t.unit_index].name,
                 "remaining": t.remaining, "next_in": round(t.next_finish - now)}
                for t in v.training]
    military = []
    for bid in (B.BARRACKS, B.STABLES, B.WORKSHOP, B.RESIDENCE):
        lvl = levels.get(bid, 0)
        if lvl < 1:
            continue
        b = BLD.get(bid)
        military.append({
            "building_id": bid, "building": b.name, "level": lvl,
            "units": [{"index": i, "name": u.name, "cost": list(u.cost),
                       "time": round(u.train_time * b.benefit(lvl) / v.server_speed)}
                      for i, u in V.trainable_units(v, bid)],
        })

    moves = []
    for m in store.movements_for(v.id):
        incoming = m["target_id"] == v.id if m["phase"] == "outbound" else m["origin_id"] == v.id
        moves.append({"kind": m["kind"], "phase": m["phase"],
                      "dir": "in" if incoming else "out",
                      "n": sum(json.loads(m["units"])),
                      "arrive_in": round(m["arrive_at"] - now)})

    return {
        "id": v.id, "name": v.name, "tribe": TRIBE_NAMES_FR[v.tribe],
        "x": v.x, "y": v.y, "is_own": v.player_id == HUMAN_PLAYER_ID,
        "server_speed": v.server_speed,
        "resources": [round(r) for r in v.resources], "capacities": caps,
        "production": [round(p, 1) for p in prod], "population": V.population(v),
        "troop_upkeep": V.troop_upkeep(v),
        "queue_len": len(v.queue), "max_queue": v.max_queue, "slots": slots,
        "troops": troops, "training": training, "military": military,
        "movements": moves,
    }


def _get(village_id: int) -> V.Village:
    M.process_due(time.time())  # résout les mouvements arrivés (combats, retours)
    v = store.load_village(village_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Village introuvable.")
    return v


class SendArmy(BaseModel):
    target_id: int
    kind: str  # attack | raid | reinforce
    units: list[int]


@app.get("/api/villages")
def villages():
    rows = store.list_villages()
    for r in rows:
        r["is_own"] = r["player_id"] == HUMAN_PLAYER_ID
    return {"villages": rows, "human_player_id": HUMAN_PLAYER_ID}


@app.get("/api/village/{village_id}")
def get_village(village_id: int):
    return serialize(_get(village_id))


@app.post("/api/village/{village_id}/build/{slot_index}")
def build(village_id: int, slot_index: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        order = V.enqueue_build(v, slot_index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "finish_in": round(order.finish_at - time.time()),
            "village": serialize(v)}


@app.post("/api/village/{village_id}/construct/{slot_index}/{building_id}")
def construct(village_id: int, slot_index: int, building_id: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        order = V.enqueue_new_building(v, slot_index, building_id)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "finish_in": round(order.finish_at - time.time()),
            "village": serialize(v)}


@app.post("/api/village/{village_id}/train/{building_id}/{unit_index}/{count}")
def train(village_id: int, building_id: int, unit_index: int, count: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_training(v, building_id, unit_index, count)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.post("/api/village/{village_id}/send")
def send_army(village_id: int, body: SendArmy):
    if body.kind not in ("attack", "raid", "reinforce"):
        raise HTTPException(status_code=400, detail="Type d'ordre invalide.")
    units = (body.units + [0] * 10)[:10]
    try:
        info = M.send(village_id, body.target_id, HUMAN_PLAYER_ID, body.kind, units)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "village": serialize(_get(village_id))}


@app.get("/api/reports")
def reports():
    M.process_due(time.time())
    return {"reports": store.reports_for(HUMAN_PLAYER_ID)}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# Sert les assets statiques (images de bâtiments, etc.) sous /img.
app.mount("/img", StaticFiles(directory=WEB / "img"), name="img")
