"""Villages Natars : génération et garnisons (socle de l'endgame).

Les **Natars** sont la tribu PNJ (`Tribe.NATARS`) dont les villages parsèment la
carte, concentrés **vers le centre** comme dans le vrai Travian. Ils sont gardés par
de fortes garnisons (`Village.troops`, unités de `UNITS[Tribe.NATARS]`), d'autant plus
fortes qu'on se rapproche du centre. Ils sont **attaquables et pillables** (combat
normal, butin normal) mais **non conquérables** (cf. `conquest.conquer_eligible` :
garde-fou `NPC_TRIBES`). Plus tard, ils détiendront artefacts (phase 2) et plans de
la Merveille (phase 3).

⚠️ **Fidélité** : kirilloid ne modélise ni les villages Natars ni leurs garnisons.
La répartition et la taille des garnisons sont des **approximations documentées**,
calibrées « plus fort au centre » à partir du comportement décrit sur
support.travian.com (« Strongest Natars defenses ») et du wiki communautaire. Les
*stats* des unités Natars, elles, sont recoupées (cf. `app/data/units.py`).
"""
from __future__ import annotations

import random

from app.data.tribes import Tribe
from app.data.buildings import B
from app.engine import village as V
from app.engine import world as W

# Zone d'apparition (distance de Tchebychev au centre). On laisse le cœur immédiat
# (≤ INNER) libre pour ta capitale et tes premières expansions ; les Natars occupent
# l'anneau INNER..OUTER, le plus dense/fort vers INNER.
NATAR_ZONE_INNER = 5
NATAR_ZONE_OUTER = 30
NUM_NATAR_VILLAGES = 16

# Composition de garnison « par point de force » (index d'unité Natar -> effectif).
# La force d'un village = (OUTER − distance), donc un village proche du centre
# multiplie cette base par un facteur bien plus élevé qu'un village périphérique.
# Mélange défensif dominant (piquiers anti-cavalerie, gardes polyvalents) + une
# pointe offensive (cavaliers à hache, chevaliers, éléphants). Approximation documentée.
GARRISON_BASE = {0: 50, 2: 35, 4: 6, 5: 6, 6: 2}  # Piquier, Garde, Axerider, Chevalier, Éléphant


def in_natar_zone(x: int, y: int) -> bool:
    return NATAR_ZONE_INNER <= max(abs(x), abs(y)) <= NATAR_ZONE_OUTER


def garrison_for(x: int, y: int) -> list[int]:
    """Garnison Natar d'un village à (x, y) : composition de base × force, la force
    croissant vers le centre. Renvoie un vecteur `troops[10]`."""
    dist = max(abs(x), abs(y))
    strength = max(1, NATAR_ZONE_OUTER - dist)
    troops = [0] * 10
    for idx, base in GARRISON_BASE.items():
        troops[idx] = base * strength
    return troops


def _natar_village(name: str, x: int, y: int, player_id: int, server_speed: int,
                   layout_code: str) -> V.Village:
    """Construit un village Natar : champs de la vallée, quelques bâtiments défensifs
    et d'entreposage (pour absorber/offrir du butin), et une garnison Natar."""
    slots: dict[int, V.Slot] = {}
    for i, bid in enumerate(W.layout_fields(layout_code), start=1):
        slots[i] = V.Slot(building_id=bid, level=10)  # champs développés (PNJ statique)
    slots[19] = V.Slot(building_id=B.MAIN_BUILDING, level=10)
    slots[20] = V.Slot(building_id=B.WAREHOUSE, level=15)
    slots[21] = V.Slot(building_id=B.GRANARY, level=15)
    slots[V.RALLY_SLOT] = V.Slot(building_id=B.RALLY_POINT, level=1)
    v = V.Village(name=name, tribe=Tribe.NATARS, slots=slots, server_speed=server_speed,
                  x=x, y=y, player_id=player_id, is_capital=False)
    v.troops = garrison_for(x, y)
    # Ressources presque pleines : il y a du butin à piller (plafonné par la capacité).
    v.resources = [c * 0.8 for c in V.capacities(v)]  # [bois, argile, fer, céréales]
    return v


def _candidate_centers(rng: random.Random, n: int) -> list[tuple[int, int]]:
    """`n` points cibles déterministes répartis en couronnes dans la zone Natar
    (plus on est près du centre, plus la garnison sera forte). Les points sont
    ensuite « accrochés » à la vallée libre la plus proche par le seeding."""
    pts: list[tuple[int, int]] = []
    for k in range(n):
        # Répartition en spirale : rayon croissant + angle réparti.
        frac = (k + 0.5) / n
        radius = int(NATAR_ZONE_INNER + frac * (NATAR_ZONE_OUTER - NATAR_ZONE_INNER))
        angle = 2 * 3.14159265 * (k * 0.61803398875)  # nombre d'or ⇒ bonne dispersion
        import math
        x = int(round(radius * math.cos(angle)))
        y = int(round(radius * math.sin(angle)))
        # Petit jitter déterministe pour éviter les alignements.
        x += rng.randint(-2, 2)
        y += rng.randint(-2, 2)
        pts.append((x, y))
    return pts


def _nearest_free_valley(near_x: int, near_y: int,
                         occupied: set[tuple[int, int]]) -> tuple[int, int] | None:
    """Vallée libre la plus proche d'un point (spirale en anneaux), **restant dans la
    zone Natar** (distance au centre du monde ≥ INNER : on ne colle pas un village
    Natar contre la zone de départ). Renvoie None si rien d'éligible localement."""
    from app import store
    for radius in range(0, NATAR_ZONE_OUTER + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                x, y = near_x + dx, near_y + dy
                if (x, y) in occupied or max(abs(x), abs(y)) > W.WORLD_RADIUS:
                    continue
                if max(abs(x), abs(y)) < NATAR_ZONE_INNER:  # trop près du centre
                    continue
                t = store.get_tile(x, y)
                if t and t["kind"] == "valley":
                    return x, y
    return None


def spawn_natar_villages(player_id: int, server_speed: int,
                         n: int = NUM_NATAR_VILLAGES) -> list[V.Village]:
    """Crée et persiste `n` villages Natars sur des vallées libres de la zone
    centrale. Déterministe (seed du monde). Renvoie les villages créés."""
    from app import store
    occupied = {(v["x"], v["y"]) for v in store.list_villages()}
    rng = random.Random(W.WORLD_SEED ^ 0x4E415441)  # "NATA"
    created: list[V.Village] = []
    for i, (cx, cy) in enumerate(_candidate_centers(rng, n), start=1):
        spot = _nearest_free_valley(cx, cy, occupied)
        if spot is None:
            continue
        x, y = spot
        occupied.add((x, y))
        tile = store.get_tile(x, y)
        layout = tile["layout"] if tile else "4-4-4-6"
        v = _natar_village(f"Village natar {i:02d}", x, y, player_id,
                           server_speed, layout)
        created.append(store.insert_village(v))
    return created
