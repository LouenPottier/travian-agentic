"""Occupation d'oasis : rattacher le bonus de production d'une oasis à un village.

Mécanique Travian (référence de *comportement* : vrai jeu / TravianZ ; **kirilloid ne
modélise rien de l'occupation d'oasis** → les seuils ci-dessous sont des approximations
documentées au même titre que le commerce ou le héros) :

- Le **manoir du héros** débloque des emplacements d'oasis selon son niveau :
  niv 10 → 1 oasis, niv 15 → 2, niv 20 → 3 (même palier que les emplacements de
  palais : on réutilise `formulas.slots3`).
- Une oasis ne peut être annexée que si elle est **proche** du village. Le vrai
  Travian limite l'annexion aux oasis « bordant » le village ; on l'approxime par une
  distance de Tchebychev ≤ `OASIS_RANGE` (carré de côté 2·R+1 centré sur le village).
- Il faut d'abord en avoir **nettoyé les animaux** (combat d'oasis, cf. movement.py) ;
  une oasis encore gardée n'est pas annexable.
- Une oasis déjà occupée ne peut pas être annexée *pacifiquement* (`occupy`). La **prise**
  d'une oasis à un adversaire (re-conquête) se fait par **attaque victorieuse** : une fois la
  case nettoyée de ses animaux, une attaque/razzia dont des troupes survivent détache l'oasis
  de son détenteur et la rattache à un village éligible de l'attaquant (`conquer`, appelé depuis
  `movement._resolve_oasis`). Faute de village éligible, l'oasis est seulement libérée.

Le bonus de production de l'oasis est crédité au village via `village.gross_production`
(le village stocke la liste de ses oasis dans `Village.oases`).
"""
from __future__ import annotations

from app.data import formulas as F
from app.data.buildings import B
from app.engine import village as V
from app.engine import world as W
from app import store

# Portée d'annexion (distance de Tchebychev max entre le village et l'oasis).
OASIS_RANGE = 3
# Niveau minimal de manoir du héros pour annexer la première oasis.
MANSION_MIN_LEVEL = 10


class OasisError(Exception):
    """Occupation impossible (conditions non réunies)."""


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    return max(abs(ax - bx), abs(ay - by))


def mansion_level(v: V.Village) -> int:
    return V.building_levels(v).get(B.HERO_MANSION, 0)


def max_oases(v: V.Village) -> int:
    """Nombre d'oasis que ce village peut annexer (selon le manoir du héros)."""
    return F.slots3(mansion_level(v))


def free_slots(v: V.Village) -> int:
    return max(0, max_oases(v) - len(v.oases))


def in_range(v: V.Village, x: int, y: int) -> bool:
    return chebyshev(v.x, v.y, x, y) <= OASIS_RANGE


def _check_occupiable(v: V.Village, tile: dict, x: int, y: int) -> None:
    """Lève `OasisError` si `v` ne peut pas annexer la case (x, y)."""
    if tile is None or tile["kind"] != "oasis":
        raise OasisError("Cette case n'est pas une oasis.")
    if tile.get("owner_id") is not None:
        raise OasisError("Cette oasis est déjà occupée.")
    if W.animal_count(tile["animals"]) > 0:
        raise OasisError("L'oasis est encore gardée par des animaux : nettoie-la d'abord.")
    if mansion_level(v) < MANSION_MIN_LEVEL:
        raise OasisError(f"Manoir du héros niveau {MANSION_MIN_LEVEL} requis "
                         f"(actuel : {mansion_level(v)}).")
    if not in_range(v, x, y):
        raise OasisError(f"Oasis trop loin de {v.name} (portée {OASIS_RANGE} cases).")
    if free_slots(v) <= 0:
        raise OasisError("Plus d'emplacement d'oasis libre dans ce village "
                         "(améliore le manoir du héros : niv 10/15/20).")


# --- Garnison d'une oasis occupée -------------------------------------------
# Vrai T4.6 (recoupé support.travian.com / wiki, kirilloid muet) : on peut **renforcer
# une oasis qu'on occupe** ; ces troupes la **défendent** (sans bonus mur/résidence) et
# doivent être **détruites** pour la reprendre (attaque normale). La garnison vit sur
# l'entrée `Village.oases` du village d'attache : `{"x","y","code","garrison":[10]}`
# (unités de la tribu du propriétaire). ⚠️ **Simplification documentée** : la garnison
# d'oasis ne **consomme pas** de céréales (le vrai jeu la nourrit au village d'attache ;
# le modèle ne suit pas les contingents par origine, comme pour les renforts de village).


def _oasis_entry(v: V.Village, x: int, y: int) -> dict | None:
    for o in v.oases:
        if o["x"] == x and o["y"] == y:
            return o
    return None


def oasis_garrison(v: V.Village, x: int, y: int) -> list[int]:
    o = _oasis_entry(v, x, y)
    return list(o.get("garrison", [0] * 10)) if o else [0] * 10


def set_oasis_garrison(v: V.Village, x: int, y: int, garrison: list[int]) -> None:
    """Fixe la garnison d'une oasis occupée (retirée si vide)."""
    o = _oasis_entry(v, x, y)
    if o is None:
        return
    if any(garrison):
        o["garrison"] = list(garrison)
    else:
        o.pop("garrison", None)


def occupy(village_id: int, x: int, y: int, player_id: int, now: float | None = None) -> dict:
    """Annexe l'oasis (x, y) au village `village_id`. Lève `OasisError` sinon."""
    v = store.load_village(village_id)
    if v is None or v.player_id != player_id:
        raise OasisError("Village invalide.")
    tile = store.get_tile(x, y)
    _check_occupiable(v, tile, x, y)

    V.tick(v, now)  # fige la production à l'ancien rythme avant d'ajouter le bonus
    v.oases.append({"x": x, "y": y, "code": tile["layout"]})
    store.save_village(v)
    store.set_tile_owner(x, y, village_id)
    return {"x": x, "y": y, "label": W.oasis_label(tile["layout"]),
            "bonus": W.oasis_bonus(tile["layout"]), "free_slots": free_slots(v)}


def abandon(village_id: int, x: int, y: int, player_id: int, now: float | None = None) -> dict:
    """Détache une oasis du village (libère l'emplacement et la case)."""
    v = store.load_village(village_id)
    if v is None or v.player_id != player_id:
        raise OasisError("Village invalide.")
    entry = _oasis_entry(v, x, y)
    if entry is None:
        raise OasisError("Cette oasis n'est pas occupée par ce village.")

    V.tick(v, now)
    # La garnison éventuelle rejoint instantanément le village d'attache (approximation :
    # le vrai jeu la fait rentrer au rassemblement ; on évite de perdre des troupes).
    garrison = entry.get("garrison", [0] * 10)
    for i in range(10):
        v.troops[i] += garrison[i]
    v.oases = [o for o in v.oases if not (o["x"] == x and o["y"] == y)]
    store.save_village(v)
    store.set_tile_owner(x, y, None)
    return {"x": x, "y": y, "free_slots": free_slots(v),
            "garrison_returned": list(garrison)}


def best_eligible_village(player_id: int, x: int, y: int,
                          prefer_id: int | None = None) -> V.Village | None:
    """Meilleur village du joueur pouvant accueillir l'oasis (x, y) : en portée,
    manoir niv 10+, emplacement libre. Préfère `prefer_id`, puis le plus proche.
    Renvoie None si aucun village n'est éligible."""
    cands = []
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if (v is not None and mansion_level(v) >= MANSION_MIN_LEVEL
                and in_range(v, x, y) and free_slots(v) > 0):
            cands.append(v)
    if not cands:
        return None
    cands.sort(key=lambda v: (v.id != prefer_id, chebyshev(v.x, v.y, x, y)))
    return cands[0]


def conquer(tile: dict, origin: V.Village, now: float) -> dict:
    """Vol d'une oasis ennemie après une attaque victorieuse (animaux nettoyés).

    `origin` = village d'où part l'attaque. Détache l'oasis de son détenteur (qui
    perd le bonus et est notifié) puis la rattache à un village éligible de
    l'attaquant (préférence à `origin`). Si aucun n'est éligible (hors portée,
    manoir trop bas, pas de slot), l'oasis est seulement **libérée**. Renvoie un
    récap pour le rapport ; `conquered=False` si non rattachée."""
    x, y = tile["x"], tile["y"]
    prev_vid = tile.get("owner_id")
    if prev_vid is None:
        return {"conquered": False}
    prev = store.load_village(prev_vid)
    if prev is not None and prev.player_id == origin.player_id:
        return {"conquered": False}  # déjà à nous : rien à voler

    if prev is not None:
        V.tick(prev, now)  # fige sa prod avant de retirer le bonus d'oasis
        prev.oases = [o for o in prev.oases if not (o["x"] == x and o["y"] == y)]
        store.save_village(prev)
        store.add_report(prev.player_id, now, f"🌴 Oasis perdue ({x}|{y})",
                         {"type": "oasis_lost", "coords": [x, y], "village": prev.name})
    store.set_tile_owner(x, y, None)

    nv = best_eligible_village(origin.player_id, x, y, prefer_id=origin.id)
    if nv is None:
        return {"conquered": False, "freed": True,
                "from": prev.name if prev else "?"}
    # Si c'est `origin` qui annexe, on mute l'objet vivant (que le mouvement
    # ré-enregistre ensuite) pour éviter qu'un save ultérieur n'écrase l'annexion.
    if nv.id == origin.id:
        nv = origin
    V.tick(nv, now)
    nv.oases.append({"x": x, "y": y, "code": tile["layout"]})
    store.save_village(nv)
    store.set_tile_owner(x, y, nv.id)
    return {"conquered": True, "village": nv.name,
            "from": prev.name if prev else "?", "free_slots": free_slots(nv)}


def eligible_villages(player_id: int, tile: dict) -> list[dict]:
    """Villages du joueur capables d'annexer cette oasis (libre + nettoyée).

    Renvoie [{id, name, dist, free_slots}] pour les villages en portée avec un
    manoir niv 10+ et un emplacement libre ; liste vide si l'oasis est gardée,
    occupée, ou qu'aucun village ne remplit les conditions.
    """
    if tile["kind"] != "oasis" or tile.get("owner_id") is not None:
        return []
    if W.animal_count(tile["animals"]) > 0:
        return []
    out = []
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if (v is not None and mansion_level(v) >= MANSION_MIN_LEVEL
                and in_range(v, tile["x"], tile["y"]) and free_slots(v) > 0):
            out.append({"id": vid, "name": v.name,
                        "dist": chebyshev(v.x, v.y, tile["x"], tile["y"]),
                        "free_slots": free_slots(v)})
    out.sort(key=lambda e: e["dist"])
    return out
