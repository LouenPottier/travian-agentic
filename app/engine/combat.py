"""Moteur de combat T4.6 — portage fidèle de la cascade combat Kirilloid (base→t3→t4).

Reproduit la formule exacte de Travian : points off (infanterie/cavalerie),
défense « adduite » selon la composition de l'attaque, bonus de moral, exposant
d'immensité variable, bonus de muraille, béliers (démolition de mur) et catapultes
(démolition de bâtiment), avec la formule d'amélioration de forge T4.

Les pièges gaulois (capture pré-combat des assaillants) sont appliqués en amont,
côté movement.py (cf. village.distribute_traps / add_prisoners), pas ici.

L'espionnage (reconnaissance) est traité à part dans `engine.scouting` (éclaireurs
seuls, hors de cette formule de bataille). Non encore géré ici : fête (party),
métallurgie, bière teutonne (brew).

Vecteurs de validation (cf. tests) issus de t4/combat/combat.spec.ts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.data.formulas import round_p
from app.data.units import Unit

round_stat = round_p(1e-4)     # arrondi des stats (upgrade)
round_pct = round_p(1e-4)      # part infanterie/cavalerie
round_morale = round_p(1e-3)
round_imm = round_p(0.0002)
round_siege = round_p(0.005)

BASE_VILLAGE_DEF = 10


# --- Amélioration en forge (formule T4) -------------------------------------
def upgrade(stat: float, level: int, upkeep_u: int) -> float:
    """Valeur d'une stat améliorée au niveau `level` (forge T4)."""
    upkeep = upkeep_u / 1.007
    return round_stat(stat
                      + (stat + 300 * upkeep / 7) * (1.007 ** level - 1)
                      + upkeep * 0.0021)


# --- Points de combat (vecteur infanterie/cavalerie) ------------------------
@dataclass
class Side:
    units: list[Unit]
    numbers: list[int]
    upgrades: list[int] = field(default_factory=lambda: [0] * 10)

    def total(self) -> int:
        return sum(self.numbers)


@dataclass
class Off(Side):
    pop: int = 100
    kind: str = "attack"          # "attack" | "raid"
    targets: list[int] = field(default_factory=list)  # niveaux des bâtiments visés
    # Héros accompagnant l'attaque (cf. engine.hero) : force de combat plate ajoutée
    # à l'infanterie offensive, et bonus d'attaque (% multiplicatif). Défauts neutres.
    hero_power: float = 0.0
    bonus: float = 0.0            # bonus d'attaque du héros (fraction, ex. 0.20 = +20 %)

    def off_points(self) -> list[float]:
        """[points infanterie, points cavalerie] de l'attaque."""
        inf = cav = 0.0
        for u, n, up in zip(self.units, self.numbers, self.upgrades):
            pts = n * upgrade(u.attack, up, u.upkeep)
            if u.infantry:
                inf += pts
            else:
                cav += pts
        inf += self.hero_power  # le héros combat comme de l'infanterie
        return [inf, cav]

    def _slot(self, predicate):
        for i in range(min(10, len(self.units))):
            if predicate(self.units[i]):
                return self.numbers[i], self.upgrades[i]
        return 0, 0

    def rams(self):
        return self._slot(lambda u: u.is_ram)

    def cats(self):
        return self._slot(lambda u: u.is_catapult)


@dataclass
class Defender(Side):
    def def_points(self) -> list[float]:
        inf = cav = 0.0
        for u, n, up in zip(self.units, self.numbers, self.upgrades):
            inf += n * upgrade(u.def_inf, up, u.upkeep)
            cav += n * upgrade(u.def_cav, up, u.upkeep)
        return [inf, cav]


@dataclass
class Place:
    tribe: int = 0
    pop: int = 100
    dur_bonus: float = 1.0          # durabilité (tailleur de pierre)
    wall_level: int = 0
    wall_durability: float = 1.0
    wall_bonus: callable = lambda lvl: {"def_bonus": 0.0}  # buildings.wall4(...)
    def_extra: float = 0.0          # défense plate additionnelle (héros en défense inclus)
    def_bonus_extra: float = 0.0    # bonus de défense multiplicatif (héros défenseur)


@dataclass
class CombatResult:
    off_losses: float = 0.0
    def_losses: float = 0.0
    wall: int = 0
    buildings: list[int] = field(default_factory=list)


# --- Fonctions de combat (fns) ----------------------------------------------
def morale(off_pop: int, def_pop: int, pts_ratio: float = 1.0) -> float:
    if off_pop <= def_pop:
        return 1.0
    pop_ratio = off_pop / max(def_pop, 3)
    return max(0.667, round_morale(pop_ratio ** (-0.2 * min(pts_ratio, 1.0))))


def cata_morale(off_pop: int, def_pop: int) -> float:
    """Moral des catapultes (t3) : clamp((offPop/defPop)^-0.3, 0.3333, 1)."""
    return min(1.0, max(0.3333, (off_pop / def_pop) ** -0.3))


def immensity(total_troops: int) -> float:
    """Exposant d'immensité (t3) : clamp(3.7184 - 2·n^0.015, 1.2578, 1.5)."""
    raw = 3.7184 - 2 * total_troops ** 0.015
    return round_imm(min(1.5, max(1.2578, raw)))


def adduced_def(off_pts, def_pts):
    """Défense effective selon la composition (infanterie/cavalerie) de l'attaque."""
    total_off = off_pts[0] + off_pts[1]
    inf_part = round_pct(off_pts[0] / total_off)
    cav_part = round_pct(off_pts[1] / total_off)
    total_def = def_pts[0] * inf_part + def_pts[1] * cav_part
    return total_off, total_def


def sigma(x: float) -> float:
    return (2 - x ** -1.5 if x > 1 else x ** 1.5) / 2


def siege_upgrade(level: int) -> float:
    return round_siege(1.0205 ** level)


def demolish_points(catas, upg_lvl, durability, pts_ratio, m=1.0):
    eff = math.floor(catas / durability) * m
    return 4 * sigma(pts_ratio) * eff * siege_upgrade(upg_lvl)


def demolish(level: int, damage: float) -> int:
    """Niveau de bâtiment restant après `damage` points de démolition."""
    damage -= 0.5
    if damage < 0:
        return level
    while damage >= level and level:
        damage -= level
        level -= 1
    return level


# table de la phase « early ram » : earlyRamTable[niveau][n]
def _build_early_ram_table():
    table = []
    for lvl in range(21):
        row = []
        l = 0
        while l <= lvl / 2:
            row.append(-2 * l ** 2 + (2 * lvl + 1) * l)
            l += 1
        base = lvl * (lvl + 1) / 2 + 20
        while l <= lvl:
            dl = l - (lvl // 2) - 1
            row.append(1.25 * dl ** 2 + 49.75 * dl + base)
            l += 1
        row.append(1e9)
        table.append(row)
    return table


_EARLY_RAM = _build_early_ram_table()


def demolish_wall(tribe_dur: float, level: int, points: float) -> int:
    row = _EARLY_RAM[level]
    dem = 0
    while int(tribe_dur * row[dem + 1]) <= points:
        dem += 1
    return level - dem


# --- Bataille (une vague : un attaquant contre les défenseurs) --------------
def combat(place: Place, off: Off, defs: list[Defender]) -> CombatResult:
    result = CombatResult(wall=place.wall_level)

    off_pts = off.off_points()
    def_total = [0.0, 0.0]
    for d in defs:
        dp = d.def_points()
        def_total[0] += dp[0]
        def_total[1] += dp[1]

    base_off, base_def = adduced_def(off_pts, def_total)
    base_off *= 1 + off.bonus  # bonus d'attaque du héros (neutre si 0)
    total_troops = off.total() + sum(d.total() for d in defs)
    imm = immensity(total_troops)

    def def_bonus():
        return ((1 + place.wall_bonus(place.wall_level).get("def_bonus", 0.0))
                * (1 + place.def_bonus_extra))

    def def_absolute():
        return (BASE_VILLAGE_DEF + place.def_extra
                + place.wall_bonus(place.wall_level).get("def", 0.0))

    def final_def():
        return (base_def + def_absolute()) * def_bonus()

    def final_off():
        m = morale(off.pop, place.pop, base_off / final_def())
        return base_off * m

    fdef = final_def()
    foff = final_off()
    ratio = foff / fdef

    # Béliers : démolition de la muraille (affecte le bonus défensif)
    rams_n, ram_up = off.rams()
    if rams_n and place.wall_level:
        pts = demolish_points(rams_n, ram_up, place.dur_bonus, ratio)
        # niveau de mur effectif pendant la bataille
        eff_wall = demolish_wall(place.wall_durability, place.wall_level, pts)
        saved = place.wall_level
        place.wall_level = eff_wall
        fdef = final_def()
        foff = final_off()
        ratio = foff / fdef
        place.wall_level = saved
        result.wall = demolish(place.wall_level,
                               demolish_points(rams_n, ram_up, place.dur_bonus, ratio))

    x = ratio ** imm

    if off.kind == "raid":
        result.off_losses = 1 / (1 + x)
        result.def_losses = x / (1 + x)
    else:
        result.off_losses = min(1 / x, 1.0)
        result.def_losses = min(x, 1.0)

    # Catapultes : démolition de bâtiments
    if off.targets:
        cats_n, cat_up = off.cats()
        m = cata_morale(off.pop, place.pop)
        pts = demolish_points(cats_n / len(off.targets), cat_up,
                              place.dur_bonus, ratio, m)
        result.buildings = [demolish(b, pts) for b in off.targets]

    # Attaquant solitaire trop faible : meurt
    if off.total() == 1:
        o = off_pts[0] + off_pts[1]
        if o * morale(off.pop, place.pop, 1.0) < 84.5:
            result.off_losses = 1.0

    return result
