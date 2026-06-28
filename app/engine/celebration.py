"""Hôtel de ville : petites & grandes célébrations (points de culture).

⚠️ **Sources** (kirilloid ne modélise PAS les célébrations : il ne donne que le seuil
de culture cumulé — cf. `expansion.py`) :

- **Coûts** (fixes) et **durées par niveau d'hôtel de ville** : TravianZ
  `GameEngine/Data/cel.php` (https://github.com/Shadowss/TravianZ/blob/master/GameEngine/Data/cel.php),
  recoupé `celebration.php`. Mécanique (= « comportement que kirilloid ne couvre pas »).
- **Points de culture accordés** : **vrai Travian** (support.travian.com « Celebrations
  and Town Hall » ; travian.fandom.com/wiki/Celebrations). Petite fête = production de
  culture/jour du **village** de l'hôtel de ville, **plafonnée à 500** ; grande fête =
  production de culture/jour de **tout le compte**, **plafonnée à 2000**.
  ⚠️ **Écart documenté avec TravianZ** : `cel.php` accorde un forfait (`attri` = 500/2000) ;
  on suit le **vrai jeu** (valeur variable plafonnée), conformément à la hiérarchie des
  sources (« conflit → le vrai Travian tranche »).
- **Grande fête → conquête** : tant qu'une grande fête est active dans le village **d'où
  partent** les administrateurs, chaque chef réduit la loyauté de **+5 points**
  supplémentaires (cf. `conquest.GREAT_CELEBRATION_BONUS`). Grande fête : hôtel de ville
  **niveau 10+** requis.

**Approximation documentée** : les points de culture sont **figés au lancement** de la
fête (production courante) puis crédités à la fin ; le vrai jeu les calcule à la fin. La
production de culture variant peu sur la durée d'une fête, l'écart est négligeable.

Persistance : une fête en cours vit sur `Village.celebration`
= `{"type": 1|2, "ends_at": float, "cp": int}` (None si aucune). Le crédit des points est
**paresseux** : récolté par `harvest_completed`, appelé depuis `expansion.accumulate_culture`
(la culture est un total au niveau du *joueur*).
"""
from __future__ import annotations

import time as _time

from app import store
from app.data.buildings import B
from app.engine import village as V

SMALL = 1
GREAT = 2

# Coûts fixes [bois, argile, fer, céréales] (TravianZ cel.php).
COST = {
    SMALL: (6400, 6650, 5940, 1340),
    GREAT: (29700, 33250, 32000, 6700),
}

# Plafond de points de culture accordés (vrai Travian).
CP_CAP = {SMALL: 500, GREAT: 2000}

# Durée de base (s) par niveau d'hôtel de ville (TravianZ $sc / $gc), AVANT division
# par la vitesse serveur. Petite fête : niveaux 1..20 ; grande fête : niveaux 10..20.
SMALL_DURATION = {
    1: 86400, 2: 83290, 3: 80291, 4: 77401, 5: 74614, 6: 71928, 7: 69338,
    8: 66843, 9: 64436, 10: 62117, 11: 59880, 12: 57725, 13: 55647, 14: 53643,
    15: 51712, 16: 49850, 17: 48056, 18: 46326, 19: 44658, 20: 43050,
}
GREAT_DURATION = {
    10: 155291, 11: 149701, 12: 144312, 13: 139116, 14: 134108, 15: 129280,
    16: 124626, 17: 120140, 18: 115815, 19: 111645, 20: 107626,
}


class CelebrationError(Exception):
    pass


def _townhall_level(v: V.Village) -> int:
    return V.building_levels(v).get(B.TOWNHALL, 0)


def duration(ctype: int, th_level: int, server_speed: int) -> float:
    """Durée d'une fête (s), ÷ vitesse serveur (= temps accéléré, fidèle à TravianZ)."""
    if ctype == SMALL:
        lvl = max(1, min(20, th_level))
        base = SMALL_DURATION[lvl]
    else:
        lvl = max(10, min(20, th_level))
        base = GREAT_DURATION[lvl]
    return base / max(1, server_speed)


def cp_award(v: V.Village, ctype: int) -> int:
    """Points de culture accordés (vrai Travian) : production/jour plafonnée.

    Petite fête = culture/jour du **village** ; grande fête = culture/jour du **compte**."""
    from app.engine import expansion as EXP
    if ctype == SMALL:
        return min(EXP.village_culture_per_day(v), CP_CAP[SMALL])
    return min(EXP.player_culture_per_day(v.player_id), CP_CAP[GREAT])


def is_active(v: V.Village, now: float, ctype: int | None = None) -> bool:
    """Une fête (du type `ctype`, ou n'importe lequel) est-elle en cours ?

    Une fête **terminée mais pas encore récoltée** (now ≥ ends_at) est inactive."""
    c = v.celebration
    if not c or now >= c["ends_at"]:
        return False
    return ctype is None or c["type"] == ctype


def great_celebration_active(v: V.Village, now: float) -> bool:
    """Grande fête en cours (bonus de conquête, cf. conquest.GREAT_CELEBRATION_BONUS)."""
    return is_active(v, now, GREAT)


def harvest_completed(player_id: int, now: float) -> float:
    """Récolte les fêtes **terminées** des villages du joueur : crédite leurs points de
    culture (lump), libère le champ `celebration`. Renvoie le total de points récoltés.

    Appelé par `expansion.accumulate_culture` (la culture est un total au niveau joueur)."""
    total = 0.0
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if v and v.celebration and now >= v.celebration["ends_at"]:
            total += v.celebration["cp"]
            v.celebration = None
            store.save_village(v)
    return total


def celebration_status(v: V.Village, now: float) -> dict:
    """État des célébrations d'un village (sans effet de bord : récolte gérée en amont)."""
    th = _townhall_level(v)
    cur = None
    if is_active(v, now):
        cur = {"type": v.celebration["type"],
               "remaining": round(v.celebration["ends_at"] - now),
               "cp": v.celebration["cp"]}
    busy = cur is not None
    options = []
    for ctype, name, min_lvl in ((SMALL, "Petite fête", 1), (GREAT, "Grande fête", 10)):
        unlocked = th >= min_lvl
        options.append({
            "type": ctype, "name": name, "unlocked": unlocked,
            "min_townhall": min_lvl, "cost": list(COST[ctype]),
            "duration": round(duration(ctype, th, v.server_speed)) if unlocked else None,
            "cp": cp_award(v, ctype) if unlocked else None,
            "can_start": unlocked and not busy,
        })
    return {"townhall_level": th, "current": cur, "options": options}


def start_celebration(village_id: int, player_id: int, ctype: int,
                      now: float | None = None) -> dict:
    """Lance une célébration : déduit les ressources, fixe la fin et le gain de points.

    Récolte d'abord les fêtes terminées (via `expansion.accumulate_culture`) pour ne pas
    écraser des points non encore crédités."""
    now = now or _time.time()
    if ctype not in (SMALL, GREAT):
        raise CelebrationError("Type de célébration invalide.")

    # Récolte des fêtes terminées AVANT de (ré)écrire `celebration`.
    from app.engine import expansion as EXP
    EXP.accumulate_culture(player_id, now)

    v = store.load_village(village_id)
    if v is None or v.player_id != player_id:
        raise CelebrationError("Village invalide.")
    th = _townhall_level(v)
    if th < 1:
        raise CelebrationError("Un hôtel de ville est requis pour célébrer.")
    if ctype == GREAT and th < 10:
        raise CelebrationError("Grande fête : hôtel de ville niveau 10 requis.")

    V.tick(v, now)
    if is_active(v, now):
        raise CelebrationError("Une célébration est déjà en cours dans ce village.")

    cost = COST[ctype]
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise CelebrationError("Ressources insuffisantes pour cette célébration.")
    for i in range(4):
        v.resources[i] -= cost[i]

    secs = duration(ctype, th, v.server_speed)
    cp = cp_award(v, ctype)
    v.celebration = {"type": ctype, "ends_at": now + secs, "cp": cp}
    store.save_village(v)
    return {"type": ctype, "ends_in": round(secs), "cp": cp}
