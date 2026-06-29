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
from app.data import formulas as F
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
class ResearchOrder:
    """Recherche d'une unité en académie : une complétion unique (research[idx]=1)."""
    unit_index: int
    finish_at: float


@dataclass
class UpgradeOrder:
    """Amélioration d'une unité en forge : porte upgrades[idx] à `target_level`."""
    unit_index: int
    target_level: int
    finish_at: float


@dataclass
class TrapOrder:
    """Construction de pièges (trappeur gaulois), à la chaîne comme l'entraînement."""
    remaining: int
    per_unit: float
    next_finish: float


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
    # Académie : recherche débloquant l'entraînement (1 = recherchée), par index d'unité.
    research: list[int] = field(default_factory=lambda: [0] * 10)
    research_queue: list[ResearchOrder] = field(default_factory=list)
    # Forge : niveau d'amélioration (attaque/défense) par index d'unité (0..niveau forge).
    upgrades: list[int] = field(default_factory=lambda: [0] * 10)
    upgrade_queue: list[UpgradeOrder] = field(default_factory=list)
    # Trappeur (Gaulois) : nombre de pièges construits + file de construction.
    traps: int = 0
    trap_queue: list[TrapOrder] = field(default_factory=list)
    # Oasis annexées (manoir du héros) : leur bonus de production est crédité à ce
    # village. Chaque entrée : {"x", "y", "code"} (code de bonus de la case, cf.
    # data/world). Le nombre maximal dépend du niveau du manoir (cf. engine.oasis).
    oases: list[dict] = field(default_factory=list)
    # Prisonniers (pièges du trappeur gaulois) : assaillants capturés, retenus ici.
    # Chaque entrée : {"player_id", "village_id", "tribe", "units": [10]}. Chaque
    # unité retenue occupe un piège ; un groupe est libérable (retour au propriétaire).
    prisoners: list[dict] = field(default_factory=list)
    # Loyauté (0..100) : cible de la conquête par chef/sénateur. Régénère au passage
    # du temps (+⅔ × niveau du bâtiment d'administration / h, cf. engine.conquest).
    loyalty: float = 100.0
    # Célébration en cours à l'hôtel de ville (cf. engine.celebration) : None ou
    # {"type": 1|2, "ends_at": float, "cp": int}. Les points de culture sont crédités
    # paresseusement à la fin (récolte par celebration.harvest_completed).
    celebration: dict | None = None
    # Fête de la bière en cours à la brasserie (Teutons, capitale ; cf. engine.brewery) :
    # None ou {"ends_at": float}. Tant qu'elle est active, +1 %/niveau de brasserie à
    # l'attaque de toutes les troupes du compte (account-wide).
    brewery_festival: dict | None = None


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


def admin_building_level(v: Village) -> int:
    """Niveau du bâtiment d'administration (résidence/palais) — pilote la régén de
    loyauté et protège contre la conquête tant qu'il est présent (cf. engine.conquest)."""
    levels = building_levels(v)
    return max(levels.get(B.RESIDENCE, 0), levels.get(B.PALACE, 0))


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

    # Bonus des oasis annexées : % de la production de base du type, **additif**
    # avec les bâtiments de raffinage (fidélité Travian : prod = base × (1 + raffinage%
    # + oasis%)). Calculé depuis les codes d'oasis stockés sur le village.
    from app.engine import world as W
    oasis_pct = [0.0, 0.0, 0.0, 0.0]
    for o in v.oases:
        for res, pct in W.oasis_bonus(o["code"]).items():
            oasis_pct[res] += pct / 100.0

    prod[WOOD] *= 1 + bonus(B.SAWMILL) + oasis_pct[WOOD]
    prod[CLAY] *= 1 + bonus(B.BRICKYARD) + oasis_pct[CLAY]
    prod[IRON] *= 1 + bonus(B.IRONFOUNDRY) + oasis_pct[IRON]
    prod[CROP] *= 1 + bonus(B.GRAINMILL) + bonus(B.BAKERY) + oasis_pct[CROP]
    return prod


# --- Abreuvoir (Romains) -----------------------------------------------------
# Vrai T4.6 (kirilloid muet ; recoupé support.travian.com « Reducing crop consumption
# & Horse Drinking Trough » / unofficialtravian) : l'abreuvoir réduit de **−1 céréale/h**
# l'entretien de chaque cavalier romain au passage de paliers (Equites Legati niv 10,
# Imperatoris niv 15, Caesaris niv 20) et accélère l'entraînement de la cavalerie de
# **−1 %/niveau**. Effets locaux au village qui possède l'abreuvoir.
HORSE_POOL_CROP_THRESHOLDS = {3: 10, 4: 15, 5: 20}  # index unité romaine → niv requis


def unit_upkeep(v: Village, unit_index: int) -> int:
    """Entretien (céréales/h) d'une unité dans `v`, abreuvoir romain déduit (min 1)."""
    base = UNITS[v.tribe][unit_index].upkeep
    if v.tribe == Tribe.ROMANS:
        need = HORSE_POOL_CROP_THRESHOLDS.get(unit_index)
        if need is not None and building_levels(v).get(B.HORSE_POOL, 0) >= need:
            return max(1, base - 1)
    return base


def horse_pool_train_factor(v: Village) -> float:
    """Facteur multiplicatif de réduction du temps d'entraînement de la cavalerie
    (abreuvoir romain) : −1 %/niveau. 1,0 hors Romains / sans abreuvoir."""
    if v.tribe != Tribe.ROMANS:
        return 1.0
    return 1.0 - 0.01 * building_levels(v).get(B.HORSE_POOL, 0)


def troop_upkeep(v: Village) -> int:
    """Consommation de céréales : troupes stationnées + troupes en déplacement.

    Les armées en vol (raid/attaque/renfort à l'aller, survivants au retour)
    mangent toujours le blé de leur village d'origine, comme dans le vrai Travian.
    Les renforts qui ont atteint leur cible sont stationnés (`troops`) dans la
    cible et y consomment donc le blé : on ne les compte plus ici.
    """
    return sum((v.troops[i] + v.away[i]) * unit_upkeep(v, i) for i in range(len(v.troops)))


def net_production(v: Village) -> list[float]:
    """Production nette horaire ; les céréales sont diminuées de la population et des troupes."""
    prod = gross_production(v)
    prod[CROP] -= population(v) + troop_upkeep(v)
    return prod


# --- Capacité de stockage ----------------------------------------------------
def _storage(v: Village, *building_ids: int) -> int:
    """Capacité totale de stockage : somme de tous les entrepôts/greniers du type
    demandé. Le **grand entrepôt** (GREAT_WAREHOUSE) et le **grand grenier**
    (GREAT_GRANARY), hors capitale, s'additionnent à l'entrepôt/grenier ordinaire
    (chacun avec sa propre capacité = 3× l'ordinaire, cf. F.great_capacity)."""
    caps = [BLD.get(s.building_id).benefit(s.level)
            for s in v.slots.values()
            if s.building_id in building_ids and s.level > 0]
    return sum(caps) if caps else BASE_STORAGE


def warehouse_capacity(v: Village) -> int:
    return _storage(v, B.WAREHOUSE, B.GREAT_WAREHOUSE)


def granary_capacity(v: Village) -> int:
    return _storage(v, B.GRANARY, B.GREAT_GRANARY)


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

    # Collecte des événements <= now : construction, entraînement, recherche
    # (académie), amélioration (forge) et pièges (trappeur). Chacun peut modifier
    # la production (champ amélioré, troupe de plus à nourrir), d'où le découpage.
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
    for ro in v.research_queue:
        if ro.finish_at <= now:
            events.append((ro.finish_at, "research", ro))
    for uo in v.upgrade_queue:
        if uo.finish_at <= now:
            events.append((uo.finish_at, "upgrade", uo))
    for tp in v.trap_queue:
        t = tp.next_finish
        for _ in range(tp.remaining):
            if t > now:
                break
            events.append((t, "trap", tp))
            t += tp.per_unit
    events.sort(key=lambda e: e[0])

    cursor = v.updated_at
    for t, kind, payload in events:
        _accumulate(v, cursor, t)
        cursor = t
        if kind == "build":
            v.slots[payload.slot_index].level = payload.target_level
        elif kind == "train":  # une unité sort
            v.troops[payload.unit_index] += 1
            payload.remaining -= 1
            payload.next_finish += payload.per_unit
        elif kind == "research":
            v.research[payload.unit_index] = 1
        elif kind == "upgrade":
            v.upgrades[payload.unit_index] = payload.target_level
        else:  # trap : un piège construit
            v.traps += 1
            payload.remaining -= 1
            payload.next_finish += payload.per_unit

    _accumulate(v, cursor, now)
    v.queue = [o for o in v.queue if o.finish_at > now]
    v.training = [to for to in v.training if to.remaining > 0]
    v.research_queue = [ro for ro in v.research_queue if ro.finish_at > now]
    v.upgrade_queue = [uo for uo in v.upgrade_queue if uo.finish_at > now]
    v.trap_queue = [tp for tp in v.trap_queue if tp.remaining > 0]
    v.updated_at = now


def _accumulate(v: Village, t0: float, t1: float) -> None:
    if t1 <= t0:
        return
    # Vitesse serveur : le temps s'écoule `server_speed` fois plus vite
    # (production ×N, durées ÷N) — équivaut à accélérer le temps.
    hours = (t1 - t0) / 3600.0 * v.server_speed

    # Loyauté : régén +⅔ × niveau du bâtiment d'administration / h (vrai Travian,
    # cf. engine.conquest). Sans résidence/palais (ex. après conquête : détruit),
    # aucune régén ⇒ le village reste vulnérable jusqu'à reconstruction.
    if v.loyalty < 100.0:
        admin = admin_building_level(v)
        if admin > 0:
            v.loyalty = min(100.0, v.loyalty + (2.0 / 3.0) * admin * hours)

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
    # Villages PNJ (Nature/Natars) : garnison **statique**, jamais affamée (elle ne se
    # nourrit pas dans ce modèle ; comme la garnison d'oasis, cf. CLAUDE.md). Sans ça la
    # grosse garnison Natar fondrait à chaque passage du temps (×vitesse serveur).
    from app.data.tribes import NPC_TRIBES
    if v.tribe in NPC_TRIBES:
        return
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


# Vrai Travian : seuls les champs de ressources de la **capitale** dépassent le
# niveau 10 (jusqu'à leur max_level 20/21). Hors capitale, ils plafonnent à 10.
# Cf. support.travian.com — « Capital Village » : « the capital village is the only
# village where you can upgrade resource fields above level 10 ».
FIELD_CAP_NON_CAPITAL = 10


def effective_max_level(v: Village, building: Building) -> int:
    """Niveau maximum atteignable de `building` dans `v` (cf. FIELD_CAP_NON_CAPITAL)."""
    if building.slot == "res" and not v.is_capital:
        return min(FIELD_CAP_NON_CAPITAL, building.max_level)
    return building.max_level


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
    if target > effective_max_level(v, building):
        if building.slot == "res" and not v.is_capital:
            raise BuildError("Hors capitale, les champs sont limités au niveau 10.")
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
def available_buildings(v: Village, slot_index: int,
                        account_has_palace: bool = False) -> list[Building]:
    """Bâtiments qu'on peut poser sur l'emplacement `slot_index` (vide).

    `account_has_palace` : True si le joueur possède déjà un palais (dans n'importe
    quel village) — le palais est alors masqué (vrai Travian : **un seul palais par
    compte de jeu**, cf. support.travian.com)."""
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
        # Palais ⇄ résidence : mutuellement exclusifs dans un même village (vrai
        # Travian, support.travian.com). Et un seul palais sur tout le compte.
        if b.id == B.PALACE and (B.RESIDENCE in present or account_has_palace):
            continue
        if b.id == B.RESIDENCE and B.PALACE in present:
            continue
        if any(levels.get(bid, 0) < lvl for bid, lvl in b.reqs.items()):
            continue
        out.append(b)
    return out


def enqueue_new_building(v: Village, slot_index: int, building_id: int,
                         now: float | None = None,
                         account_has_palace: bool = False) -> BuildOrder:
    """Pose un nouveau bâtiment (niveau 0 → 1) sur un emplacement vide."""
    now = now or _time.time()
    tick(v, now)
    if slot_index in v.slots:
        raise BuildError("Emplacement déjà occupé.")
    if building_id not in (b.id for b in available_buildings(v, slot_index,
                                                             account_has_palace)):
        raise BuildError("Bâtiment non constructible ici.")

    v.slots[slot_index] = Slot(building_id=building_id, level=0)
    try:
        return enqueue_build(v, slot_index, now)
    except BuildError:
        del v.slots[slot_index]  # annuler la pose si l'ordre échoue
        raise


# --- Entraînement de troupes -------------------------------------------------
# Grande caserne / grande écurie : forment les **mêmes** unités que la caserne /
# écurie de base, mais à coût ×3 (fidélité vrai Travian ; kirilloid ne chiffre pas
# le ×3), via leur propre file et leur propre niveau (réduction de temps indépendante,
# d'où l'entraînement en parallèle ⇒ production doublée).
GREAT_TRAINERS = {B.GREAT_BARRACKS: B.BARRACKS, B.GREAT_STABLES: B.STABLES}
GREAT_COST_MULT = 3

# Colons & chefs (administrateurs) : dans units.py leur `producer` est la résidence,
# mais le vrai Travian impose des règles distinctes (kirilloid ne chiffre pas ces unités) :
#  - **colons** : formables en résidence OU palais, à partir du **niveau 10** (le niveau
#    qui débloque le 1ᵉʳ emplacement d'expansion — cf. F.slots2/slots3, expansion.py) ;
#  - **chefs/sénateurs** : formables **uniquement au palais** (jamais à la résidence),
#    à partir du niveau 10. La résidence sert à fonder des villages, pas à les conquérir.
SETTLER_TRAINERS = (B.RESIDENCE, B.PALACE)
CHIEF_TRAINERS = (B.PALACE,)
EXPANSION_MIN_LEVEL = 10

# Seuls caserne/écurie/atelier (et leurs « grandes » variantes) réduisent le temps
# d'entraînement (benefit = train_bonus). Résidence/palais : pas de réduction (leur
# benefit renvoie un dict de slots, pas un facteur) → facteur 1,0.
TRAIN_BONUS_BUILDINGS = (B.BARRACKS, B.STABLES, B.WORKSHOP,
                         B.GREAT_BARRACKS, B.GREAT_STABLES)


def base_producer(building_id: int) -> int:
    """Bâtiment dont les unités sont formées par `building_id` (grande caserne →
    caserne, grande écurie → écurie). Pour un bâtiment normal : lui-même."""
    return GREAT_TRAINERS.get(building_id, building_id)


def _expansion_trainers(u: Unit) -> tuple | None:
    """Bâtiments où une unité d'expansion (colon/chef) se forme, ou None si l'unité
    n'en est pas une (troupe militaire ordinaire)."""
    if u.is_chief:
        return CHIEF_TRAINERS
    if u.is_settler:
        return SETTLER_TRAINERS
    return None


def train_time_factor(building_id: int, level: int) -> float:
    """Facteur de réduction du temps d'entraînement. Caserne/écurie/atelier (et grandes
    variantes) : `train_bonus` (0,9**(niv−1)). Résidence/palais : 1,0 (pas de réduction)."""
    if building_id in TRAIN_BONUS_BUILDINGS:
        return BLD.get(building_id).benefit(level)
    return 1.0


def trainable_units(v: Village, building_id: int) -> list[tuple[int, Unit]]:
    """Unités productibles dans `building_id` (bâtiment présent au niveau requis).

    Colons/chefs : résidence/palais selon le type, et seulement à partir du niveau 10
    (vrai Travian). Troupes militaires : caserne/écurie/atelier (+ grandes variantes)."""
    level = building_levels(v).get(building_id, 0)
    if level < 1:
        return []
    prod = base_producer(building_id)
    out = []
    for i, u in enumerate(UNITS[v.tribe]):
        trainers = _expansion_trainers(u)
        if trainers is not None:
            if building_id in trainers and level >= EXPANSION_MIN_LEVEL:
                out.append((i, u))
        elif u.producer == prod:
            out.append((i, u))
    return out


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
    if not (0 <= unit_index < len(units)):
        raise BuildError("Cette unité ne se forme pas ici.")
    unit = units[unit_index]
    trainers = _expansion_trainers(unit)
    if trainers is not None:
        # Colon/chef : résidence ou palais selon le type, niveau 10+ (vrai Travian).
        if building_id not in trainers:
            raise BuildError("Cette unité ne se forme pas ici.")
        if level < EXPANSION_MIN_LEVEL:
            kind = "Le chef" if unit.is_chief else "Le colon"
            raise BuildError(f"{kind} requiert {BLD.get(building_id).name} "
                             f"niveau {EXPANSION_MIN_LEVEL}.")
    elif unit.producer != base_producer(building_id):
        raise BuildError("Cette unité ne se forme pas ici.")
    if needs_research(v, unit_index) and not v.research[unit_index]:
        raise BuildError(f"{unit.name} : recherche en académie requise.")

    mult = GREAT_COST_MULT if building_id in GREAT_TRAINERS else 1
    cost = [unit.cost[i] * count * mult for i in range(4)]
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")
    for i in range(4):
        v.resources[i] -= cost[i]

    per_unit = unit.train_time * train_time_factor(building_id, level) / v.server_speed
    if not unit.infantry:  # abreuvoir romain : −1 %/niveau sur la cavalerie
        per_unit *= horse_pool_train_factor(v)
    free = _building_free_at(v, building_id, now)
    order = TrainOrder(building_id=building_id, unit_index=unit_index,
                       remaining=count, per_unit=per_unit, next_finish=free + per_unit)
    v.training.append(order)
    return order


# --- Académie : recherche d'unités ------------------------------------------
# En T4, seule la 1ʳᵉ unité de la caserne (index 0) est disponible d'emblée ;
# toutes les autres unités militaires (caserne/écurie/atelier) doivent être
# recherchées en académie. Les unités de la résidence (colon/chef) n'utilisent
# pas l'académie. ⚠️ Kirilloid ne modélise PAS le coût de recherche : on prend le
# coût d'entraînement de l'unité (le temps, lui, vient de kirilloid : `research_time`).
RESEARCH_PRODUCERS = (B.BARRACKS, B.STABLES, B.WORKSHOP)


def needs_research(v: Village, unit_index: int) -> bool:
    u = UNITS[v.tribe][unit_index]
    return u.producer in RESEARCH_PRODUCERS and unit_index != 0


def is_researched(v: Village, unit_index: int) -> bool:
    return not needs_research(v, unit_index) or bool(v.research[unit_index])


def research_cost(v: Village, unit_index: int) -> tuple:
    return tuple(UNITS[v.tribe][unit_index].cost)


def research_time(v: Village, unit_index: int) -> float:
    """Temps de recherche effectif (s) : `research_time` kirilloid ÷ vitesse serveur."""
    return UNITS[v.tribe][unit_index].research_time / v.server_speed


def researchable_units(v: Village) -> list[tuple[int, Unit]]:
    """Unités recherchables : nécessitent une recherche et leur bâtiment producteur
    est déjà construit (on ne recherche que ce qu'on pourra entraîner)."""
    levels = building_levels(v)
    out = []
    for i, u in enumerate(UNITS[v.tribe]):
        if needs_research(v, i) and levels.get(u.producer, 0) >= 1:
            out.append((i, u))
    return out


def enqueue_research(v: Village, unit_index: int, now: float | None = None) -> ResearchOrder:
    now = now or _time.time()
    tick(v, now)
    if building_levels(v).get(B.ACADEMY, 0) < 1:
        raise BuildError("Académie requise pour rechercher.")
    units = UNITS[v.tribe]
    if not (0 <= unit_index < len(units)) or not needs_research(v, unit_index):
        raise BuildError("Cette unité ne se recherche pas.")
    if v.research[unit_index] or any(r.unit_index == unit_index for r in v.research_queue):
        raise BuildError("Recherche déjà effectuée ou en cours.")
    if building_levels(v).get(units[unit_index].producer, 0) < 1:
        raise BuildError("Bâtiment producteur de l'unité absent.")

    cost = research_cost(v, unit_index)
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")
    for i in range(4):
        v.resources[i] -= cost[i]
    # Files de recherche en parallèle (chaque académie traite une recherche à la fois ;
    # on enchaîne après la dernière en cours pour rester simple et déterministe).
    free = now + max((r.finish_at - now for r in v.research_queue), default=0.0)
    order = ResearchOrder(unit_index=unit_index, finish_at=free + research_time(v, unit_index))
    v.research_queue.append(order)
    return order


# --- Forge : amélioration des unités (attaque/défense) ----------------------
# La forge améliore les stats de combat (cf. engine.combat.upgrade). Le niveau
# d'amélioration d'une unité est plafonné par le niveau de la forge (règle Travian).
# ⚠️ Kirilloid ne modélise PAS le coût d'amélioration : approximation documentée =
# coût d'entraînement × niveau visé ; temps = research_time × niveau ÷ 5.
def smithy_level(v: Village) -> int:
    return building_levels(v).get(B.SMITHY, 0)


def upgrade_cost(v: Village, unit_index: int, target_level: int) -> tuple:
    return tuple(F.round5(UNITS[v.tribe][unit_index].cost[i] * target_level) for i in range(4))


def upgrade_time(v: Village, unit_index: int, target_level: int) -> float:
    return UNITS[v.tribe][unit_index].research_time * target_level / 5 / v.server_speed


def upgradable_units(v: Village) -> list[tuple[int, Unit]]:
    """Unités améliorables : unités de combat de la tribu dont le bâtiment producteur
    est construit (on n'améliore pas les colons)."""
    levels = building_levels(v)
    out = []
    for i, u in enumerate(UNITS[v.tribe]):
        if u.is_settler or u.producer < 0:
            continue
        if levels.get(u.producer, 0) >= 1:
            out.append((i, u))
    return out


def enqueue_upgrade(v: Village, unit_index: int, now: float | None = None) -> UpgradeOrder:
    now = now or _time.time()
    tick(v, now)
    level = smithy_level(v)
    if level < 1:
        raise BuildError("Forge requise pour améliorer les unités.")
    units = UNITS[v.tribe]
    if not (0 <= unit_index < len(units)) or units[unit_index].is_settler \
            or units[unit_index].producer < 0:
        raise BuildError("Cette unité ne s'améliore pas.")
    if any(u.unit_index == unit_index for u in v.upgrade_queue):
        raise BuildError("Amélioration déjà en cours pour cette unité.")
    target = v.upgrades[unit_index] + 1
    if target > level:
        raise BuildError(f"La forge doit être au niveau {target} pour cette amélioration.")
    if target > 20:
        raise BuildError("Amélioration maximale atteinte.")

    cost = upgrade_cost(v, unit_index, target)
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")
    for i in range(4):
        v.resources[i] -= cost[i]
    free = now + max((u.finish_at - now for u in v.upgrade_queue), default=0.0)
    order = UpgradeOrder(unit_index=unit_index, target_level=target,
                         finish_at=free + upgrade_time(v, unit_index, target))
    v.upgrade_queue.append(order)
    return order


# --- Trappeur (Gaulois) : construction de pièges ----------------------------
# Le trappeur peut détenir jusqu'à `trapper_traps(niveau)` pièges. ⚠️ Kirilloid ne
# modélise PAS le coût/temps des pièges : approximation documentée.
TRAP_COST = (30, 40, 20, 10)   # par piège (approximation, vrai Travian ≈ petit coût)
TRAP_TIME = 1000               # s par piège (vitesse 1), approximation


def trap_capacity(v: Village) -> int:
    lvl = building_levels(v).get(B.TRAPPER, 0)
    return F.trapper_traps(lvl) if lvl >= 1 else 0


def traps_pending(v: Village) -> int:
    return sum(tp.remaining for tp in v.trap_queue)


def traps_total(v: Village) -> int:
    """Pièges déjà construits + en cours de construction."""
    return v.traps + traps_pending(v)


def enqueue_traps(v: Village, count: int, now: float | None = None) -> TrapOrder:
    now = now or _time.time()
    tick(v, now)
    if count < 1:
        raise BuildError("Nombre invalide.")
    if building_levels(v).get(B.TRAPPER, 0) < 1:
        raise BuildError("Trappeur requis.")
    free_slots = trap_capacity(v) - traps_total(v)
    if count > free_slots:
        raise BuildError(f"Capacité dépassée ({free_slots} piège(s) possible(s)).")
    cost = [TRAP_COST[i] * count for i in range(4)]
    if any(v.resources[i] < cost[i] for i in range(4)):
        raise BuildError("Ressources insuffisantes.")
    for i in range(4):
        v.resources[i] -= cost[i]
    per_unit = TRAP_TIME / v.server_speed
    free = now + max((tp.next_finish - now + (tp.remaining - 1) * tp.per_unit
                      for tp in v.trap_queue), default=0.0)
    order = TrapOrder(remaining=count, per_unit=per_unit, next_finish=free + per_unit)
    v.trap_queue.append(order)
    return order


# --- Pièges en combat : capture & prisonniers -------------------------------
# Modèle de capture (fidèle au vrai Travian / référence TravianZ, kirilloid muet) :
# à l'attaque, les pièges retiennent jusqu'à `free_traps` assaillants AVANT la
# bataille (répartis au prorata des effectifs). Les capturés ne combattent pas et
# ne meurent pas : ils deviennent prisonniers du village défenseur (un piège occupé
# par unité). Le surplus livre bataille normalement.
def prisoners_count(v: Village) -> int:
    """Nombre total d'assaillants retenus prisonniers (un piège occupé par unité)."""
    return sum(sum(p["units"]) for p in v.prisoners)


def free_traps(v: Village) -> int:
    """Pièges disponibles pour capturer = pièges posés − prisonniers déjà retenus."""
    return max(0, v.traps - prisoners_count(v))


def distribute_traps(units: list[int], n: int) -> list[int]:
    """Répartit `n` captures au prorata des effectifs assaillants (plus grand reste),
    plafonné par l'effectif de chaque type. Renvoie le vecteur capturé (10 indices)."""
    total = sum(units)
    n = min(max(0, n), total)
    if n <= 0:
        return [0] * len(units)
    exact = [units[i] * n / total for i in range(len(units))]
    caught = [int(e) for e in exact]
    rem = n - sum(caught)
    order = sorted(range(len(units)), key=lambda i: exact[i] - caught[i], reverse=True)
    k = 0
    while rem > 0 and k < 100 * len(units):
        i = order[k % len(order)]
        if caught[i] < units[i]:
            caught[i] += 1
            rem -= 1
        k += 1
    return caught


def add_prisoners(v: Village, player_id: int, village_id: int,
                  tribe: int, units: list[int]) -> None:
    """Retient un groupe d'assaillants capturés, regroupé par village d'origine."""
    if sum(units) <= 0:
        return
    for p in v.prisoners:
        if p["village_id"] == village_id and p["player_id"] == player_id:
            for i in range(10):
                p["units"][i] += units[i]
            return
    v.prisoners.append({"player_id": player_id, "village_id": village_id,
                        "tribe": int(tribe), "units": list(units)})


def release_prisoner(v: Village, index: int) -> dict:
    """Retire et renvoie un groupe de prisonniers (la réintégration chez le
    propriétaire est faite par l'appelant, qui a accès au store)."""
    if not (0 <= index < len(v.prisoners)):
        raise BuildError("Prisonnier introuvable.")
    return v.prisoners.pop(index)


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
