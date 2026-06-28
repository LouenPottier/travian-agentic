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
from app.engine import hero as HERO
from app.engine import expansion as EXP
from app.engine import oasis as OAS
from app.data import items as IT

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
        # Parties antérieures (avant la Phase 3) : créer le héros s'il manque, rattaché
        # à la première ville du joueur humain.
        if HUMAN_PLAYER_ID is not None and HERO.load(HUMAN_PLAYER_ID) is None:
            vids = store.player_villages(HUMAN_PLAYER_ID)
            if vids:
                HERO.get_or_create(HUMAN_PLAYER_ID, vids[0])
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
    # Héros du joueur, rattaché à sa capitale.
    cap = store.player_villages(HUMAN_PLAYER_ID)[0]
    HERO.get_or_create(HUMAN_PLAYER_ID, cap)


seed_world()


def _tick_player(player_id: int, now: float) -> None:
    """Avance l'état « joueur » : héros (santé/production/aventure), apparition
    d'aventures, accumulation des points de culture."""
    h = HERO.load(player_id)
    if h is not None:
        home = store.load_village(h.home_village_id)
        if home is not None:
            if HERO.tick(h, home, now):
                store.save_village(home)
            HERO.replenish_adventures(player_id, h, now)
            HERO.save(h)
    EXP.accumulate_culture(player_id, now)


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
    # Seuls caserne/écurie/atelier réduisent le temps d'entraînement (train_bonus).
    # La résidence n'a pas de réduction (son `benefit` renvoie un dict de slots).
    TRAIN_BONUS_BUILDINGS = (B.BARRACKS, B.STABLES, B.WORKSHOP,
                             B.GREAT_BARRACKS, B.GREAT_STABLES)
    for bid in (B.BARRACKS, B.STABLES, B.WORKSHOP, B.RESIDENCE):
        lvl = levels.get(bid, 0)
        if lvl < 1:
            continue
        b = BLD.get(bid)
        factor = b.benefit(lvl) if bid in TRAIN_BONUS_BUILDINGS else 1.0
        military.append({
            "building_id": bid, "building": b.name, "level": lvl,
            "units": [{"index": i, "name": u.name, "cost": list(u.cost),
                       "time": round(u.train_time * factor / v.server_speed),
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

    # Héros : présent dans ce village (rattaché ici et disponible) → l'UI propose
    # de l'envoyer avec l'armée. On indique aussi son état succinct.
    hero_here = None
    h = HERO.load(v.player_id) if v.player_id is not None else None
    if h is not None and h.home_village_id == v.id:
        hero_here = {"name": h.name, "level": h.level,
                     "health": round(h.health), "status": h.status,
                     "available": h.status == "home" and h.health > 0}

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
        "movements": moves, "market": market, "hero_here": hero_here,
        "oases": [{"x": o["x"], "y": o["y"], "label": W.oasis_label(o["code"]),
                   "emoji": W.oasis_emoji(o["code"])} for o in v.oases],
        "oasis_slots": {"used": len(v.oases), "max": OAS.max_oases(v)},
    }


def _get(village_id: int) -> V.Village:
    now = time.time()
    M.process_due(now)  # résout les mouvements arrivés (combats, retours, fondations)
    if HUMAN_PLAYER_ID is not None:
        _tick_player(HUMAN_PLAYER_ID, now)
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
    with_hero: bool = False            # embarquer le héros (attaque/razzia)


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
    by_id = {v["id"]: v for v in villages.values()}
    tiles = []
    for t in store.tiles_in_box(cx - r, cx + r, cy - r, cy + r):
        cell = {"x": t["x"], "y": t["y"], "kind": t["kind"]}
        v = villages.get((t["x"], t["y"]))
        if v is not None:
            cell["kind"] = "village"
            cell["village"] = {"id": v["id"], "name": v["name"], "player": v["player"],
                               "is_own": v["is_own"], "is_capital": bool(v["is_capital"])}
        elif t["kind"] == "oasis":
            owner = by_id.get(t.get("owner_id"))
            cell["oasis"] = {"label": W.oasis_label(t["layout"]),
                             "emoji": W.oasis_emoji(t["layout"]),
                             "animals": W.animal_count(t["animals"]),
                             "owned": owner is not None,
                             "is_own_oasis": bool(owner and owner["is_own"])}
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
        owner = None
        if t.get("owner_id") is not None:
            ov = store.load_village(t["owner_id"])
            if ov is not None:
                owner = {"id": ov.id, "name": ov.name,
                         "is_own": ov.player_id == HUMAN_PLAYER_ID}
        out["oasis"] = {
            "label": W.oasis_label(t["layout"]),
            "emoji": W.oasis_emoji(t["layout"]),
            "bonus": [{"resource": res_names[i], "percent": p} for i, p in bonus.items()],
            "animals": W.animal_breakdown(t["animals"]),
            "total_animals": W.animal_count(t["animals"]),
            "owner": owner,
            "eligible_villages": (OAS.eligible_villages(HUMAN_PLAYER_ID, t)
                                  if owner is None else []),
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
                      target_x=body.target_x, target_y=body.target_y,
                      with_hero=body.with_hero)
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


# --- Héros, aventures, objets ------------------------------------------------
def _hero_payload(h: HERO.Hero, now: float) -> dict:
    eff = HERO.effective(h)
    home = store.load_village(h.home_village_id)
    res_names = ["bois", "argile", "fer", "céréales", "réparti"]
    equipment = {slot: IT.item_dict(key) for slot, key in h.equipment.items()}
    bag = [{**IT.item_dict(k), "qty": n} for k, n in h.bag.items() if IT.item_dict(k)]
    advs = [{"id": a["id"], "x": a["x"], "y": a["y"], "difficulty": a["difficulty"]}
            for a in store.adventures_for(h.player_id)]
    return {
        "name": h.name, "level": h.level, "experience": round(h.experience),
        "xp_next": round(HERO.xp_threshold(h.level + 1)),
        "xp_this": round(HERO.xp_threshold(h.level)),
        "health": round(h.health, 1), "status": h.status,
        "busy_in": max(0, round(h.busy_until - now)) if h.busy_until else 0,
        "points": h.points,
        "attrs": {"fight": h.fight, "off": h.off_points, "def": h.def_points,
                  "res": h.res_points},
        "res_choice": h.res_choice, "res_choice_label": res_names[h.res_choice if h.res_choice >= 0 else 4],
        "effective": {"strength": round(eff["strength"]),
                      "off_bonus": round(eff["off_bonus"] * 100, 1),
                      "def_bonus": round(eff["def_bonus"] * 100, 1),
                      "regen_per_day": round(eff["regen_per_day"], 1),
                      "production_per_hour": round(eff["production_per_hour"], 1),
                      "speed": round(eff["speed"], 1)},
        "home_village_id": h.home_village_id,
        "home_village": home.name if home else "?",
        "equipment": equipment, "bag": bag, "adventures": advs,
        "slots": IT.SLOT_LABELS,
        "revive_cost": list(HERO.REVIVE_COST),
    }


@app.get("/api/hero")
def hero_state():
    now = time.time()
    M.process_due(now)
    _tick_player(HUMAN_PLAYER_ID, now)
    h = HERO.load(HUMAN_PLAYER_ID)
    if h is None:
        raise HTTPException(status_code=404, detail="Pas de héros.")
    return _hero_payload(h, now)


def _hero_action(fn) -> dict:
    """Exécute une action héros, persiste, renvoie l'état à jour."""
    now = time.time()
    M.process_due(now)
    _tick_player(HUMAN_PLAYER_ID, now)
    h = HERO.load(HUMAN_PLAYER_ID)
    if h is None:
        raise HTTPException(status_code=404, detail="Pas de héros.")
    try:
        fn(h)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    HERO.save(h)
    return {"ok": True, "hero": _hero_payload(HERO.load(HUMAN_PLAYER_ID), now)}


@app.post("/api/hero/allocate/{attr}/{amount}")
def hero_allocate(attr: str, amount: int):
    mapping = {"fight": "fight", "off": "off_points", "def": "def_points",
               "res": "res_points"}
    if attr not in mapping:
        raise HTTPException(status_code=400, detail="Attribut inconnu.")
    return _hero_action(lambda h: HERO.allocate(h, mapping[attr], amount))


@app.post("/api/hero/res_choice/{choice}")
def hero_res_choice(choice: int):
    return _hero_action(lambda h: HERO.set_res_choice(h, choice))


@app.post("/api/hero/equip/{key}")
def hero_equip(key: str):
    return _hero_action(lambda h: HERO.equip(h, key))


@app.post("/api/hero/unequip/{slot}")
def hero_unequip(slot: str):
    return _hero_action(lambda h: HERO.unequip(h, slot))


@app.post("/api/hero/use/{key}")
def hero_use(key: str):
    return _hero_action(lambda h: HERO.use_consumable(h, key))


@app.post("/api/hero/adventure/{adventure_id}")
def hero_adventure(adventure_id: int):
    now = time.time()
    M.process_due(now)
    _tick_player(HUMAN_PLAYER_ID, now)
    try:
        info = HERO.send_to_adventure(HUMAN_PLAYER_ID, adventure_id, now)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"],
            "hero": _hero_payload(HERO.load(HUMAN_PLAYER_ID), now)}


@app.post("/api/hero/revive")
def hero_revive():
    now = time.time()
    M.process_due(now)
    _tick_player(HUMAN_PLAYER_ID, now)
    try:
        info = HERO.revive(HUMAN_PLAYER_ID, now)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "revive_in": info["revive_in"],
            "hero": _hero_payload(HERO.load(HUMAN_PLAYER_ID), now)}


# --- Expansion (colons / nouveau village) ------------------------------------
@app.get("/api/expansion")
def expansion_state():
    now = time.time()
    M.process_due(now)
    return EXP.expansion_status(HUMAN_PLAYER_ID, now)


class Settle(BaseModel):
    x: int
    y: int


@app.post("/api/village/{village_id}/settle")
def settle(village_id: int, body: Settle):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        info = EXP.send_settlers(village_id, body.x, body.y, HUMAN_PLAYER_ID)
    except EXP.ExpansionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "village": serialize(_get(village_id))}


# --- Occupation d'oasis (manoir du héros) ------------------------------------
class OasisTarget(BaseModel):
    x: int
    y: int


@app.post("/api/village/{village_id}/oasis/occupy")
def occupy_oasis(village_id: int, body: OasisTarget):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        info = OAS.occupy(village_id, body.x, body.y, HUMAN_PLAYER_ID, time.time())
    except OAS.OasisError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "oasis": {"x": info["x"], "y": info["y"], "label": info["label"]},
            "village": serialize(_get(village_id))}


@app.post("/api/village/{village_id}/oasis/abandon")
def abandon_oasis(village_id: int, body: OasisTarget):
    v = _get(village_id)
    if v.player_id != HUMAN_PLAYER_ID:
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        OAS.abandon(village_id, body.x, body.y, HUMAN_PLAYER_ID, time.time())
    except OAS.OasisError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(_get(village_id))}


@app.get("/api/reports")
def reports():
    now = time.time()
    M.process_due(now)
    if HUMAN_PLAYER_ID is not None:
        _tick_player(HUMAN_PLAYER_ID, now)
    return {"reports": store.reports_for(HUMAN_PLAYER_ID)}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# Sert les assets statiques (images de bâtiments, etc.) sous /img.
app.mount("/img", StaticFiles(directory=WEB / "img"), name="img")
