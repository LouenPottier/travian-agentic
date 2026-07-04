"""API FastAPI + UI — Phase 1/2 : villages persistés (SQLite), plusieurs joueurs.

Lancer : ./venv/bin/uvicorn app.main:app --reload
Puis ouvrir http://127.0.0.1:8000/
"""
from __future__ import annotations

import contextvars
import json
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
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
from app.engine import celebration as CEL
from app.engine import brewery as BRW
from app.engine import farmlist as FARM
from app.engine import capital as CAP
from app.engine import natars as NAT
from app.engine import artifacts as ART
from app.engine import ranking as RK
from app.data import items as IT
from app.engine import situation as SIT
from app.agents import macro as MACRO
from app.agents import defender as DEF

# --- Joueur agissant (Phase 4 : identité par requête) -------------------------
# Toute la surface joueur était épinglée au global `HUMAN_PLAYER_ID`. Pour qu'un
# agent LLM puisse jouer un AUTRE compte via la MÊME surface enforced, on résout un
# « joueur agissant » par requête : posé depuis l'en-tête `X-Acting-Player` (aucune
# auth de toute façon, appels en loopback), sinon repli sur `HUMAN_PLAYER_ID` ⇒ le
# comportement du navigateur humain est inchangé (pas d'en-tête). L'ownership check
# `v.player_id != acting_player()` reste imposé : l'agent ne peut agir que sur SES
# villages, exactement comme un humain (« sans tricher »).
_ACTING_PLAYER: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "acting_player", default=None)


def acting_player() -> int | None:
    """Joueur agissant pour la requête courante (repli sur le joueur humain)."""
    pid = _ACTING_PLAYER.get()
    return pid if pid is not None else HUMAN_PLAYER_ID


async def _bind_acting_player(x_acting_player: int | None = Header(default=None)):
    """Dependency globale : pose le ContextVar depuis l'en-tête, le remet après.
    Une dependency (et non un BaseHTTPMiddleware) garantit que le ContextVar posé se
    propage bien à l'endpoint (même task async)."""
    token = _ACTING_PLAYER.set(x_acting_player) if x_acting_player is not None else None
    try:
        yield
    finally:
        if token is not None:
            _ACTING_PLAYER.reset(token)


app = FastAPI(title="Travian local — T4.6", dependencies=[Depends(_bind_acting_player)])

SERVER_SPEED = 100  # temps ×100 pour faciliter les tests
WEB = Path(__file__).resolve().parent.parent / "web"

HUMAN_PLAYER_ID: int | None = None

# Bâtiments visables par les catapultes (siège) — bâtiments stratégiques du centre.
# La muraille en est exclue (elle est l'affaire des béliers, automatique).
CATA_TARGET_BUILDINGS = (
    B.MAIN_BUILDING, B.RALLY_POINT, B.WAREHOUSE, B.GRANARY, B.MARKETPLACE,
    B.RESIDENCE, B.PALACE, B.BARRACKS, B.STABLES, B.WORKSHOP, B.ACADEMY,
    B.SMITHY, B.TOWNHALL, B.TREASURY, B.EMBASSY, B.CRANNY, B.HERO_MANSION,
)


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


def _ensure_natars() -> None:
    """Crée le joueur PNJ Natars + ses villages s'ils n'existent pas encore. Idempotent
    (migration douce : ajoute les Natars aux parties existantes sans toucher au reste)."""
    if store.find_player_by_name("Natars") is not None:
        return
    natar_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    NAT.spawn_natar_villages(natar_pid, SERVER_SPEED)


def _ensure_artifacts() -> None:
    """Crée les villages Natars détenteurs d'artefacts + les artefacts s'ils manquent.
    Idempotent (migration douce : ajoute les artefacts aux parties déjà créées)."""
    if store.artifacts_exist():
        return
    natar_pid = store.find_player_by_name("Natars")
    if natar_pid is None:
        return
    ART.spawn_artifact_villages(natar_pid, SERVER_SPEED)


# Phase 4 : un vrai compte joué par un agent LLM (posture défensive). Gaulois = meilleure
# défense + trappeur (pièges). Placé près du joueur humain pour qu'on puisse l'attaquer et
# observer sa défense. `players.agent=1` le distingue des PNJ passifs (Natars/Voisin).
AGENT_PLAYER_NAME = "Défenseur IA"


def _ensure_agent_player() -> None:
    """Crée le joueur IA défensif + son village près du joueur humain s'il manque.
    Idempotent (migration douce : l'ajoute aux mondes déjà créés)."""
    if store.find_player_by_name(AGENT_PLAYER_NAME) is not None:
        return
    if HUMAN_PLAYER_ID is None:
        return
    hvids = store.player_villages(HUMAN_PLAYER_ID)
    if not hvids:
        return
    caps = [store.load_village(v) for v in hvids]
    anchor = next((c for c in caps if c.is_capital), caps[0])
    occupied = {(vv["x"], vv["y"]) for vv in store.list_villages()}
    ax, ay = _find_free_valley(anchor.x + 4, anchor.y + 3, occupied)
    pid = store.create_player(AGENT_PLAYER_NAME, Tribe.GAULS, agent=True)
    store.insert_village(V.new_village(
        "Village IA", Tribe.GAULS, server_speed=SERVER_SPEED,
        x=ax, y=ay, player_id=pid, is_capital=True))
    HERO.get_or_create(pid, store.player_villages(pid)[0])


# Position de départ du joueur humain : **loin du centre**, car la zone centrale
# (anneau `NATAR_ZONE_INNER..OUTER`) est occupée par les Natars. On reste bien à
# l'intérieur du monde (rayon 100) avec de la marge pour s'étendre.
HUMAN_START = (60, 60)


def _relocate_human_start(player_id: int) -> None:
    """Migration douce des mondes créés avant la zone Natar centrale : si la capitale
    humaine est encore dans/au bord de la zone Natar (centre), on la **déplace loin**
    (ses données développées suivent le déplacement). Tout **village secondaire resté au
    centre** est ensuite **regroupé près de la capitale** (et non laissé en zone Natar) ;
    si le joueur n'a qu'un village, on lui en adjoint un proche. Le voisin teuton est
    rapproché. Idempotent : ne re-déplace pas un village déjà éloigné du centre."""
    vids = store.player_villages(player_id)
    if not vids:
        return
    caps = [store.load_village(v) for v in vids]
    cap = next((c for c in caps if c.is_capital), caps[0])
    occupied = {(v["x"], v["y"]) for v in store.list_villages()}
    if max(abs(cap.x), abs(cap.y)) <= NAT.NATAR_ZONE_OUTER:   # capitale encore au centre
        occupied.discard((cap.x, cap.y))
        hx, hy = _find_free_valley(*HUMAN_START, occupied)
        store.move_village(cap.id, hx, hy)
        occupied.add((hx, hy))
    else:
        hx, hy = cap.x, cap.y
    # Villages secondaires encore au centre ⇒ les rapprocher de la capitale (pas du centre).
    for sec in caps:
        if sec.id == cap.id or max(abs(sec.x), abs(sec.y)) > NAT.NATAR_ZONE_OUTER:
            continue
        occupied.discard((sec.x, sec.y))
        sx, sy = _find_free_valley(hx + 2, hy + 1, occupied)
        store.move_village(sec.id, sx, sy)
        occupied.add((sx, sy))
    if len(vids) < 2:                                          # aucun 2ᵉ village ⇒ en créer un proche
        sx, sy = _find_free_valley(hx + 2, hy + 1, occupied)
        occupied.add((sx, sy))
        store.insert_village(V.new_village(
            "Mon 2e village", cap.tribe, server_speed=SERVER_SPEED,
            x=sx, y=sy, player_id=player_id, is_capital=False))
    npc_id = store.find_player_by_name("Voisin")              # rapprocher le voisin
    if npc_id is not None:
        for nv in store.player_villages(npc_id):
            tv = store.load_village(nv)
            if max(abs(tv.x), abs(tv.y)) <= NAT.NATAR_ZONE_OUTER:
                occupied.discard((tv.x, tv.y))
                nx, ny = _find_free_valley(hx - 3, hy - 2, occupied)
                store.move_village(tv.id, nx, ny)
                occupied.add((nx, ny))


def seed_world() -> None:
    """Crée le monde de départ si la base est vide : la carte, toi (loin du centre) +
    un 2ᵉ village proche + un voisin NPC + les Natars (au centre). Agrandissement de
    carte **non destructif** : la (ré)insertion des cases est idempotente (INSERT OR
    IGNORE + terrain déterministe), donc bumper `WORLD_RADIUS` ajoute seulement les
    nouvelles couronnes sans effacer `game.db`."""
    global HUMAN_PLAYER_ID
    store.init_db()
    # Première création OU agrandissement du rayon (la case du bord n'existe pas encore).
    if store.world_is_empty() or store.get_tile(W.WORLD_RADIUS, 0) is None:
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
        if HUMAN_PLAYER_ID is not None:
            _relocate_human_start(HUMAN_PLAYER_ID)  # éloigne du centre Natar + 2ᵉ village
        _ensure_natars()  # migration : ajoute les Natars aux mondes déjà créés
        _ensure_artifacts()  # migration : ajoute les artefacts (villages Natars dédiés)
        _ensure_agent_player()  # migration : ajoute le joueur IA défensif
        return
    HUMAN_PLAYER_ID = store.create_player("Toi", Tribe.GAULS)
    occupied: set[tuple[int, int]] = set()
    hx, hy = _find_free_valley(*HUMAN_START, occupied)         # capitale loin du centre
    occupied.add((hx, hy))
    store.insert_village(V.new_village(
        "Mon village", Tribe.GAULS, server_speed=SERVER_SPEED,
        x=hx, y=hy, player_id=HUMAN_PLAYER_ID, is_capital=True))
    sx, sy = _find_free_valley(hx + 2, hy + 1, occupied)       # 2ᵉ village proche
    occupied.add((sx, sy))
    store.insert_village(V.new_village(
        "Mon 2e village", Tribe.GAULS, server_speed=SERVER_SPEED,
        x=sx, y=sy, player_id=HUMAN_PLAYER_ID, is_capital=False))
    npc = store.create_player("Voisin", Tribe.TEUTONS, is_npc=True)
    nx, ny = _find_free_valley(hx - 3, hy - 2, occupied)       # voisin proche de toi
    occupied.add((nx, ny))
    store.insert_village(V.new_village(
        "Camp teuton", Tribe.TEUTONS, server_speed=SERVER_SPEED,
        x=nx, y=ny, player_id=npc, is_capital=True))
    # Héros du joueur, rattaché à sa capitale.
    cap = store.player_villages(HUMAN_PLAYER_ID)[0]
    HERO.get_or_create(HUMAN_PLAYER_ID, cap)
    _ensure_natars()
    _ensure_artifacts()
    _ensure_agent_player()


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


def _account_has_palace(player_id: int, exclude_village_id: int | None = None) -> bool:
    """True si le joueur possède un palais (niv ≥ 1) dans un autre village.

    Sert à appliquer « un seul palais par compte » (vrai Travian)."""
    if player_id is None:
        return False
    for vid in store.player_villages(player_id):
        if vid == exclude_village_id:
            continue
        vv = store.load_village(vid)
        if vv and V.building_levels(vv).get(B.PALACE, 0) >= 1:
            return True
    return False


def serialize(v: V.Village) -> dict:
    V.tick(v)
    store.save_village(v)  # persiste l'état avancé (ressources, file)
    account_has_palace = _account_has_palace(v.player_id, v.id)
    prod = V.net_production(v)
    caps = V.capacities(v)
    now = time.time()
    # File de planification : nb d'ordres en attente par emplacement + niveau projeté
    # (courant + file active + planifiée), pour l'affichage « en file » de chaque tuile.
    planned_by_slot: dict[int, int] = {}
    for p in v.build_plan:
        planned_by_slot[p.slot_index] = planned_by_slot.get(p.slot_index, 0) + 1
    slots = []
    for idx in range(1, 41):
        s = v.slots.get(idx)
        if s is not None:
            b = BLD.get(s.building_id)
            order = next((o for o in v.queue if o.slot_index == idx), None)
            dem = v.demolition if (v.demolition and v.demolition.slot_index == idx) else None
            mx = V.effective_max_level(v, b)  # champs hors capitale plafonnés à 10
            projected = V._projected_slot_level(v, idx)
            slots.append({
                "index": idx, "empty": False,
                "building_id": s.building_id, "name": b.name,
                "level": s.level, "max_level": mx, "slot_type": b.slot,
                "next_cost": b.cost_at(s.level + 1) if s.level < mx else None,
                "next_time": round(V.build_time(v, b, s.level + 1)) if s.level < mx else None,
                "finish_in": round(order.finish_at - now) if order else None,
                "target_level": order.target_level if order else None,
                # File de planification : ordres en attente sur cet emplacement + niveau
                # projeté une fois toute la file réalisée (bornage du bouton « +file »).
                "planned": planned_by_slot.get(idx, 0),
                "projected_level": projected, "can_queue": projected < mx,
                # Démolition (bâtiment principal niv 10+) : possibilité + démolition en cours.
                # False si une démolition tourne déjà (une seule à la fois) ou si l'emplacement
                # est en construction.
                "can_demolish": (V.can_demolish(v) and V.is_demolishable_slot(v, idx)
                                 and order is None and v.demolition is None),
                "demolish_finish_in": round(dem.finish_at - now) if dem else None,
                "demolish_target": dem.target_level if dem else None,
                "effect": EFF.building_effect(v, s.building_id, s.level),
                "next_effect": (EFF.building_effect(v, s.building_id, s.level + 1)
                                if s.level < mx else None),
            })
        else:
            # Emplacement vide : peut être « planifié » (pose en attente, Slot non encore
            # créé) → on le signale pour ne pas reproposer une pose.
            planned = next((p for p in v.build_plan if p.slot_index == idx), None)
            buildable = [{"id": b.id, "name": b.name, "cost": b.cost_at(1),
                          "time": round(V.build_time(v, b, 1))}
                         for b in V.available_buildings(v, idx, account_has_palace)]
            slots.append({"index": idx, "empty": True,
                          "slot_type": _slot_type(idx), "buildable": buildable,
                          "planned": (BLD.get(planned.building_id).name
                                      if planned else None)})

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
    # Caserne/écurie/atelier (+ grandes variantes) → troupes ; résidence/palais →
    # colons/chefs (niveau 10+). La réduction de temps (train_time_factor) ne
    # s'applique qu'aux casernes/écuries/ateliers (résidence/palais : facteur 1,0).
    for bid in (B.BARRACKS, B.STABLES, B.WORKSHOP, B.RESIDENCE, B.PALACE,
                B.GREAT_BARRACKS, B.GREAT_STABLES):
        lvl = levels.get(bid, 0)
        if lvl < 1:
            continue
        b = BLD.get(bid)
        factor = V.train_time_factor(bid, lvl)
        # Grande caserne / grande écurie : mêmes unités, coût ×3 (cf. village.py).
        mult = V.GREAT_COST_MULT if bid in V.GREAT_TRAINERS else 1
        military.append({
            "building_id": bid, "building": b.name, "level": lvl,
            "units": [{"index": i, "name": u.name,
                       "cost": [c * mult for c in u.cost],
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

    # Siège : si ce village abrite des catapultes, l'UI propose de viser des
    # bâtiments lors d'une **attaque** de village. La muraille (béliers) n'est pas
    # listée. Le nombre de cibles distinctes dépend de l'atelier (≥ niv 20 ⇒ 2).
    siege = None
    if any(c > 0 for i, c in enumerate(v.troops) if units[i].is_catapult):
        siege = {"limit": M.catapult_target_limit(v),
                 "targets": [{"id": bid, "name": BLD.get(bid).name}
                             for bid in CATA_TARGET_BUILDINGS]}

    # Hôtel de ville : célébration en cours (pour l'indicateur d'en-tête / l'API agents).
    celebration = None
    if CEL.is_active(v, now):
        celebration = {"type": v.celebration["type"],
                       "remaining": round(v.celebration["ends_at"] - now),
                       "cp": v.celebration["cp"]}

    # Brasserie (Teutons, capitale) : fête de la bière en cours + bonus d'attaque.
    brewery = None
    if V.building_levels(v).get(B.BREWERY, 0) > 0:
        brewery = BRW.brewery_status(v, now)

    # Trésorerie : niveau + emplacements de trésor + artefact stocké (endgame Natars).
    treasury = None
    if V.building_levels(v).get(B.TREASURY, 0) > 0:
        treasury = ART.treasury_status(v)

    # Place de marché : niveau, marchands (total / libres), capacité par marchand.
    market = None
    if M.merchants_total(v) > 0:
        market = {"level": M.merchants_total(v),
                  "merchants_total": M.merchants_total(v),
                  "merchants_free": M.merchants_available(v),
                  "capacity": M.merchant_capacity(v)}

    return {
        "id": v.id, "name": v.name, "tribe": TRIBE_NAMES_FR[v.tribe],
        "x": v.x, "y": v.y, "is_own": v.player_id == acting_player(),
        "server_speed": v.server_speed,
        "is_capital": v.is_capital,
        # Palais niv ≥ 1 dans CE village ⇒ on peut le déclarer capitale (s'il ne l'est pas).
        "can_make_capital": (not v.is_capital
                             and V.building_levels(v).get(B.PALACE, 0) >= 1),
        "resources": [round(r) for r in v.resources], "capacities": caps,
        "production": [round(p, 1) for p in prod], "population": V.population(v),
        "troop_upkeep": V.troop_upkeep(v),
        "loyalty": round(v.loyalty),
        "queue_len": len(v.queue), "max_queue": v.max_queue, "slots": slots,
        # File de planification (arbitrairement longue) : ordres en attente, dans l'ordre
        # de lancement, annulables tant qu'ils n'ont pas démarré (bouton ✕ → /build/cancel).
        "build_plan": [{"pos": i, "slot": p.slot_index,
                        "name": BLD.get(p.building_id).name,
                        "target_level": p.target_level,
                        "cost": BLD.get(p.building_id).cost_at(p.target_level),
                        # Durée propre de construction de cet ordre (réduction bâtiment
                        # principal + vitesse serveur incluses) : l'UI enchaîne ces durées
                        # pour afficher l'ETA de démarrage sous l'hypothèse « sans attente ».
                        "time": round(V.build_time(v, BLD.get(p.building_id),
                                                    p.target_level))}
                       for i, p in enumerate(v.build_plan)],
        "troops": troops, "training": training, "military": military,
        "movements": moves, "market": market, "hero_here": hero_here, "siege": siege,
        "celebration": celebration, "brewery": brewery, "treasury": treasury,
        "oases": [{"x": o["x"], "y": o["y"], "label": W.oasis_label(o["code"]),
                   "emoji": W.oasis_emoji(o["code"])} for o in v.oases],
        "oasis_slots": {"used": len(v.oases), "max": OAS.max_oases(v)},
    }


def _get(village_id: int) -> V.Village:
    now = time.time()
    M.process_due(now)  # résout les mouvements arrivés (combats, retours, fondations)
    if acting_player() is not None:
        _tick_player(acting_player(), now)
    v = store.load_village(village_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Village introuvable.")
    return v


class SendArmy(BaseModel):
    kind: str  # attack | raid | reinforce | scout
    units: list[int]
    target_id: int | None = None       # cible village
    target_x: int | None = None        # cible oasis (coordonnées)
    target_y: int | None = None
    with_hero: bool = False            # embarquer le héros (attaque/razzia)
    targets: list[int] | None = None   # siège : ids de bâtiments visés (catapultes)
    scout_mode: str | None = None      # espionnage : "res" (ressources) | "def" (défenses)


@app.get("/api/villages")
def villages():
    rows = store.list_villages()
    for r in rows:
        r["is_own"] = r["player_id"] == acting_player()
    return {"villages": rows, "human_player_id": acting_player()}


def _villages_by_xy() -> dict[tuple[int, int], dict]:
    out = {}
    for r in store.list_villages():
        r["is_own"] = r["player_id"] == acting_player()
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
                               "is_own": v["is_own"], "is_capital": bool(v["is_capital"]),
                               "is_natar": v["tribe"] == int(Tribe.NATARS)}
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
        pop = 0
        vv = store.load_village(v["id"])
        if vv is not None:
            pop = V.population(vv)
        out["village"] = {"id": v["id"], "name": v["name"], "player": v["player"],
                          "player_id": v["player_id"], "population": pop,
                          "is_own": v["is_own"], "is_capital": bool(v["is_capital"]),
                          "is_natar": v["tribe"] == int(Tribe.NATARS)}
    elif t["kind"] == "oasis":
        bonus = W.oasis_bonus(t["layout"])
        res_names = ["bois", "argile", "fer", "céréales"]
        owner = None
        if t.get("owner_id") is not None:
            ov = store.load_village(t["owner_id"])
            if ov is not None:
                is_own = ov.player_id == acting_player()
                owner = {"id": ov.id, "name": ov.name, "is_own": is_own}
                if is_own:  # garnison postée (visible seulement au propriétaire)
                    g = OAS.oasis_garrison(ov, x, y)
                    owner["garrison"] = [{"name": UNITS[ov.tribe][i].name, "count": g[i]}
                                         for i in range(10) if g[i] > 0]
        out["oasis"] = {
            "label": W.oasis_label(t["layout"]),
            "emoji": W.oasis_emoji(t["layout"]),
            "bonus": [{"resource": res_names[i], "percent": p} for i, p in bonus.items()],
            "animals": W.animal_breakdown(t["animals"]),
            "total_animals": W.animal_count(t["animals"]),
            "owner": owner,
            "eligible_villages": (OAS.eligible_villages(acting_player(), t)
                                  if owner is None else []),
        }
    else:
        w, c, i, cr = (int(n) for n in t["layout"].split("-"))
        out["valley"] = {"layout": t["layout"], "fields": {"bois": w, "argile": c,
                                                           "fer": i, "céréales": cr}}
    return out


@app.get("/api/player/{player_id}")
def player_profile(player_id: int):
    """Profil public d'un joueur : nom, peuple et liste de ses villages (nom,
    coordonnées, population). Sert au clic sur un nom de joueur (classement / carte)."""
    p = store.get_player(player_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Joueur inconnu.")
    villages = []
    for vid in store.player_villages(player_id):
        vv = store.load_village(vid)
        if vv is None:
            continue
        villages.append({"id": vv.id, "name": vv.name, "x": vv.x, "y": vv.y,
                         "population": V.population(vv),
                         "is_capital": bool(vv.is_capital)})
    villages.sort(key=lambda r: r["population"], reverse=True)
    return {"id": p["id"], "name": p["name"],
            "tribe_name": TRIBE_NAMES_FR.get(Tribe(p["tribe"]), str(p["tribe"])),
            "is_npc": bool(p["is_npc"]),
            "is_own": player_id == acting_player(),
            "villages": villages}


@app.get("/api/village/{village_id}")
def get_village(village_id: int):
    return serialize(_get(village_id))


def _order_status(order) -> dict:
    """Résumé d'un ordre renvoyé par enqueue_build/enqueue_new_building : soit démarré
    (BuildOrder → `finish_in`), soit en attente dans la file (PlannedBuild → `queued`)."""
    if isinstance(order, V.BuildOrder):
        return {"started": True, "finish_in": round(order.finish_at - time.time())}
    return {"started": False, "finish_in": None, "queued": True}


@app.post("/api/village/{village_id}/build/{slot_index}")
def build(village_id: int, slot_index: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        order = V.enqueue_build(v, slot_index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **_order_status(order), "village": serialize(v)}


@app.post("/api/village/{village_id}/construct/{slot_index}/{building_id}")
def construct(village_id: int, slot_index: int, building_id: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        order = V.enqueue_new_building(
            v, slot_index, building_id,
            account_has_palace=_account_has_palace(v.player_id, v.id))
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **_order_status(order), "village": serialize(v)}


@app.post("/api/village/{village_id}/build/cancel/{pos}")
def cancel_build(village_id: int, pos: int):
    """Annule l'ordre en attente à la position `pos` de la file de planification
    (les constructions déjà démarrées ne sont pas annulables)."""
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.cancel_plan(v, pos)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.post("/api/village/{village_id}/demolish/{slot_index}")
def demolish(village_id: int, slot_index: int, target_level: int | None = None):
    """Démolit l'emplacement `slot_index` (bâtiment principal niv 10+). `target_level`
    optionnel : niveau visé (omis ⇒ un seul niveau ; 0 ⇒ destruction complète)."""
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        order = V.enqueue_demolish(v, slot_index, target_level)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "finish_in": round(order.finish_at - time.time()),
            "village": serialize(v)}


@app.post("/api/village/{village_id}/make-capital")
def make_capital(village_id: int):
    """Déclare ce village comme capitale (exige un palais niv ≥ 1)."""
    now = time.time()
    M.process_due(now)
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        info = CAP.make_capital(acting_player(), village_id, now)
    except CAP.CapitalError as e:
        raise HTTPException(status_code=400, detail=str(e))
    removed = info.get("removed_new_capital", []) + info.get("removed_old_capital", [])
    return {"ok": True, "reduced": info["reduced"],
            "removed": [BLD.get(bid).name for bid in removed],
            "village": serialize(_get(village_id))}


@app.post("/api/village/{village_id}/train/{building_id}/{unit_index}/{count}")
def train(village_id: int, building_id: int, unit_index: int, count: int):
    v = _get(village_id)
    if v.player_id != acting_player():
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
        missing = V.unmet_requirements(v, i)
        items.append({"index": i, "name": u.name, "researched": bool(v.research[i]),
                      "cost": list(V.research_cost(v, i)),
                      "time": round(V.research_time(v, i)),
                      "in_queue": round(in_queue.finish_at - now) if in_queue else None,
                      "locked": bool(missing),
                      "requires": [{"building": BLD.get(b).name, "level": lvl}
                                   for b, lvl in missing]})
    return {"level": level, "units": items}


@app.post("/api/village/{village_id}/research/{unit_index}")
def research(village_id: int, unit_index: int):
    v = _get(village_id)
    if v.player_id != acting_player():
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
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_upgrade(v, unit_index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.get("/api/village/{village_id}/celebration")
def celebration_state(village_id: int):
    """Hôtel de ville : célébration en cours + options (petite/grande fête)."""
    v = _get(village_id)
    if V.building_levels(v).get(B.TOWNHALL, 0) < 1:
        raise HTTPException(status_code=400, detail="Pas d'hôtel de ville dans ce village.")
    now = time.time()
    if v.player_id is not None:
        EXP.accumulate_culture(v.player_id, now)  # récolte les fêtes terminées
        v = store.load_village(village_id)
    return CEL.celebration_status(v, now)


@app.post("/api/village/{village_id}/celebration/{ctype}")
def start_celebration(village_id: int, ctype: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        CEL.start_celebration(village_id, acting_player(), ctype, time.time())
    except CEL.CelebrationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(store.load_village(village_id))}


@app.get("/api/village/{village_id}/brewery")
def brewery_state(village_id: int):
    """Brasserie (Teutons) : niveau, fête de la bière en cours, bonus d'attaque."""
    v = _get(village_id)
    if V.building_levels(v).get(B.BREWERY, 0) < 1:
        raise HTTPException(status_code=400, detail="Pas de brasserie dans ce village.")
    return BRW.brewery_status(v, time.time())


@app.post("/api/village/{village_id}/brewery/festival")
def start_brewery_festival(village_id: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        BRW.start_festival(village_id, acting_player(), time.time())
    except BRW.BreweryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(store.load_village(village_id))}


def _prisoner_view(p: dict) -> dict:
    """Vue lisible d'un groupe de prisonniers (noms d'unités + village d'origine)."""
    units = UNITS[p["tribe"]]
    items = [{"name": units[i].name, "count": p["units"][i]}
             for i in range(10) if p["units"][i] > 0]
    owner = store.load_village(p["village_id"])
    return {"village_id": p["village_id"], "owner": owner.name if owner else "?",
            "total": sum(p["units"]), "units": items}


@app.get("/api/village/{village_id}/trapper")
def trapper(village_id: int):
    """Trappeur : capacité de pièges, pièges construits / en cours, prisonniers retenus."""
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
            "trap_time": round(V.TRAP_TIME / v.server_speed),
            "held": V.prisoners_count(v),
            "prisoners": [_prisoner_view(p) for p in v.prisoners]}


@app.post("/api/village/{village_id}/prisoners/{index}/release")
def release_prisoners(village_id: int, index: int):
    """Libère un groupe de prisonniers : retour immédiat à leur village d'origine
    (approximation : le vrai Travian les renvoie en trajet)."""
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        p = V.release_prisoner(v, index)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store.save_village(v)
    owner = store.load_village(p["village_id"])
    if owner is not None:
        now = time.time()
        V.tick(owner, now)
        for i in range(10):
            owner.troops[i] += p["units"][i]
        store.save_village(owner)
        store.add_report(owner.player_id, now, "🕊️ Prisonniers libérés",
                         {"type": "release", "de": v.name, "unites": p["units"]})
    return {"ok": True, "village": serialize(v)}


@app.post("/api/village/{village_id}/traps/{count}")
def build_traps(village_id: int, count: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        V.enqueue_traps(v, count)
    except V.BuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(v)}


@app.post("/api/village/{village_id}/send")
def send_army(village_id: int, body: SendArmy):
    if body.kind not in ("attack", "raid", "reinforce", "scout"):
        raise HTTPException(status_code=400, detail="Type d'ordre invalide.")
    if body.target_id is None and (body.target_x is None or body.target_y is None):
        raise HTTPException(status_code=400, detail="Cible manquante.")
    units = (body.units + [0] * 10)[:10]
    try:
        info = M.send(village_id, body.target_id, acting_player(), body.kind, units,
                      target_x=body.target_x, target_y=body.target_y,
                      with_hero=body.with_hero, targets=body.targets,
                      scout_mode=body.scout_mode)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "village": serialize(_get(village_id))}


class SendResources(BaseModel):
    target_id: int
    amounts: list[int]  # [bois, argile, fer, céréales]


class TradeRoute(BaseModel):
    target_id: int
    amounts: list[int]          # [bois, argile, fer, céréales]
    interval_hours: float       # cadence (heures de temps de base)


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
                        "x": r["x"], "y": r["y"], "is_own": r["player_id"] == acting_player(),
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
        info = M.send_resources(village_id, body.target_id, acting_player(), amounts)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"], "merchants": info["merchants"],
            "village": serialize(_get(village_id))}


def _route_view(r: dict, now: float) -> dict:
    target = store.load_village(r["target_id"])
    return {"id": r["id"], "target_id": r["target_id"],
            "target": target.name if target else "?",
            "amounts": json.loads(r["amounts"]),
            "interval_hours": r["interval_hours"],
            "next_in": max(0, round(r["next_run"] - now))}


@app.get("/api/village/{village_id}/trade_routes")
def trade_routes(village_id: int):
    """Routes commerciales récurrentes partant de ce village."""
    v = _get(village_id)
    now = time.time()
    return {"routes": [_route_view(r, now) for r in store.trade_routes_for(v.id)]}


@app.post("/api/village/{village_id}/trade_route")
def create_trade_route(village_id: int, body: TradeRoute):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    amounts = (body.amounts + [0, 0, 0, 0])[:4]
    try:
        M.create_trade_route(village_id, body.target_id, acting_player(),
                             amounts, body.interval_hours)
    except M.MoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(_get(village_id))}


@app.delete("/api/village/{village_id}/trade_route/{route_id}")
def delete_trade_route(village_id: int, route_id: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    store.delete_trade_route(route_id, v.id)
    return {"ok": True, "village": serialize(v)}


# --- Liste de fermes (farm list) : razzias groupées -------------------------
class FarmTarget(BaseModel):
    units: list[int]
    target_id: int | None = None       # cible village
    target_x: int | None = None        # cible oasis (coordonnées)
    target_y: int | None = None


@app.get("/api/village/{village_id}/farmlist")
def farmlist(village_id: int):
    """Cibles de la liste de fermes de ce village (+ faisabilité immédiate)."""
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    return {"targets": FARM.list_targets(v.id, acting_player())}


@app.post("/api/village/{village_id}/farmlist")
def add_farm_target(village_id: int, body: FarmTarget):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    units = (body.units + [0] * 10)[:10]
    try:
        FARM.add_target(v.id, acting_player(), units, body.target_id,
                        body.target_x, body.target_y)
    except FARM.FarmError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "targets": FARM.list_targets(v.id, acting_player())}


@app.delete("/api/village/{village_id}/farmlist/{target_id}")
def remove_farm_target(village_id: int, target_id: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        FARM.remove_target(target_id, v.id, acting_player())
    except FARM.FarmError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "targets": FARM.list_targets(v.id, acting_player())}


@app.post("/api/village/{village_id}/farmlist/raid")
def raid_farmlist(village_id: int):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        res = FARM.raid_all(v.id, acting_player(), time.time())
    except FARM.FarmError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "result": res, "village": serialize(_get(village_id))}


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
    _tick_player(acting_player(), now)
    h = HERO.load(acting_player())
    if h is None:
        raise HTTPException(status_code=404, detail="Pas de héros.")
    return _hero_payload(h, now)


def _hero_action(fn) -> dict:
    """Exécute une action héros, persiste, renvoie l'état à jour."""
    now = time.time()
    M.process_due(now)
    _tick_player(acting_player(), now)
    h = HERO.load(acting_player())
    if h is None:
        raise HTTPException(status_code=404, detail="Pas de héros.")
    try:
        fn(h)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    HERO.save(h)
    return {"ok": True, "hero": _hero_payload(HERO.load(acting_player()), now)}


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
    _tick_player(acting_player(), now)
    try:
        info = HERO.send_to_adventure(acting_player(), adventure_id, now)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "arrive_in": info["arrive_in"],
            "hero": _hero_payload(HERO.load(acting_player()), now)}


@app.post("/api/hero/revive")
def hero_revive():
    now = time.time()
    M.process_due(now)
    _tick_player(acting_player(), now)
    try:
        info = HERO.revive(acting_player(), now)
    except HERO.HeroError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "revive_in": info["revive_in"],
            "hero": _hero_payload(HERO.load(acting_player()), now)}


# --- Expansion (colons / nouveau village) ------------------------------------
@app.get("/api/expansion")
def expansion_state():
    now = time.time()
    M.process_due(now)
    return EXP.expansion_status(acting_player(), now)


@app.get("/api/artifacts")
def artifacts_state():
    """Artefacts du joueur (capturés/actifs) + artefacts encore à conquérir (carte)."""
    now = time.time()
    M.process_due(now)
    owned = ART.owned_status(acting_player()) if acting_player() is not None else []
    return {"owned": owned, "map": ART.map_status(), "catalogue": ART.catalogue()}


class Settle(BaseModel):
    x: int
    y: int


@app.post("/api/village/{village_id}/settle")
def settle(village_id: int, body: Settle):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        info = EXP.send_settlers(village_id, body.x, body.y, acting_player())
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
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        info = OAS.occupy(village_id, body.x, body.y, acting_player(), time.time())
    except OAS.OasisError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "oasis": {"x": info["x"], "y": info["y"], "label": info["label"]},
            "village": serialize(_get(village_id))}


@app.post("/api/village/{village_id}/oasis/abandon")
def abandon_oasis(village_id: int, body: OasisTarget):
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    try:
        OAS.abandon(village_id, body.x, body.y, acting_player(), time.time())
    except OAS.OasisError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "village": serialize(_get(village_id))}


@app.get("/api/ranking")
def ranking():
    """Classement des joueurs par population, points d'attaque/défense, ressources
    pillées et nombre de villages (cf. engine.ranking)."""
    now = time.time()
    M.process_due(now)
    return RK.rankings(acting_player())


@app.get("/api/reports")
def reports():
    now = time.time()
    M.process_due(now)
    if acting_player() is not None:
        _tick_player(acting_player(), now)
    return {"reports": store.reports_for(acting_player())}


# --- Macros pilotées par Claude Code (Phase 4) -------------------------------
class MacroStart(BaseModel):
    goal: str
    model: str | None = None   # sonnet | opus | haiku (défaut sonnet)


@app.post("/api/village/{village_id}/macro")
async def macro_start(village_id: int, body: MacroStart):
    """Lance une macro (agent Claude Code) qui gère ce village vers `goal`.
    L'agent n'agit que via les actions joueur légitimes (aucune triche possible)."""
    v = _get(village_id)
    if v.player_id != acting_player():
        raise HTTPException(status_code=403, detail="Ce village ne t'appartient pas.")
    if not body.goal.strip():
        raise HTTPException(status_code=400, detail="Objectif vide.")
    try:
        return MACRO.start_macro(village_id, body.goal, body.model or MACRO.DEFAULT_MODEL,
                                 v.name, TRIBE_NAMES_FR.get(v.tribe, "?"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/village/{village_id}/macro")
def macro_state(village_id: int):
    """État + journal de la macro courante de ce village (pour le rafraîchissement UI)."""
    return MACRO.status_for(village_id)


@app.post("/api/village/{village_id}/macro/stop")
async def macro_stop(village_id: int):
    """Interrompt la macro en cours sur ce village."""
    return await MACRO.stop_macro(village_id)


# --- Agent joueur défenseur (Phase 4) ----------------------------------------
@app.get("/api/agent/situation")
def agent_situation():
    """Digest compact du compte agissant (défense) : menaces, rapports, villages terse.
    Bien plus léger que serialize() — c'est l'observation du défenseur LLM."""
    now = time.time()
    M.process_due(now)
    pid = acting_player()
    if pid is None:
        raise HTTPException(status_code=404, detail="Aucun joueur.")
    return SIT.build_digest(pid, now)


@app.get("/api/agent/players")
def agent_players():
    """Joueurs IA (players.agent=1) + leurs villages (chacun pilotable par un défenseur)."""
    out = []
    for p in store.agent_players():
        villages = [{"id": vid, "name": (vv := store.load_village(vid)).name,
                     "x": vv.x, "y": vv.y}
                    for vid in store.player_villages(p["id"])]
        out.append({"id": p["id"], "name": p["name"],
                    "tribe": TRIBE_NAMES_FR.get(Tribe(p["tribe"]), "?"),
                    "villages": villages})
    return {"players": out}


class DefenderStart(BaseModel):
    model: str | None = None   # sonnet | opus | haiku (défaut sonnet)


def _agent_village(village_id: int) -> V.Village:
    """Village d'un joueur IA (players.agent=1) — sinon 400/404 (les villages humains se
    pilotent via les macros, pas le défenseur)."""
    v = _get(village_id)
    p = store.get_player(v.player_id) if v.player_id is not None else None
    if not (p and p.get("agent")):
        raise HTTPException(status_code=400,
                            detail="Ce village n'appartient pas à un joueur IA.")
    return v


@app.post("/api/village/{village_id}/defender/wake")
def defender_wake(village_id: int, body: DefenderStart):
    """Fait jouer UN tour au défenseur de ce village : il observe puis (re)pose sa pile
    d'ordres permanents, et se rendort. L'exécuteur réalise ensuite la pile sans LLM."""
    v = _agent_village(village_id)
    return DEF.wake(village_id, v.player_id, body.model or DEF.DEFAULT_MODEL,
                    v.name, TRIBE_NAMES_FR.get(Tribe(v.tribe), "?"))


@app.post("/api/village/{village_id}/defender/unplug")
async def defender_unplug(village_id: int):
    """Débranche le LLM SANS toucher la pile : l'exécuteur continue de réaliser les ordres."""
    return await DEF.unplug(village_id)


@app.post("/api/village/{village_id}/defender/stop")
async def defender_stop(village_id: int):
    """Arrêt complet : débranche le LLM ET vide la pile d'ordres du village."""
    return await DEF.stop(village_id)


@app.get("/api/village/{village_id}/defender")
def defender_state(village_id: int):
    """État + journal du défenseur d'un village (rafraîchissement UI)."""
    return DEF.status_for(village_id)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# Sert les assets statiques (images de bâtiments, etc.) sous /img.
app.mount("/img", StaticFiles(directory=WEB / "img"), name="img")
