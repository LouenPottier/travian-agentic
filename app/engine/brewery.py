"""Brasserie (Teutons) : fête de la bière (mead festival) → bonus d'attaque.

Vrai T4.6 — **kirilloid ne modélise pas** la brasserie ; chiffres recoupés sur la doc
officielle / wiki (cf. hiérarchie des sources, CLAUDE.md) :
- **support.travian.com** « Brewery » (autorité de comportement) ;
- **unofficialtravian.com/2025/10/brewery/** et **travian.fandom.com/wiki/Brewery**.

Mécanique :
- Brasserie **teutonne**, **capitale uniquement**, **niveau max 10** (cf. data/buildings).
- On y lance une **fête de la bière** (coût fixe, durée **72 h** ÷ vitesse serveur).
  Tant qu'elle est active, **toutes les troupes du compte** gagnent **+1 % d'attaque
  par niveau** de brasserie (effet *account-wide*), appliqué **au moment du combat**.

⚠️ **Effets secondaires NON modélisés** (approximation documentée) : pendant la fête, les
catapultes teutonnes frappent au hasard, et la persuasion des chefs (perte de loyauté)
est divisée par 2. On ne modélise que le **bonus d'attaque** (cœur de la mécanique).

État : `Village.brewery_festival = {"ends_at": float}` (None sinon), persisté.
"""
from __future__ import annotations

import time as _time

from app import store
from app.data.buildings import B
from app.data.tribes import Tribe
from app.engine import village as V

# Coût fixe de la fête de la bière [bois, argile, fer, céréales] (vrai Travian).
COST = (3870, 1680, 215, 10900)
DURATION = 72 * 3600          # 72 h (avant division par la vitesse serveur)
BONUS_PER_LEVEL = 0.01        # +1 % d'attaque par niveau de brasserie


class BreweryError(Exception):
    pass


def _brewery_level(v: V.Village) -> int:
    return V.building_levels(v).get(B.BREWERY, 0)


def festival_active(v: V.Village, now: float) -> bool:
    f = v.brewery_festival
    return bool(f) and now < f["ends_at"]


def attack_bonus(player_id: int, now: float) -> float:
    """Bonus d'attaque (fraction, ex. 0,10) accordé à TOUTES les troupes du joueur tant
    qu'une fête de la bière est active dans sa brasserie. 0 sinon (account-wide)."""
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if v and festival_active(v, now):
            return BONUS_PER_LEVEL * _brewery_level(v)
    return 0.0


def duration(server_speed: int) -> float:
    return DURATION / max(1, server_speed)


def brewery_status(v: V.Village, now: float) -> dict:
    """État de la brasserie d'un village (pour l'API / l'UI)."""
    lvl = _brewery_level(v)
    active = festival_active(v, now)
    return {
        "level": lvl,
        "bonus_per_level": BONUS_PER_LEVEL,
        "active": active,
        "remaining": round(v.brewery_festival["ends_at"] - now) if active else 0,
        "current_bonus": round(BONUS_PER_LEVEL * lvl, 4) if active else 0.0,
        "cost": list(COST),
        "duration": round(duration(v.server_speed)),
        "can_start": lvl >= 1 and not active,
    }


def start_festival(village_id: int, player_id: int, now: float | None = None) -> dict:
    """Lance une fête de la bière depuis la brasserie (Teuton, capitale)."""
    now = now or _time.time()
    v = store.load_village(village_id)
    if v is None or v.player_id != player_id:
        raise BreweryError("Village invalide.")
    if v.tribe != Tribe.TEUTONS:
        raise BreweryError("La brasserie est réservée aux Teutons.")
    if _brewery_level(v) < 1:
        raise BreweryError("Une brasserie est requise.")
    V.tick(v, now)
    if festival_active(v, now):
        raise BreweryError("Une fête de la bière est déjà en cours.")
    if any(v.resources[i] < COST[i] for i in range(4)):
        raise BreweryError("Ressources insuffisantes pour la fête de la bière.")
    for i in range(4):
        v.resources[i] -= COST[i]
    secs = duration(v.server_speed)
    v.brewery_festival = {"ends_at": now + secs}
    store.save_village(v)
    return {"ends_in": round(secs), "bonus": round(BONUS_PER_LEVEL * _brewery_level(v), 4)}
