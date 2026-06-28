"""Mouvements d'armées : envoi, trajet, résolution du combat, butin, retour, rapports.

Trajet : temps = distance (cases) / vitesse de la troupe la plus lente (cases/h),
accéléré par la vitesse serveur. Le combat à l'arrivée utilise le moteur validé
`engine.combat`. Les survivants repartent vers le village d'origine avec le butin.
"""
from __future__ import annotations

import json
import math
import threading
import time as _time

from app import store
from app.data import buildings as BLD
from app.data.units import UNITS
from app.engine import combat as C
from app.engine import village as V


def distance(ax, ay, bx, by) -> float:
    return math.hypot(ax - bx, ay - by)


def army_speed(tribe, units: list[int]) -> float:
    speeds = [UNITS[tribe][i].speed for i, n in enumerate(units) if n > 0]
    return min(speeds) if speeds else 1.0


def travel_seconds(ax, ay, bx, by, tribe, units, server_speed) -> float:
    d = distance(ax, ay, bx, by)
    return d / army_speed(tribe, units) * 3600.0 / server_speed


class MoveError(Exception):
    pass


def send(origin_id: int, target_id: int, player_id: int, kind: str,
         units: list[int], now: float | None = None) -> dict:
    now = now or _time.time()
    origin = store.load_village(origin_id)
    target = store.load_village(target_id)
    if origin is None or target is None:
        raise MoveError("Village introuvable.")
    if origin.player_id != player_id:
        raise MoveError("Ce village ne t'appartient pas.")
    if origin_id == target_id:
        raise MoveError("Cible identique à l'origine.")
    if sum(units) <= 0:
        raise MoveError("Aucune troupe sélectionnée.")

    V.tick(origin, now)
    for i in range(10):
        if units[i] > origin.troops[i]:
            raise MoveError("Pas assez de troupes.")
    # Les troupes quittent la garnison mais restent à la charge du village
    # d'origine (elles continuent d'y consommer du blé pendant le trajet).
    for i in range(10):
        origin.troops[i] -= units[i]
        origin.away[i] += units[i]
    store.save_village(origin)

    secs = travel_seconds(origin.x, origin.y, target.x, target.y,
                          origin.tribe, units, origin.server_speed)
    arrive_at = now + secs
    mid = store.insert_movement(origin_id, target_id, player_id, kind, "outbound",
                                units, arrive_at)
    return {"id": mid, "arrive_in": round(secs)}


def _build_place(target: V.Village) -> C.Place:
    wall_level, wall_bonus = 0, (lambda lvl: {"def_bonus": 0.0})
    for s in target.slots.values():
        b = BLD.get(s.building_id)
        if b.slot == "wall" and s.level > 0:
            wall_level, wall_bonus = s.level, b.benefit
    return C.Place(tribe=int(target.tribe), pop=V.population(target),
                   wall_level=wall_level, wall_bonus=wall_bonus)


def _resolve_battle(origin, target, units, kind, now):
    """Résout un combat à l'arrivée et renvoie (survivants, butin)."""
    off = C.Off(units=UNITS[origin.tribe], numbers=list(units), upgrades=[0] * 10,
                pop=V.population(origin), kind=kind)
    deff = C.Defender(units=UNITS[target.tribe], numbers=list(target.troops),
                      upgrades=[0] * 10)
    place = _build_place(target)
    res = C.combat(place, off, [deff])

    survivors = [round(units[i] * (1 - res.off_losses)) for i in range(10)]
    def_before = list(target.troops)
    target.troops = [round(target.troops[i] * (1 - res.def_losses)) for i in range(10)]

    # Butin : capacité de transport des survivants
    cap = sum(survivors[i] * UNITS[origin.tribe][i].capacity for i in range(10))
    avail = sum(target.resources)
    loot = [0, 0, 0, 0]
    take = min(cap, avail)
    if avail > 0 and take > 0:
        for i in range(4):
            loot[i] = round(take * target.resources[i] / avail)
            target.resources[i] = max(0.0, target.resources[i] - loot[i])
    store.save_village(target)

    # Rapports
    store.add_report(origin.player_id, now, f"⚔️ Attaque sur {target.name}", {
        "type": "offensive", "cible": target.name, "kind": kind,
        "envoyees": list(units), "survivantes": survivors,
        "pertes_pct": round(res.off_losses * 100), "butin": loot})
    store.add_report(target.player_id, now, f"🛡️ Défense de {target.name}", {
        "type": "defensive", "attaquant": origin.name, "kind": kind,
        "def_avant": def_before, "def_apres": target.troops,
        "pertes_pct": round(res.def_losses * 100), "butin_pille": loot})
    return survivors, loot


# FastAPI exécute les endpoints synchrones dans un pool de threads : deux requêtes
# concurrentes (poll de l'UI + navigation) appelaient process_due en parallèle,
# lisaient le même mouvement « arrivé » avant suppression et réintégraient donc les
# troupes deux fois (duplication au retour). Ce verrou sérialise le traitement.
_PROCESS_LOCK = threading.Lock()


def process_due(now: float | None = None) -> int:
    """Traite tous les mouvements arrivés à échéance. Renvoie le nombre traité."""
    now = now or _time.time()
    with _PROCESS_LOCK:
        return _process_due_locked(now)


def _process_due_locked(now: float) -> int:
    count = 0
    for m in store.due_movements(now):
        count += 1
        origin = store.load_village(m["origin_id"])
        target = store.load_village(m["target_id"])
        units = list(json.loads(m["units"]))

        if m["phase"] == "back":
            # Retour : survivants + butin réintégrés à l'origine ; ils quittent
            # les effectifs « en vol » pour rejoindre la garnison.
            V.tick(origin, now)
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - units[i])
                origin.troops[i] += units[i]
            loot = json.loads(m["loot"])
            caps = V.capacities(origin)
            for i in range(4):
                origin.resources[i] = min(caps[i], origin.resources[i] + loot[i])
            store.save_village(origin)
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "reinforce":
            # Les renforts cessent de consommer chez l'origine et passent à la
            # charge de la cible où ils stationnent désormais.
            V.tick(origin, now)
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - units[i])
            store.save_village(origin)
            V.tick(target, now)
            for i in range(10):
                target.troops[i] += units[i]
            store.save_village(target)
            store.add_report(target.player_id, now, f"➕ Renfort à {target.name}",
                             {"type": "reinforce", "de": origin.name, "unites": units})
            store.delete_movement(m["id"])
            continue

        # attack / raid
        V.tick(target, now)
        survivors, loot = _resolve_battle(origin, target, units, m["kind"], now)
        # Les pertes au combat quittent définitivement les effectifs en vol de
        # l'origine ; les survivants y restent jusqu'à leur retour.
        V.tick(origin, now)
        for i in range(10):
            origin.away[i] = max(0, origin.away[i] - (units[i] - survivors[i]))
        store.save_village(origin)
        store.delete_movement(m["id"])
        if sum(survivors) > 0:
            secs = travel_seconds(target.x, target.y, origin.x, origin.y,
                                  origin.tribe, survivors, origin.server_speed)
            store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                  m["kind"], "back", survivors, now + secs, loot)
    return count
