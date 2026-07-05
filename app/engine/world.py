"""Génération et lecture du monde : carte de cases (vallées et oasis).

Chaque case (x, y) de la carte est soit une **vallée** (terrain où l'on peut fonder
un village, caractérisé par sa distribution de champs, p. ex. 4-4-4-6), soit une
**oasis** (bonus de production, gardée par des animaux sauvages — tribu Nature).

Le terrain est **déterministe** : un même `WORLD_SEED` + couple (x, y) donne toujours
la même case. On le persiste tout de même (table `tiles`) car l'état des oasis est
*mutable* (les animaux tués ne réapparaissent pas dans ce modèle local).

Fidélité Travian : distributions de champs et types d'oasis repris des tables
officielles ; garnisons d'animaux générées (la distribution exacte n'est pas publique).
"""
from __future__ import annotations

import random

from app.data.tribes import Tribe
from app.data.units import UNITS

WORLD_SEED = 42
# Carte (2·R+1)² cases, centrée sur (0, 0). Agrandie « façon Travian » (grand carré
# centré, Natars vers le milieu) : R=150 ⇒ 301×301 ≈ 90 k cases, de quoi loger des
# empires rivaux frontaliers (cf. engine.rivals). Le terrain est déterministe
# (seed + (x,y)) donc agrandir le rayon n'altère aucune case existante (cf. seed_world :
# ré-insertion idempotente via INSERT OR IGNORE, pas de wipe).
WORLD_RADIUS = 150
# Densité d'oasis. Travian ne publie pas son ratio exact, mais ses cartes sont
# nettement plus riches en oasis que notre 0.14 initial ; ~0.30 colle à l'aspect
# « beaucoup d'oasis » du vrai jeu (cf. CLAUDE.md, approximation tunée).
OASIS_RATE = 0.30

WOOD, CLAY, IRON, CROP = 0, 1, 2, 3


# --- Distributions de champs des vallées (bois-argile-fer-céréales) ----------
# 18 champs au total. Le 4-4-4-6 standard domine ; les variantes riches en
# céréales (9-cropper, 15-cropper) sont rares. (code, poids)
VALLEY_LAYOUTS: list[tuple[str, float]] = [
    ("4-4-4-6", 60),
    ("3-4-5-6", 6), ("3-5-4-6", 6), ("4-3-5-6", 6),
    ("4-5-3-6", 6), ("5-3-4-6", 6), ("5-4-3-6", 6),
    ("3-3-3-9", 3),      # 9-cropper
    ("1-1-1-15", 1),     # 15-cropper
]


def layout_fields(code: str) -> list[int]:
    """Liste des 18 champs (ids de bâtiment 0..3) pour une distribution donnée."""
    w, c, i, cr = (int(n) for n in code.split("-"))
    return [WOOD] * w + [CLAY] * c + [IRON] * i + [CROP] * cr


# --- Types d'oasis : bonus de production (index ressource -> % bonus) ---------
# Les 8 types de Travian : 4 mono-ressource +25 %, 3 combos « ressource +25 % /
# céréales +25 % », et l'oasis +50 % céréales. `emoji` distingue le type sur la
# carte. (code, label, bonus, emoji, poids)
OASIS_BONUSES: list[tuple[str, str, dict[int, int], str, float]] = [
    ("wood25",       "Bois +25 %",          {WOOD: 25}, "🌲", 10),
    ("clay25",       "Argile +25 %",        {CLAY: 25}, "🧱", 10),
    ("iron25",       "Fer +25 %",           {IRON: 25}, "⛏️", 10),
    ("crop25",       "Céréales +25 %",      {CROP: 25}, "🌾", 10),
    ("crop50",       "Céréales +50 %",      {CROP: 50}, "🌾🌾", 4),
    ("wood25crop25", "Bois +25 % / Céréales +25 %",   {WOOD: 25, CROP: 25}, "🌲🌾", 4),
    ("clay25crop25", "Argile +25 % / Céréales +25 %", {CLAY: 25, CROP: 25}, "🧱🌾", 4),
    ("iron25crop25", "Fer +25 % / Céréales +25 %",    {IRON: 25, CROP: 25}, "⛏️🌾", 4),
]

OASIS_BY_CODE = {code: (label, bonus, emoji)
                 for code, label, bonus, emoji, _ in OASIS_BONUSES}


def oasis_label(code: str) -> str:
    return OASIS_BY_CODE.get(code, (code, {}, "🌿"))[0]


def oasis_bonus(code: str) -> dict[int, int]:
    return OASIS_BY_CODE.get(code, ("", {}, "🌿"))[1]


def oasis_emoji(code: str) -> str:
    return OASIS_BY_CODE.get(code, ("", {}, "🌿"))[2]


# --- Génération déterministe d'une case --------------------------------------
def _rng(x: int, y: int) -> random.Random:
    return random.Random((WORLD_SEED * 73856093) ^ (x * 19349663) ^ (y * 83492791))


def _weighted(rng: random.Random, items, weights) -> object:
    return rng.choices(items, weights=weights, k=1)[0]


def _spawn_animals(rng: random.Random) -> list[int]:
    """Garnison d'animaux d'une oasis (les espèces fortes sont plus rares)."""
    animals = [0] * 10
    spawn_w = [10, 9, 8, 7, 6, 5, 3, 2, 2, 1]   # rat … éléphant
    for _ in range(rng.randint(1, 4)):
        i = rng.choices(range(10), weights=spawn_w, k=1)[0]
        animals[i] += rng.randint(1, max(1, 20 // (i + 1)))
    return animals


def generate_tile(x: int, y: int, force_valley: bool = False) -> dict:
    """État initial d'une case (déterministe). `force_valley` pour le centre."""
    rng = _rng(x, y)
    if not force_valley and rng.random() < OASIS_RATE:
        codes = [c for c, *_ in OASIS_BONUSES]
        weights = [w for *_, w in OASIS_BONUSES]
        code = _weighted(rng, codes, weights)
        return {"x": x, "y": y, "kind": "oasis", "layout": code,
                "animals": _spawn_animals(rng)}
    codes = [c for c, _ in VALLEY_LAYOUTS]
    weights = [w for _, w in VALLEY_LAYOUTS]
    code = _weighted(rng, codes, weights)
    return {"x": x, "y": y, "kind": "valley", "layout": code, "animals": None}


def generate_world(radius: int = WORLD_RADIUS) -> list[dict]:
    """Toutes les cases de la carte. Le centre (0,0) est forcé en vallée 4-4-4-6
    (village humain de départ) ; ses voisins immédiats restent des vallées pour
    laisser de la place aux premiers villages."""
    tiles = []
    for y in range(-radius, radius + 1):
        for x in range(-radius, radius + 1):
            force = max(abs(x), abs(y)) <= 2
            t = generate_tile(x, y, force_valley=force)
            if x == 0 and y == 0:
                t["layout"] = "4-4-4-6"
            tiles.append(t)
    return tiles


def animal_count(animals: list[int] | None) -> int:
    return sum(animals) if animals else 0


def animal_breakdown(animals: list[int] | None) -> list[dict]:
    """Détail des animaux présents (pour l'UI / les rapports)."""
    if not animals:
        return []
    names = [u.name for u in UNITS[Tribe.NATURE]]
    return [{"name": names[i], "count": n} for i, n in enumerate(animals) if n > 0]
