"""Liste de fermes (farm list) — razzias groupées T4.

Fonctionnalité **annexe** T4.6 (le rassemblement gère une liste de cibles de razzia
récurrentes, chacune avec un modèle de troupes ; un bouton « razzia groupée » envoie
un raid sur chaque cible). Mécanique de *confort* : kirilloid ne la modélise pas, elle
n'introduit **aucun nouveau chiffre** de jeu — elle **réutilise** la machinerie de
razzia existante (`movement.send(kind="raid")`, butin/combat inchangés). On la modélise
au niveau du **village** (sa place de rassemblement) plutôt que par listes nommées
multiples (simplification ; le comportement de razzia est identique).

Comportement fidèle (cf. vrai T4 / TravianZ) : « envoyer la liste » lance un raid par
cible **avec les troupes disponibles** ; une cible dont les troupes manquent est
**sautée** (pas d'échec global), exactement comme la machinerie de razzia/route
commerciale déjà en place.
"""
from __future__ import annotations

import json
import time as _time

from app import store
from app.data.units import UNITS
from app.engine import movement as M


class FarmError(Exception):
    pass


def _own_origin(origin_id: int, player_id: int):
    v = store.load_village(origin_id)
    if v is None or v.player_id != player_id:
        raise FarmError("Village invalide.")
    return v


def add_target(origin_id: int, player_id: int, units: list[int],
               target_id: int | None = None,
               target_x: int | None = None, target_y: int | None = None) -> dict:
    """Ajoute une cible à la liste de fermes du village `origin_id`.

    La cible est soit un **village** (`target_id`, pas le tien), soit une **oasis**
    (`target_x/y`). `units` = modèle de troupes (indices de la tribu de l'origine)."""
    origin = _own_origin(origin_id, player_id)
    if len(units) != 10 or sum(units) <= 0:
        raise FarmError("Sélectionne au moins une troupe pour cette cible.")

    if target_id is not None:
        tgt = store.load_village(target_id)
        if tgt is None:
            raise FarmError("Village cible introuvable.")
        if tgt.player_id == player_id:
            raise FarmError("On ne razzie pas ses propres villages.")
        tx, ty, label = tgt.x, tgt.y, tgt.name
    else:
        tile = store.get_tile(target_x, target_y)
        if tile is None or tile["kind"] != "oasis":
            raise FarmError("Cible invalide : seules les oasis sont razziables sur la carte.")
        tx, ty, label = target_x, target_y, f"Oasis ({target_x}|{target_y})"

    if (tx, ty) == (origin.x, origin.y):
        raise FarmError("Cible identique à l'origine.")
    fid = store.insert_farm_target(origin_id, player_id, target_id, tx, ty, units, label)
    return {"id": fid, "label": label}


def remove_target(target_id: int, origin_id: int, player_id: int) -> None:
    _own_origin(origin_id, player_id)
    if store.delete_farm_target(target_id, origin_id) == 0:
        raise FarmError("Cible introuvable.")


def list_targets(origin_id: int, player_id: int) -> list[dict]:
    """Cibles de la liste de fermes + faisabilité (troupes suffisantes maintenant)."""
    origin = _own_origin(origin_id, player_id)
    available = list(origin.troops)
    units_data = UNITS[origin.tribe]
    out = []
    for e in store.farm_targets_for(origin_id):
        units = json.loads(e["units"])
        enough = all(units[i] <= available[i] for i in range(10))
        out.append({
            "id": e["id"], "label": e["label"],
            "target_id": e["target_id"], "x": e["target_x"], "y": e["target_y"],
            "units": [{"name": units_data[i].name, "count": units[i]}
                      for i in range(10) if units[i] > 0],
            "can_raid": enough,
        })
    return out


def raid_all(origin_id: int, player_id: int, now: float | None = None) -> dict:
    """Razzia groupée : envoie un raid sur chaque cible avec son modèle de troupes.

    Une cible dont les troupes manquent (au moment de l'envoi) est **sautée**.
    Réutilise `movement.send` (qui débite les troupes et résout butin/combat)."""
    now = now or _time.time()
    _own_origin(origin_id, player_id)
    sent, skipped = [], []
    for e in store.farm_targets_for(origin_id):
        units = json.loads(e["units"])
        try:
            info = M.send(origin_id, e["target_id"], player_id, "raid", units, now,
                          target_x=e["target_x"], target_y=e["target_y"])
            sent.append({"id": e["id"], "label": e["label"],
                         "arrive_in": info["arrive_in"]})
        except M.MoveError as ex:
            skipped.append({"id": e["id"], "label": e["label"], "reason": str(ex)})
    return {"sent": sent, "skipped": skipped}
