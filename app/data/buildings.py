"""Bâtiments T4.6 — table résolue depuis la cascade Kirilloid (base→t3→t3.1→t3.5→t4).

Les valeurs numériques (coûts, temps, prérequis) proviennent du modèle T4 de
Kirilloid. Deux corrections par rapport à Kirilloid, pour coller au *vrai* Travian :
  - Scierie ↔ Briqueterie : Kirilloid intervertit leurs prérequis (Scierie exigeait
    la carrière d'argile au lieu du bûcheron). Rétabli ici.
Les prérequis ambigus sont marqués « TODO valider » et seront recoupés avec
travian.kirilloid.ru (cf. tâche Phase 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import formulas as F
from .tribes import Tribe


# --- Identifiants de bâtiments (mêmes IDs que Kirilloid) ---------------------
class B:
    WOODCUTTER = 0
    CLAYPIT = 1
    IRONMINE = 2
    CROPLAND = 3
    SAWMILL = 4
    BRICKYARD = 5
    IRONFOUNDRY = 6
    GRAINMILL = 7
    BAKERY = 8
    WAREHOUSE = 9
    GRANARY = 10
    # 11 = ancienne armurerie T3, supprimée en T4 (fusionnée dans la Forge)
    SMITHY = 12
    ARENA = 13
    MAIN_BUILDING = 14
    RALLY_POINT = 15
    MARKETPLACE = 16
    EMBASSY = 17
    BARRACKS = 18
    STABLES = 19
    WORKSHOP = 20
    ACADEMY = 21
    CRANNY = 22
    TOWNHALL = 23
    RESIDENCE = 24
    PALACE = 25
    TREASURY = 26
    TRADE_OFFICE = 27
    GREAT_BARRACKS = 28
    GREAT_STABLES = 29
    CITY_WALL = 30      # Romains
    EARTH_WALL = 31     # Teutons
    PALISADE = 32       # Gaulois
    STONEMASON = 33
    BREWERY = 34        # Teutons
    TRAPPER = 35        # Gaulois
    HERO_MANSION = 36
    GREAT_WAREHOUSE = 37
    GREAT_GRANARY = 38
    WORLD_WONDER = 39
    HORSE_POOL = 40     # Romains


@dataclass
class Building:
    id: int
    name: str
    cost: tuple              # coût de base [bois, argile, fer, céréales]
    k: float                 # multiplicateur de coût par niveau
    u: int                   # population de base (entretien)
    cp: int                  # points de culture de base
    _time: Callable[[int], float]   # temps de construction de base (s)
    benefit: Callable[[int], object]  # effet du bâtiment selon le niveau
    max_level: int = 20
    reqs: dict = field(default_factory=dict)  # {building_id: niveau requis}
    tribe: Optional[Tribe] = None             # restriction de tribu
    multi: bool = False                       # plusieurs exemplaires possibles
    capital_only: bool = False                # uniquement dans la capitale
    non_capital: bool = False                 # interdit dans la capitale
    slot: str = "village"                     # res | rally | wall | village

    def cost_at(self, level: int) -> tuple:
        return tuple(F.round5(c * self.k ** (level - 1)) for c in self.cost)

    def time_at(self, level: int) -> float:
        """Temps de construction de base (s), avant réduction BP et vitesse serveur."""
        return self._time(level)

    def upkeep_at(self, level: int) -> int:
        if level == 1:
            return self.u
        return F.js_round((5 * self.u + level - 1) / 10)

    def culture_at(self, level: int) -> int:
        return F.js_round(self.cp * 1.2 ** level)


def _b(**kw) -> Building:
    return Building(**kw)


# --- Table des bâtiments T4.6 -----------------------------------------------
# `t=(a, k, b)` -> formules.make_time ; champs de ressources : production *1.4 (prod4).
_BUILDINGS = [
    _b(id=B.WOODCUTTER, name="Bûcheron", cost=(40, 100, 50, 60), k=1.67, u=2, cp=1,
       _time=F.make_time(1780 / 3, 1.6, 1000 / 3), benefit=F.prod4, max_level=20, slot="res"),
    _b(id=B.CLAYPIT, name="Carrière d'argile", cost=(80, 40, 80, 50), k=1.67, u=2, cp=1,
       _time=F.make_time(1660 / 3, 1.6, 1000 / 3), benefit=F.prod4, max_level=21, slot="res"),
    _b(id=B.IRONMINE, name="Mine de fer", cost=(100, 80, 30, 60), k=1.67, u=3, cp=1,
       _time=F.make_time(2350 / 3, 1.6, 1000 / 3), benefit=F.prod4, max_level=20, slot="res"),
    _b(id=B.CROPLAND, name="Champ de céréales", cost=(70, 90, 70, 20), k=1.67, u=0, cp=1,
       _time=F.make_time(1450 / 3, 1.6, 1000 / 3), benefit=F.prod4, max_level=21, slot="res"),

    _b(id=B.SAWMILL, name="Scierie", cost=(520, 380, 290, 90), k=1.80, u=4, cp=1,
       _time=F.make_time(5400, 1.5, 2400), benefit=F.p5, max_level=5,
       reqs={B.WOODCUTTER: 10, B.MAIN_BUILDING: 5}),  # corrigé (Kirilloid: carrière)
    _b(id=B.BRICKYARD, name="Briqueterie", cost=(440, 480, 320, 50), k=1.80, u=3, cp=1,
       _time=F.make_time(5240, 1.5, 2400), benefit=F.p5, max_level=5,
       reqs={B.CLAYPIT: 10, B.MAIN_BUILDING: 5}),     # corrigé (Kirilloid: bûcheron)
    _b(id=B.IRONFOUNDRY, name="Fonderie", cost=(200, 450, 510, 120), k=1.80, u=6, cp=1,
       _time=F.make_time(6480, 1.5, 2400), benefit=F.p5, max_level=5,
       reqs={B.IRONMINE: 10, B.MAIN_BUILDING: 5}),
    _b(id=B.GRAINMILL, name="Moulin", cost=(500, 440, 380, 1240), k=1.80, u=3, cp=1,
       _time=F.make_time(4240, 1.5, 2400), benefit=F.p5, max_level=5,
       reqs={B.CROPLAND: 5, B.MAIN_BUILDING: 5}),
    _b(id=B.BAKERY, name="Boulangerie", cost=(1200, 1480, 870, 1600), k=1.80, u=4, cp=1,
       _time=F.make_time(6080, 1.5, 2400), benefit=F.p5, max_level=5,
       reqs={B.CROPLAND: 10, B.GRAINMILL: 5, B.MAIN_BUILDING: 5}),

    _b(id=B.WAREHOUSE, name="Entrepôt", cost=(130, 160, 90, 40), k=1.28, u=1, cp=1,
       _time=F.make_time(3875), benefit=F.capacity, reqs={B.MAIN_BUILDING: 1}, multi=True),
    _b(id=B.GRANARY, name="Grenier", cost=(80, 100, 70, 20), k=1.28, u=1, cp=1,
       _time=F.make_time(3475), benefit=F.capacity, reqs={B.MAIN_BUILDING: 1}, multi=True),

    _b(id=B.SMITHY, name="Forge", cost=(180, 250, 500, 160), k=1.28, u=4, cp=2,
       _time=F.make_time(3875), benefit=F.mb_like, reqs={B.MAIN_BUILDING: 3, B.ACADEMY: 1}),
    # benefit = +20 %/niveau de vitesse des troupes au-delà de 20 cases (vrai T4.6 ;
    # cf. movement._leg_seconds). Kirilloid n'expose pas la valeur → recoupée
    # support.travian.com / unofficialtravian (niv 20 = +400 % ⇒ ×5 la vitesse de base).
    _b(id=B.ARENA, name="Place de tournoi", cost=(1750, 2250, 1530, 240), k=1.28, u=1, cp=1,
       _time=F.make_time(5375), benefit=F.percent(20), reqs={B.RALLY_POINT: 15}),
    _b(id=B.MAIN_BUILDING, name="Bâtiment principal", cost=(70, 40, 60, 20), k=1.28, u=2, cp=2,
       _time=F.make_time(3875), benefit=F.mb_like),
    _b(id=B.RALLY_POINT, name="Place de rassemblement", cost=(110, 160, 90, 70), k=1.28, u=1, cp=1,
       _time=F.make_time(3875), benefit=F.identity, slot="rally"),
    _b(id=B.MARKETPLACE, name="Place de marché", cost=(80, 70, 120, 70), k=1.28, u=4, cp=3,
       _time=F.make_time(3675), benefit=F.identity,
       reqs={B.MAIN_BUILDING: 3, B.WAREHOUSE: 1, B.GRANARY: 1}),
    _b(id=B.EMBASSY, name="Ambassade", cost=(180, 130, 150, 80), k=1.28, u=3, cp=4,
       _time=F.make_time(3875), benefit=F.identity, reqs={B.MAIN_BUILDING: 1}),
    _b(id=B.BARRACKS, name="Caserne", cost=(210, 140, 260, 120), k=1.28, u=4, cp=1,
       _time=F.make_time(3875), benefit=F.train_bonus,
       reqs={B.MAIN_BUILDING: 3, B.RALLY_POINT: 1}),
    _b(id=B.STABLES, name="Écurie", cost=(260, 140, 220, 100), k=1.28, u=5, cp=2,
       _time=F.make_time(4075), benefit=F.train_bonus, reqs={B.SMITHY: 3, B.ACADEMY: 5}),
    _b(id=B.WORKSHOP, name="Atelier", cost=(460, 510, 600, 320), k=1.28, u=3, cp=3,
       _time=F.make_time(4875), benefit=F.train_bonus,
       reqs={B.MAIN_BUILDING: 5, B.ACADEMY: 10}),
    _b(id=B.ACADEMY, name="Académie", cost=(220, 160, 90, 40), k=1.28, u=4, cp=4,
       _time=F.make_time(3875), benefit=F.mb_like, reqs={B.MAIN_BUILDING: 3, B.BARRACKS: 3}),
    _b(id=B.CRANNY, name="Cachette", cost=(40, 50, 30, 10), k=1.28, u=0, cp=1,
       _time=F.make_time(2175, 1.16, 1875), benefit=F.cranny, multi=True),  # temps T4
    _b(id=B.TOWNHALL, name="Hôtel de ville", cost=(1250, 1110, 1260, 600), k=1.28, u=4, cp=5,
       _time=F.make_time(14375), benefit=F.mb_like, reqs={B.MAIN_BUILDING: 10, B.ACADEMY: 10}),
    _b(id=B.RESIDENCE, name="Résidence", cost=(580, 460, 350, 180), k=1.28, u=1, cp=2,
       _time=F.make_time(3875), benefit=F.residence_benefit, reqs={B.MAIN_BUILDING: 5}, max_level=20),
    _b(id=B.PALACE, name="Palais", cost=(550, 800, 750, 250), k=1.28, u=1, cp=5,
       _time=F.make_time(6875), benefit=F.palace_benefit,
       reqs={B.MAIN_BUILDING: 5, B.EMBASSY: 1}, max_level=20),
    _b(id=B.TREASURY, name="Trésorerie", cost=(2880, 2740, 2580, 990), k=1.26, u=4, cp=6,
       _time=F.make_time(9875), benefit=F.slots2, reqs={B.MAIN_BUILDING: 10}),
    _b(id=B.TRADE_OFFICE, name="Comptoir commercial", cost=(1400, 1330, 1200, 400), k=1.28, u=3, cp=3,
       _time=F.make_time(4875), benefit=F.p10, reqs={B.MARKETPLACE: 20, B.STABLES: 10}),
    _b(id=B.GREAT_BARRACKS, name="Grande caserne", cost=(630, 420, 780, 360), k=1.28, u=4, cp=1,
       _time=F.make_time(3875), benefit=F.train_bonus, reqs={B.BARRACKS: 20}, non_capital=True),
    _b(id=B.GREAT_STABLES, name="Grande écurie", cost=(780, 420, 660, 300), k=1.28, u=5, cp=2,
       _time=F.make_time(4075), benefit=F.train_bonus, reqs={B.STABLES: 20}, non_capital=True),

    _b(id=B.CITY_WALL, name="Muraille", cost=(70, 90, 170, 70), k=1.28, u=0, cp=1,
       _time=F.make_time(3875), benefit=F.wall4(1.030, 10), tribe=Tribe.ROMANS, slot="wall"),
    _b(id=B.EARTH_WALL, name="Rempart de terre", cost=(120, 200, 0, 80), k=1.28, u=0, cp=1,
       _time=F.make_time(3875), benefit=F.wall4(1.020, 6), tribe=Tribe.TEUTONS, slot="wall"),
    _b(id=B.PALISADE, name="Palissade", cost=(160, 100, 80, 60), k=1.28, u=0, cp=1,
       _time=F.make_time(3875), benefit=F.wall4(1.025, 8), tribe=Tribe.GAULS, slot="wall"),

    _b(id=B.STONEMASON, name="Tailleur de pierre", cost=(155, 130, 125, 70), k=1.28, u=2, cp=1,
       _time=F.make_time(5950, 2), benefit=F.p10, reqs={B.MAIN_BUILDING: 10, B.PALACE: 3},
       capital_only=True),
    # Brasserie : Teutons **uniquement en capitale** (support.travian.com / unofficialtravian
    # « Brewery » : « It can only be built by Teutons in the capital but affects the whole
    # empire ») → flag capital_only (sans ça un Teuton pouvait la bâtir hors capitale).
    _b(id=B.BREWERY, name="Brasserie", cost=(1460, 930, 1250, 1740), k=1.40, u=6, cp=4,
       _time=F.make_time(11750, 2), benefit=F.percent(1), max_level=10,
       reqs={B.GRANARY: 20, B.RALLY_POINT: 10}, tribe=Tribe.TEUTONS, capital_only=True),
    # ⚠️ Écart Kirilloid corrigé (trappeur & manoir) : kirilloid écrit `time(2000, 0)`
    # / `time(2300, 0)`, plaçant le 0 dans l'argument **k** (multiplicateur) au lieu de
    # **b** (offset) → temps négatif/nul dès le niveau 2. L'intention est b=0 avec le k
    # par défaut (1,16) : recoupé sur le vrai Travian (manoir niv 1→20 à BP 10 :
    # 27:30 … 7:42:20, ratios = 1,16 ; 2300·0,719 = 1654 ≈ 27:30). Donc make_time(a, 1.16, 0).
    _b(id=B.TRAPPER, name="Trappeur", cost=(100, 100, 100, 100), k=1.28, u=4, cp=1,
       _time=F.make_time(2000, 1.16, 0), benefit=F.trapper_traps, reqs={B.RALLY_POINT: 1},
       tribe=Tribe.GAULS, multi=True),
    _b(id=B.HERO_MANSION, name="Manoir du héros", cost=(700, 670, 700, 240), k=1.33, u=2, cp=1,
       _time=F.make_time(2300, 1.16, 0), benefit=F.slots3, reqs={B.MAIN_BUILDING: 3, B.RALLY_POINT: 1}),
    _b(id=B.GREAT_WAREHOUSE, name="Grand entrepôt", cost=(650, 800, 450, 200), k=1.28, u=1, cp=1,
       _time=F.make_time(10875), benefit=F.great_capacity, reqs={B.MAIN_BUILDING: 10},
       multi=True, non_capital=True),
    _b(id=B.GREAT_GRANARY, name="Grand grenier", cost=(400, 500, 350, 100), k=1.28, u=1, cp=1,
       _time=F.make_time(8875), benefit=F.great_capacity, reqs={B.MAIN_BUILDING: 10},
       multi=True, non_capital=True),
    _b(id=B.HORSE_POOL, name="Abreuvoir", cost=(780, 420, 660, 540), k=1.28, u=5, cp=3,
       _time=F.make_time(5950, 2), benefit=F.percent(1), reqs={B.RALLY_POINT: 10, B.STABLES: 20},
       tribe=Tribe.ROMANS),
]

BUILDINGS: dict[int, Building] = {b.id: b for b in _BUILDINGS}


def get(building_id: int) -> Building:
    return BUILDINGS[building_id]
