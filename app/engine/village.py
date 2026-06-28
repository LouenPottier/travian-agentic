"""Moteur de village T4.6 : production paresseuse, stockage, file de construction.

Modèle « paresseux » fidèle à Travian : on ne « tick » pas chaque seconde. Les
ressources sont recalculées à la lecture (production horaire × temps écoulé,
plafonnée par entrepôt/grenier), et la file de construction est appliquée quand
on dépasse l'heure de fin d'un ordre.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from functools import lru_cache

from app.data import buildings as BLD
from app.data.buildings import B, Building
from app.data.tribes import Tribe
from app.data.units import UNITS, Unit

# Indices de ressources
WOOD, CLAY, IRON, CROP = 0, 1, 2, 3
BASE_STORAGE = 800  # entrepôt/grenier de base sans bâtiment


@dataclass
class Slot:
    """Un emplacement du village : un bâtiment (ou champ) à un niveau donné."""
    building_id: int
    level: int = 0


@dataclass
class BuildOrder:
    slot_index: int
    target_level: int
    finish_at: float


@dataclass
class TrainOrder:
    building_id: int      # bâtiment d'entraînement (caserne/écurie/atelier/résidence)
    unit_index: int       # index de l'unité dans la tribu
    remaining: int        # unités encore à produire
    per_unit: float       # temps par unité (s, vitesse serveur incluse)
    next_finish: float    # date de complétion de la prochaine unité


@dataclass
class Village:
    name: str
    tribe: Tribe
    # 18 champs de ressources (indices 1..18) + centre du village (19..40)
    slots: dict[int, Slot] = field(default_factory=dict)
    resources: list[float] = field(default_factory=lambda: [750.0, 750.0, 750.0, 750.0])
    updated_at: float = field(default_factory=_time.time)
    queue: list[BuildOrder] = field(default_factory=list)
    server_speed: int = 1
    max_queue: int = 1  # nombre d'ordres simultanés (Romains : 2, géré plus tard)
    is_capital: bool = True
    # Identité & position (persistance / monde multijoueur)
    id: int | None = None
    player_id: int | None = None
    x: int = 0
    y: int = 0
    # Troupes stationnées (effectifs par index d'unité de la tribu, 10 emplacements)
    troops: list[int] = field(default_factory=lambda: [0] * 10)
    # Troupes appartenant au village mais en déplacement (aller/retour) : elles
    # continuent de consommer le blé d'ici (fidélité Travian) mais ne défendent
    # pas et ne sont pas (ré)envoyables tant qu'elles ne sont pas rentrées.
    away: list[int] = field(default_factory=lambda: [0] * 10)
    training: list[TrainOrder] = field(default_factory=list)


# Emplacements : 1..18 champs de ressources, 19..38 centre du village,
# 39 place de rassemblement, 40 muraille.
CENTER_SLOTS = range(19, 39)
RALLY_SLOT = 39
WALL_SLOT = 40


# --- Population cumulée d'un bâtiment (somme des incréments par niveau) -------
@lru_cache(maxsize=None)
def building_population(building_id: int, level: int) -> int:
    b = BLD.get(building_id)
    return sum(b.upkeep_at(l) for l in range(1, level + 1))


def population(v: Village) -> int:
    """Population totale du village = consommation de céréales par les habitants."""
    return sum(building_population(s.building_id, s.level)
               for s in v.slots.values() if s.level > 0)


def building_levels(v: Village) -> dict[int, int]:
    """Niveau maximal présent pour chaque type de bâtiment (pour les prérequis)."""
    levels: dict[int, int] = {}
    for s in v.slots.values():
        levels[s.building_id] = max(levels.get(s.building_id, 0), s.level)
    return levels


# --- Production horaire brute (avant entretien) ------------------------------
def gross_production(v: Village) -> list[float]:
    prod = [0.0, 0.0, 0.0, 0.0]
    res_map = {B.WOODCUTTER: WOOD, B.CLAYPIT: CLAY, B.IRONMINE: IRON, B.CROPLAND: CROP}
    for s in v.slots.values():
        idx = res_map.get(s.building_id)
        if idx is not None:  # les champs niveau 0 produisent déjà une base (prod4(0)=3)
            prod[idx] += BLD.get(s.building_id).benefit(s.level)

    # Bonus des bâtiments de raffinage (% de la production de base du type)
    def bonus(building_id):
        best = 0.0
        for s in v.slots.values():
            if s.building_id == building_id and s.level > 0:
                best = max(best, BLD.get(building_id).benefit(s.level))  # % cumulé
        return best / 100.0

    prod[WOOD] *= 1 + bonus(B.SAWMILL)
    prod[CLAY] *= 1 + bonus(B.BRICKYARD)
    prod[IRON] *= 1 + bonus(B.IRONFOUNDRY)
    prod[CROP] *= 1 + bonus(B.GRAINMILL) + bonus(B.BAKERY)
    return prod


def troop_upkeep(v: Village) -> int:
    """Consommation de céréales : troupes stationnées + troupes en déplacement.

    Les armées en vol (raid/attaque/renfort à l'aller, survivants au retour)
    mangent toujours le blé de leur village d'origine, comme dans le vrai Travian.
    Les renforts qui ont atteint leur cible sont stationnés (`troops`) dans la
    cible et y consomment donc le blé : on ne les compte plus ici.
    """
    units = UNITS[v.tribe]
    return sum((v.troops[i] + v.away[i]) * units[i].upkeep for i in range(len(v.troops)))


def net_production(v: Village) -> list[float]:
    """Production nette horaire ; les céréales sont diminuées de la population et des troupes."""
    prod = gross_production(v)
    prod[CROP] -= population(v) + troop_upkeep(v)
    return prod


# --- Capacité de stockage ----------------------------------------------------
def _storage(v: Village, building_id: int) -> int:
    caps = [BLD.get(building_id).benefit(s.level)
            for s in v.slots.values() if s.building_id == building_id and s.level > 0]
    return sum(caps) if caps else BASE_STORAGE


def warehouse_capacity(v: Village) -> int:
    return _storage(v, B.WAREHOUSE)


def granary_capacity(v: Village) -> int:
    return _storage(v, B.GRANARY)


def capacities(v: Village) -> list[int]:
    w = warehouse_capacity(v)
    g = granary_capacity(v)
    return [w, w, w, g]


# --- Mise à jour paresseuse --------------------------------------------------
def tick(v: Village, now: float | None = None) -> None:
    """Avance l'état du village jusqu'à `now`.

    Ressources accumulées par segments délimités par les événements (fin de
    construction, sortie d'une unité d'entraînement), car chacun modifie la
    production (un champ amélioré, ou une troupe de plus à nourrir).
    """
    now = now or _time.time()
    if now <= v.updated_at:
        return

    # Collecte des événements <= now : ('build', t, order) et ('train', t, order)
    events: list[tuple[float, str, object]] = []
    for o in v.queue:
        if o.finish_at <= now:
            events.append((o.finish_at, "build", o))
    for to in v.training:
        t = to.next_finish
        for _ in range(to.remaining):
            if t > now:
                break
            events.append((t, "train", to))
            t += to.per_unit
    events.sort(key=lambda e: e[0])

    cursor = v.updated_at
    for t, kind, payload in events:
        _accumulate(v, cursor, t)
        cursor = t
        if kind == "build":
            v.slots[payload.slot_index].level = payload.target_level
        else:  # train : une unité sort
            v.troops[payload.unit_index] += 1
            payload.remaining -= 1
            payload.next_finish += payload.per_unit

    _accumulate(v, cursor, now)
    v.queue = [o for o in v.queue if o.finish_at > now]
    v.training = [to for to in v.training if to.remaining > 0]
    v.updated_at = now


def _accumulate(v: Village, t0: float, t1: float) -> None:
    if t1 <= t0:
        return
    # Vitesse serveur : le temps s'écoule `server_speed` fois plus vite
    # (production ×N, durées ÷N) — équivaut à accélérer le temps.
    hours = (t1 - t0) / 3600.0 * v.server_speed
    prod = net_production(v)
    caps = capacities(v)
    for i in (WOOD, CLAY, IRON):
        v.resources[i] = min(caps[i], max(0.0, v.resources[i] + prod[i] * hours))

    # Céréales : si le grenier se vide alors que la production nette reste
    # négative, les troupes meurent de faim (cf. _starve).
    crop = v.resources[CROP] + prod[CROP] * hours
    if crop >= 0.0:
        v.resources[CROP] = min(caps[CROP], crop)
    else:
        v.resources[CROP] = 0.0
        _starve(v)


def _starve(v: Village) -> None:
    """Famine : grenier vide + production de blé négative → des troupes meurent.

    Modèle (approximation fidèle de Travian, dont la distribution exacte des
    pertes n'est pas publique) : on retire des unités jusqu'à ce que la
    production nette de blé repasse ≥ 0, en sacrifiant en priorité le type qui
    pèse le plus sur l'entretien. Si même sans aucune troupe le bilan reste
    négatif (population seule), toutes les troupes meurent et le blé reste à 0.
    """
    units = UNITS[v.tribe]
    # Blé disponible pour les troupes une fois la population nourrie (peut être < 0).
    # `troop_upkeep` inclut les troupes en déplacement, mais on ne peut sacrifier
    # ici que la garnison (`troops`) : les armées en vol ne sont retranchées qu'à
    # leur retour (sinon le décompte du mouvement serait désynchronisé). Elles
    # restent donc exposées à la famine une fois rentrées.
    surplus = gross_production(v)[CROP] - population(v)
    target = max(0.0, surplus)
    while troop_upkeep(v) > target and any(v.troops):
        idx = max((i for i, n in enumerate(v.troops) if n > 0),
                  key=lambda i: v.troops[i] * units[i].upkeep)
        v.troops[idx] -= 1


# --- Construction ------------------------------------------------------------
def build_time(v: Village, building: Building, target_level: int) -> float:
    """Temps de construction effectif (s) : base × réduction BP ÷ vitesse serveur."""
    mb_level = max((s.level for s in v.slots.values()
                    if s.building_id == B.MAIN_BUILDING), default=0)
    mb_factor = BLD.get(B.MAIN_BUILDING).benefit(mb_level) if mb_level > 0 else 1.0
    return building.time_at(target_level) * mb_factor / v.server_speed


class BuildError(Exception):
    pass


def enqueue_build(v: Village, slot_index: int, now: float | None = None) -> BuildOrder:
    """Met en file la montée d'un niveau de l'emplacement `slot_index`."""
    now = now or _time.time()
    tick(v, now)

    if len([o for o in v.queue if True]) >= v.max_queue:
        raise BuildError("File de construction pleine.")
    if slot_index in (o.slot_index for o in v.queue):
        raise BuildError("Cet emplacement est déjà en construction.")

    slot = v.slots.get(slot_index)
    if slot is None:
        raise BuildError("Emplacement vide (pose de bâtiment non gérée en Phase 1).")

    building = BLD.get(slot.building_id)
    target = slot.level + 1
    if target > building.max_level:
        raise BuildError("Niveau maximum atteint.")

    levels = building_levels(v)
    for bid, lvl in building.reqs.items():
        if levels.get(bid, 0) < lvl:
            raise BuildError(f"Prérequis manquant : {BLD.get(bid).name} niv {lvl}.")

    cost = building.cost_at(target)
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")

    for i in range(4):
        v.resources[i] -= cost[i]

    order = BuildOrder(slot_index=slot_index, target_level=target,
                       finish_at=now + build_time(v, building, target))
    v.queue.append(order)
    return order


# --- Bâtiments constructibles sur un emplacement -----------------------------
def available_buildings(v: Village, slot_index: int) -> list[Building]:
    """Bâtiments qu'on peut poser sur l'emplacement `slot_index` (vide)."""
    if slot_index in v.slots:
        return []

    if slot_index == WALL_SLOT:
        wall = next((b for b in BLD.BUILDINGS.values()
                     if b.slot == "wall" and b.tribe == v.tribe), None)
        return [wall] if wall else []

    if slot_index not in CENTER_SLOTS:
        return []

    levels = building_levels(v)
    present = {s.building_id for s in v.slots.values()}
    out = []
    for b in BLD.BUILDINGS.values():
        if b.slot != "village":
            continue
        if b.tribe is not None and b.tribe != v.tribe:
            continue
        if b.capital_only and not v.is_capital:
            continue
        if b.non_capital and v.is_capital:
            continue
        if b.id in present and not b.multi:
            continue
        if any(levels.get(bid, 0) < lvl for bid, lvl in b.reqs.items()):
            continue
        out.append(b)
    return out


def enqueue_new_building(v: Village, slot_index: int, building_id: int,
                         now: float | None = None) -> BuildOrder:
    """Pose un nouveau bâtiment (niveau 0 → 1) sur un emplacement vide."""
    now = now or _time.time()
    tick(v, now)
    if slot_index in v.slots:
        raise BuildError("Emplacement déjà occupé.")
    if building_id not in (b.id for b in available_buildings(v, slot_index)):
        raise BuildError("Bâtiment non constructible ici.")

    v.slots[slot_index] = Slot(building_id=building_id, level=0)
    try:
        return enqueue_build(v, slot_index, now)
    except BuildError:
        del v.slots[slot_index]  # annuler la pose si l'ordre échoue
        raise


# --- Entraînement de troupes -------------------------------------------------
def trainable_units(v: Village, building_id: int) -> list[tuple[int, Unit]]:
    """Unités productibles dans `building_id` (bâtiment présent niv ≥ 1)."""
    if building_levels(v).get(building_id, 0) < 1:
        return []
    return [(i, u) for i, u in enumerate(UNITS[v.tribe]) if u.producer == building_id]


def _building_free_at(v: Village, building_id: int, now: float) -> float:
    """Date à laquelle le bâtiment d'entraînement aura fini sa file actuelle."""
    free = now
    for to in v.training:
        if to.building_id == building_id:
            free = max(free, to.next_finish + (to.remaining - 1) * to.per_unit)
    return free


def enqueue_training(v: Village, building_id: int, unit_index: int, count: int,
                     now: float | None = None) -> TrainOrder:
    now = now or _time.time()
    tick(v, now)
    if count < 1:
        raise BuildError("Nombre invalide.")

    level = building_levels(v).get(building_id, 0)
    if level < 1:
        raise BuildError("Bâtiment d'entraînement absent.")
    units = UNITS[v.tribe]
    if not (0 <= unit_index < len(units)) or units[unit_index].producer != building_id:
        raise BuildError("Cette unité ne se forme pas ici.")

    unit = units[unit_index]
    cost = [unit.cost[i] * count for i in range(4)]
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")
    for i in range(4):
        v.resources[i] -= cost[i]

    per_unit = unit.train_time * BLD.get(building_id).benefit(level) / v.server_speed
    free = _building_free_at(v, building_id, now)
    order = TrainOrder(building_id=building_id, unit_index=unit_index,
                       remaining=count, per_unit=per_unit, next_finish=free + per_unit)
    v.training.append(order)
    return order


# --- Village de départ standard (4-4-4-6) -----------------------------------
def new_village(name: str, tribe: Tribe, server_speed: int = 1,
                x: int = 0, y: int = 0, player_id: int | None = None,
                is_capital: bool = True) -> Village:
    slots: dict[int, Slot] = {}
    layout = ([B.WOODCUTTER] * 4 + [B.CLAYPIT] * 4 + [B.IRONMINE] * 4 + [B.CROPLAND] * 6)
    for i, bid in enumerate(layout, start=1):
        slots[i] = Slot(building_id=bid, level=0)
    # Centre du village : bâtiment principal niv 1, place de rassemblement niv 1
    slots[19] = Slot(building_id=B.MAIN_BUILDING, level=1)
    slots[RALLY_SLOT] = Slot(building_id=B.RALLY_POINT, level=1)
    return Village(name=name, tribe=tribe, slots=slots, server_speed=server_speed,
                   x=x, y=y, player_id=player_id, is_capital=is_capital)
