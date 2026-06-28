"""Expansion : points de culture, colons et fondation d'un nouveau village.

⚠️ **Source des chiffres** : kirilloid modélise les *points de culture par bâtiment*
(cf. `Building.culture_at`, déjà porté) mais PAS le **seuil cumulé** requis pour
fonder le n-ᵉ village. Le tableau `CULTURE_NEEDED` ci-dessous est une approximation
documentée reprise des tables communautaires de Travian T4.6 (croissance ≈ cubique) ;
au-delà du tableau on extrapole par la même formule.

Mécanique fidèle reproduite :
- Chaque village produit des **points de culture/jour** = somme des `culture_at` de
  ses bâtiments. Les points s'accumulent au niveau du **joueur** (production
  paresseuse : crédités au passage de la date, ×vitesse serveur).
- Pour fonder un village il faut **(a)** un *emplacement d'expansion* libre
  (résidence niv 10/20 → 1/2, palais niv 10/15/20 → 1/2/3, cumulés sur tous les
  villages) et **(b)** assez de points de culture pour le prochain village.
- On entraîne 3 **colons** (résidence/palais), on les envoie sur une **vallée libre**,
  et le village est fondé à l'arrivée (les colons sont consommés).
"""
from __future__ import annotations

import time as _time

from app import store
from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe
from app.engine import village as V
from app.engine import world as W

SETTLERS_NEEDED = 3

# Points de culture cumulés requis pour AVOIR n villages (n=1 = capitale, gratuite).
# Approximation documentée (tables communautaires T4.6).
CULTURE_NEEDED = {1: 0, 2: 200, 3: 500, 4: 1100, 5: 2000, 6: 3200, 7: 4800,
                  8: 6700, 9: 9000, 10: 11700}


class ExpansionError(Exception):
    pass


def culture_needed(n: int) -> int:
    """Points cumulés pour avoir `n` villages (extrapolation cubique au-delà)."""
    if n <= 1:
        return 0
    if n in CULTURE_NEEDED:
        return CULTURE_NEEDED[n]
    # Extrapolation : ~ même croissance que la fin du tableau connu.
    return int(round(2 * n ** 3 + 5 * n ** 2))


# --- Production / accumulation de points de culture --------------------------
def village_culture_per_day(v: V.Village) -> int:
    return sum(BLD.get(s.building_id).culture_at(s.level)
               for s in v.slots.values() if s.level > 0)


def player_culture_per_day(player_id: int) -> int:
    total = 0
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if v:
            total += village_culture_per_day(v)
    return total


def accumulate_culture(player_id: int, now: float | None = None) -> float:
    """Crédite les points de culture produits depuis la dernière lecture. Renvoie
    le total cumulé courant."""
    now = now or _time.time()
    culture, culture_at = store.get_culture(player_id)
    # Fêtes terminées (hôtel de ville) : crédit ponctuel de leurs points de culture,
    # indépendant de l'horloge d'accumulation (cf. engine.celebration).
    from app.engine import celebration as CEL
    culture += CEL.harvest_completed(player_id, now)
    if culture_at == 0:  # première lecture : on amorce l'horloge sans rétroactif
        store.set_culture(player_id, culture, now)
        return culture
    if now <= culture_at:
        store.set_culture(player_id, culture, culture_at)
        return culture
    per_day = player_culture_per_day(player_id)
    # Vitesse serveur : prise du premier village du joueur (toutes identiques ici).
    vids = store.player_villages(player_id)
    speed = store.load_village(vids[0]).server_speed if vids else 1
    gained = per_day * (now - culture_at) / 86400.0 * speed
    culture += gained
    store.set_culture(player_id, culture, now)
    return culture


# --- Emplacements d'expansion ------------------------------------------------
def expansion_slots(player_id: int) -> int:
    """Nombre total d'emplacements d'expansion du joueur (résidences + palais)."""
    from app.data import formulas as F
    slots = 0
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if not v:
            continue
        levels = V.building_levels(v)
        slots += F.slots2(levels.get(B.RESIDENCE, 0))   # résidence : niv 10, 20
        slots += F.slots3(levels.get(B.PALACE, 0))      # palais : niv 10, 15, 20
    return slots


def expansion_status(player_id: int, now: float | None = None) -> dict:
    """État d'expansion du joueur (points, slots, prochain seuil, faisabilité)."""
    now = now or _time.time()
    culture = accumulate_culture(player_id, now)
    n_villages = len(store.player_villages(player_id))
    # Fondations déjà en route : chaque train de colons réserve un emplacement
    # d'expansion ET le palier de culture du village qu'il va fonder, tant qu'il
    # n'est pas arrivé. Sinon on dépasserait son quota en lançant plusieurs colons
    # en parallèle (slot/culture n'étant consommés qu'à l'arrivée) — infidèle à Travian.
    pending = store.pending_settlements(player_id)
    slots = expansion_slots(player_id)
    used = (n_villages - 1) + pending           # capitale gratuite ; +1 par colon en route
    next_village = n_villages + pending + 1
    next_cost = culture_needed(next_village)
    return {
        "culture": round(culture),
        "culture_per_day": player_culture_per_day(player_id),
        "villages": n_villages,
        "pending_settlements": pending,
        "slots_total": slots,
        "slots_free": max(0, slots - used),
        "next_village": next_village,
        "culture_needed": next_cost,
        "can_settle": (slots - used) > 0 and culture >= next_cost,
    }


# --- Fondation d'un village (3 colons → vallée libre) ------------------------
def settler_index(tribe: Tribe) -> int:
    from app.data.units import UNITS
    for i, u in enumerate(UNITS[tribe]):
        if u.is_settler:
            return i
    raise ExpansionError("Cette tribu n'a pas de colon.")


def settled_village(name: str, tribe: Tribe, x: int, y: int, player_id: int,
                    server_speed: int, layout_code: str) -> V.Village:
    """Construit un nouveau village (non-capitale) sur une vallée de distribution
    `layout_code`, avec bâtiment principal niv 1 et place de rassemblement niv 1."""
    fields = W.layout_fields(layout_code)
    slots: dict[int, V.Slot] = {}
    for i, bid in enumerate(fields, start=1):
        slots[i] = V.Slot(building_id=bid, level=0)
    slots[19] = V.Slot(building_id=B.MAIN_BUILDING, level=1)
    slots[V.RALLY_SLOT] = V.Slot(building_id=B.RALLY_POINT, level=1)
    return V.Village(name=name, tribe=tribe, slots=slots, server_speed=server_speed,
                     x=x, y=y, player_id=player_id, is_capital=False)


def send_settlers(origin_id: int, x: int, y: int, player_id: int,
                  now: float | None = None) -> dict:
    """Envoie 3 colons depuis `origin_id` fonder un village sur la vallée (x, y)."""
    now = now or _time.time()
    origin = store.load_village(origin_id)
    if origin is None or origin.player_id != player_id:
        raise ExpansionError("Village d'origine invalide.")

    status = expansion_status(player_id, now)
    if status["slots_free"] <= 0:
        raise ExpansionError("Aucun emplacement d'expansion libre "
                             "(résidence/palais niveau 10+).")
    if status["culture"] < status["culture_needed"]:
        raise ExpansionError(
            f"Points de culture insuffisants ({status['culture']}/"
            f"{status['culture_needed']} pour le village {status['next_village']}).")

    tile = store.get_tile(x, y)
    if tile is None or tile["kind"] != "valley":
        raise ExpansionError("On ne fonde un village que sur une vallée libre.")
    if any(v["x"] == x and v["y"] == y for v in store.list_villages()):
        raise ExpansionError("Cette case est déjà occupée.")

    V.tick(origin, now)
    idx = settler_index(origin.tribe)
    if origin.troops[idx] < SETTLERS_NEEDED:
        raise ExpansionError(f"3 colons requis (tu en as {origin.troops[idx]}).")

    units = [0] * 10
    units[idx] = SETTLERS_NEEDED
    origin.troops[idx] -= SETTLERS_NEEDED
    origin.away[idx] += SETTLERS_NEEDED
    store.save_village(origin)

    # Import local pour éviter une dépendance circulaire avec movement.
    from app.engine import movement as M
    secs = M.travel_seconds(origin.x, origin.y, x, y, origin.tribe, units,
                            origin.server_speed)
    mid = store.insert_movement(origin_id, None, player_id, "settle", "outbound",
                                units, now + secs, target_x=x, target_y=y)
    return {"id": mid, "arrive_in": round(secs)}


def found_on_arrival(m: dict, now: float) -> None:
    """Résout l'arrivée d'un mouvement « settle » : fonde le village, consomme les
    colons. Appelé par movement.process_due."""
    origin = store.load_village(m["origin_id"])
    x, y = m["target_x"], m["target_y"]
    units = __import__("json").loads(m["units"])
    idx = next((i for i, n in enumerate(units) if n > 0), 0)

    # Les colons quittent définitivement les effectifs en vol de l'origine.
    V.tick(origin, now)
    origin.away[idx] = max(0, origin.away[idx] - units[idx])
    store.save_village(origin)

    tile = store.get_tile(x, y)
    occupied = any(v["x"] == x and v["y"] == y for v in store.list_villages())
    if tile is None or tile["kind"] != "valley" or occupied:
        # Case devenue invalide entre-temps : les colons rentrent bredouilles.
        from app.engine import movement as M
        secs = M.travel_seconds(x, y, origin.x, origin.y, origin.tribe, units,
                                origin.server_speed)
        store.insert_movement(m["origin_id"], None, m["owner_id"], "settle", "back",
                              units, now + secs, target_x=origin.x, target_y=origin.y)
        store.add_report(m["owner_id"], now, "🏕️ Fondation impossible",
                         {"type": "settle", "ok": False, "coords": [x, y]})
        return

    n = len(store.player_villages(m["owner_id"])) + 1
    nv = settled_village(f"Village {n}", origin.tribe, x, y, m["owner_id"],
                         origin.server_speed, tile["layout"])
    store.insert_village(nv)
    store.add_report(m["owner_id"], now, f"🏕️ Nouveau village fondé ({x}|{y})",
                     {"type": "settle", "ok": True, "coords": [x, y],
                      "name": nv.name})
