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
from app.engine import world as W
from app.engine import effects as EFF

app = FastAPI(title="Travian local — T4.6")

SERVER_SPEED = 100  # temps ×100 pour faciliter les tests
WEB = Path(__file__).resolve().parent.parent / "web"

HUMAN_PLAYER_ID: int | None = None


def _find_free_valley(near_x: int, near_y: int, occupied: set[tuple[int, int]]) -> tuple[int, int]:
    """Vallée libre la plus proche d'un point (spirale en anneaux croissants)."""
    for radius in range(W.WORLD_RADIUS + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue  # uniquement le bord de l'anneau courant
                x, y = near_x + dx, near_y + dy
                if (x, y) in occupied:
                    continue
                t = store.get_tile(x, y)
                if t and t["kind"] == "valley":
                    return x, y
    raise RuntimeError("Aucune vallée libre sur la carte.")


def seed_world() -> None:
    """Crée le monde de départ si la base est vide : la carte, toi + un voisin NPC."""
    global HUMAN_PLAYER_ID
    store.init_db()
    if store.world_is_empty():
        store.insert_tiles(W.generate_world())
    if not store.is_empty():
        rows = store.list_villages()
        HUMAN_PLAYER_ID = next((r["player_id"] for r in rows), None)
        return
    HUMAN_PLAYER_ID = store.create_player("Toi", Tribe.GAULS)
    store.insert_village(V.new_village(
        "Mon village", Tribe.GAULS, server_speed=SERVER_SPEED,
        x=0, y=0, player_id=HUMAN_PLAYER_ID, is_capital=True))
    npc = store.create_player("Voisin", Tribe.TEUTONS, is_npc=True)
    nx, ny = _find_free_valley(3, 1, {(0, 0)})
    store.insert_village(V.new_village(
        "Camp teuton", Tribe.TEUTONS, server_speed=SERVER_SPEED,
        x=nx, y=ny, player_id=npc, is_capital=True))


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
                "effect": EFF.building_effect(v, s.building_id, s.level),
                "next_effect": (EFF.building_effect(v, s.building_id, s.level + 1)
                                if s.level < b.max_level else None),
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
    training = [{"building_id": t.building_id, "building": BLD.get(t.building_id).name,
                 "unit": units[t.unit_index].name,
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
                       "time": round(u.train_time * b.benefit(lvl) / v.server_speed),
                       "researched": V.is_researched(v, i),
                       "research_required": V.needs_research(v, i)}
                      for i, u in V.trainable_units(v, bid)],
        })

    moves = []
    for m in store.movements_for(v.id):
        incoming = m["target_id"] == v.id if m["phase"] == "outbound" else m["origin_id"] == v.id
        entry = {"kind": m["kind"], "phase": m["phase"],
                 "dir": "in" if incoming else "out",
                 "n": sum(json.loads(m["units"])),
                 "arrive_in": round(m["arrive_at"] - now)}
        if m["kind"] == "trade":
            entry["cargo"] = json.loads(m["loot"])  # ressources transportées
            entry["merchants"] = m["merchants"]
        moves.append(entry)

    # Place de marché : niveau, marchands (total / libres), capacité par marchand.
    market = None
    if M.merchants_total(v) > 0:
        market = {"level": M.merchants_total(v),
                  "merchants_total": M.merchants_total(v),
                  "merchants_free": M.merchants_available(v),
                  "capacity": M.merchant_capacity(v)}

    return {
        "id": v.id, "name": v.name, "tribe": TRIBE_NAMES_FR[v.tribe],
        "x": v.x, "y": v.y, "is_own": v.player_id == HUMAN_PLAYER_ID,
        "server_speed": v.server_speed,
        "resources": [round(r) for r in v.resources], "capacities": caps,
        "production": [round(p, 1) for p in prod], "population": V.population(v),
        "troop_upkeep": V.troop_upkeep(v),
        "queue_len": len(v.queue), "max_queue": v.max_queue, "slots": slots,
        "troops": troops, "training": training, "military": military,
        "movements": moves, "market": market,
    }


def _get(village_id: int) -> V.Village:
    M.process_due(time.time())  # résout les mouvements arrivés (combats, retours)
    v = store.load_village(village_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Village introuvable.")
    return v


class SendArmy(BaseModel):
    kind: str  # attack | raid | reinforce
    units: list[int]
    target_id: int | None = None       # cible village
    target_x: int | None = None        # cible oasis (coordonnées)
    target_y: int | None = None


@app.get("/api/villages")
def villages():
    rows = store.list_villages()
    for r in rows:
        r["is_own"] = r["player_id"] == HUMAN_PLAYER_ID
    return {"villages": rows, "human_player_id": HUMAN_PLAYER_ID}


def _villages_by_xy() -> dict[tuple[int, int], dict]:
    out = {}
    for r in store.list_villages():
        r["is_own"] = r["player_id"] == HUMAN_PLAYER_ID
        out[(r["x"], r["y"])] = r
    return out


@app.get("/api/map")
def map_view(cx: int = 0, cy: int = 0, r: int = 7):
    """Viewport de la carte : cases (vallées/oasis/villages) dans un carré ±r."""
    r = max(1, min(r, 15))
    villages = _villages_by_xy()
    tiles = []
    for t in store.tiles_in_box(cx - r, cx + r, cy - r, cy + r):
        cell = {"x": t["x"], "y": t["y"], "kind": t["kind"]}
        v = villages.get((t["x"], t["y"]))
        if v is not None:
            cell["kind"] = "village"
            cell["village"] = {"id": v["id"], "name": v["name"], "player": v["player"],
                               "is_own": v["is_own"], "is_capital": bool(v["is_capital"])}
        elif t["kind"] == "oasis":
            cell["oasis"] = {"label": W.oasis_label(t["layout"]),
                             "emoji": W.oasis_emoji(t["layout"]),
                             "animals": W.animal_count(t["animals"])}
        else:
            cell["layout"] = t["layout"]
        tiles.append(cell)
    return {"center": [cx, cy], "radius": r, "world_radius": W.WORLD_RADIUS,
            "tiles": tiles}


@app.get("/api/tile/{x}/{y}")
def tile_detail(x: int, y: int):
    t = store.get_tile(x, y)
    if t is None:
        raise HTTPException(status_code=404, detail="Hors de la carte.")
    v = _villages_by_xy().get((x, y))
    out = {"x": x, "y": y, "kind": t["kind"]}
    if v is not None:
        out["kind"] = "village"
        out["village"] = {"id": v["id"], "name": v["name"], "player": v["player"],
                          "is_own": v["is_own"], "is_capital": bool(v["is_capital"])}
    elif t["kind"] == "oasis":
        bonus = W.oasis_bonus(t["layout"])
        res_names = ["bois", "argile", "fer", "céréales"]
        out["oasis"] = {
            "label": W.oasis_label(t["layout"]),
            "emoji": W.oasis_emoji(t["layout"]),
            "bonus": [{"resource": res_names[i], "percent": p} for i, p in bonus.items()],
            "animals": W.animal_breakdown(t["animals"]),
            "total_animals": W.animal_count(t["animals"]),
        }
    else:
        w, c, i, cr = (int(n) for n in t["layout"].split("-"))
        out["valley"] = {"layout": t["layout"], "fields": {"bois": w, "argile": c,
                                                           "fer": i, "céréales": cr}}
    return out


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


@app.get("/api/village/{village_id}/academy")
def academy(village_id: int):
    """Académie : unités recherchables (coût, temps), déjà recherchées, en cours."""
    v = _get(village_id)
    units = UNITS[v.tribe]
    level = V.building_levels(v).get(B.ACADEMY, 0)
    now = time.time()
    items = []
    for i, u in V.researchable_units(v):
        in_queue = next((r for r in v.research_queue if r.unit_index == i), None)
        items.append({"index": i, "name": u.name, "researched": bool(v.research[i]),
                      "cost": list(V.research_cost(v, i)),
                      "time": round(V.research_time(v, i)),
                      "in_queue": round(in_queue.finish_at - now) if in_queue else None})
    return {"level": level, "units": items}


@app.post("/api/village/{village_id}/research/{unit_index}")
def research(village_id: int, unit_index: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_research(v, unit_index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.get("/api/village/{village_id}/smithy")
def smithy(village_id: int):
    """Forge : niveau d'amélioration courant et coût/temps du niveau suivant par unité."""
    v = _get(village_id)
    level = V.smithy_level(v)
    now = time.time()
    items = []
    for i, u in V.upgradable_units(v):
        cur = v.upgrades[i]
        in_queue = next((o for o in v.upgrade_queue if o.unit_index == i), None)
        nxt = cur + 1
        items.append({
            "index": i, "name": u.name, "level": cur,
            "can_upgrade": nxt <= level and nxt <= 20,
            "next_level": nxt if nxt <= 20 else None,
            "next_cost": list(V.upgrade_cost(v, i, nxt)) if nxt <= 20 else None,
            "next_time": round(V.upgrade_time(v, i, nxt)) if nxt <= 20 else None,
            "in_queue": ({"target": in_queue.target_level,
                          "finish_in": round(in_queue.finish_at - now)} if in_queue else None),
        })
    return {"level": level, "units": items}


@app.post("/api/village/{village_id}/upgrade/{unit_index}")
def upgrade_unit(village_id: int, unit_index: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_upgrade(v, unit_index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.get("/api/village/{village_id}/trapper")
def trapper(village_id: int):
    """Trappeur : capacité de pièges, pièges construits / en cours, coût unitaire."""
    v = _get(village_id)
    if V.building_levels(v).get(B.TRAPPER, 0) < 1:
        raise HTTPException(status_code=400, detail="Pas de trappeur dans ce village.")
    now = time.time()
    pending = [{"remaining": tp.remaining, "next_in": round(tp.next_finish - now)}
               for tp in v.trap_queue]
    return {"capacity": V.trap_capacity(v), "built": v.traps,
            "pending": V.traps_pending(v), "queue": pending,
            "free": V.trap_capacity(v) - V.traps_total(v),
            "trap_cost": list(V.TRAP_COST),
            "trap_time": round(V.TRAP_TIME / v.server_speed)}


@app.post("/api/village/{village_id}/traps/{count}")
def build_traps(village_id: int, count: int):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_traps(v, count)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.post("/api/village/{village_id}/send")
def send_army(village_id: int, body: SendArmy):
    if body.kind not in ("attack", "raid", "reinforce"):
        raise HTTPException(status_code=400, detail="Type d'ordre invalide.")
    if body.target_id is None and (body.target_x is None or body.target_y is None):
        raise HTTPException(status_code=400, detail="Cible manquante.")
    units = (body.units + [0] * 10)[:10]
    try:
        info = M.send(village_id, body.target_id, HUMAN_PLAYER_ID, body.kind, units,
                      target_x=body.target_x, target_y=body.target_y)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "village": serialize(_get(village_id))}


class SendResources(BaseModel):
    target_id: int
    amounts: list[int]  # [bois, argile, fer, céréales]


@app.get("/api/village/{village_id}/market")
def market(village_id: int):
    """Infos de la place de marché + cibles d'envoi possibles (autres villages)."""
    v = _get(village_id)
    if M.merchants_total(v) < 1:
        raise HTTPException(status_code=400, detail="Pas de place de marché dans ce village.")
    targets = []
    for r in store.list_villages():
        if r["id"] == v.id:
            continue
        d = M.distance(v.x, v.y, r["x"], r["y"])
        targets.append({"id": r["id"], "name": r["name"], "player": r["player"],
                        "x": r["x"], "y": r["y"], "is_own": r["player_id"] == HUMAN_PLAYER_ID,
                        "distance": round(d, 1),
                        "travel": round(M.merchant_seconds(v.x, v.y, r["x"], r["y"],
                                                           v.tribe, v.server_speed))})
    targets.sort(key=lambda t: t["distance"])
    return {"level": M.merchants_total(v), "merchants_total": M.merchants_total(v),
            "merchants_free": M.merchants_available(v), "capacity": M.merchant_capacity(v),
            "resources": [round(r) for r in v.resources], "targets": targets}


@app.post("/api/village/{village_id}/trade")
def trade(village_id: int, body: SendResources):
    amounts = (body.amounts + [0, 0, 0, 0])[:4]
    try:
        info = M.send_resources(village_id, body.target_id, HUMAN_PLAYER_ID, amounts)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "merchants": info["merchants"],
            "village": serialize(_get(village_id))}


@app.get("/api/reports")
def reports():
    M.process_due(time.time())
    return {"reports": store.reports_for(HUMAN_PLAYER_ID)}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# Sert les assets statiques (images de bâtiments, etc.) sous /img.
app.mount("/img", StaticFiles(directory=WEB / "img"), name="img")
