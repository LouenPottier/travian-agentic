"""Joueurs rivaux avancés — peuplement « façon serveur mûr » (RP).

Ce module sème dans le monde des **comptes rivaux déjà développés**, pour donner de
la profondeur à la carte : quelques **légendes** au sommet (une dizaine de villages,
tous bâtiments niveau max, capitale sur un **15-cropper** (1-1-1-15) champs niveau 20,
**armée gigantesque**) et une poignée de joueurs **moyens** (3-4 villages de niveau
intermédiaire, armées modestes). Les secondaires **alimentent la capitale en céréales
par des routes commerciales** (RP + tampon de grenier).

⚠️ **Fidélité** (cf. CLAUDE.md) : ce sont des **PNJ de peuplement**, pas des chiffres
de jeu. Les *valeurs de jeu* (production, capacités, upkeep) restent celles des tables
kirilloid — on ne fait que **poser un état** que le moteur produit naturellement. Les
**choix de peuplement** (qui, combien de villages, où, quelle composition d'armée) sont
des choix de dev assumés, au même statut que le seeding des Natars / du joueur IA.

### Armée « max tenable » — dimensionnement fidèle
Le vrai Travian dit « armée max = celle que la production de céréales du compte peut
nourrir ». Dans ce moteur, la **famine** (`village._starve`) est ancrée sur la
production **du village où stationnent les troupes** : le plafond d'une garnison qui
survit à un `tick` est exactement `gross_production[CROP] − population` de **son**
village (le blé reçu par commerce **remplit le grenier** mais ne relève pas ce
plafond — limitation documentée du modèle paresseux). On dimensionne donc l'armée
stationnée à **≈ 0,9 × (blé brut − population) du village hôte** : gigantesque sur un
15-cropper niveau 20 (mill+boulangerie ⇒ +50 %) et **garantie sans famine** au premier
passage du temps. Les routes commerciales des secondaires vers la capitale sont le
**ravitaillement RP** (elles remplissent le grenier, tampon confortable).
"""
from __future__ import annotations

import math
import random
import time as _time

from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe
from app.data.units import UNITS
from app.engine import village as V
from app.engine import world as W
from app.engine import hero as HERO


# --- Marqueur d'idempotence --------------------------------------------------
# Un joueur « sentinelle » dont la présence signale que le peuplement rival est déjà
# fait (migration douce : on ne re-sème jamais). Doit rester le tout dernier créé.
SEED_MARKER = "Auguste"


# --- Panoplie de bâtiments d'une ville développée ----------------------------
# Bâtiment producteur de blé mis en avant : mill + boulangerie (niv 5) ⇒ +50 % de
# céréales, ce qui gonfle le budget d'armée. Marché niv 20 ⇒ 20 marchands (routes
# commerciales). Ordre = priorité (si l'on dépasse les 20 emplacements de centre
# 19..38, les derniers sont ignorés). `(building_id, niveau)` — le niveau est plafonné
# au `max_level` du bâtiment. `None` pour un emplacement « spécial de tribu » injecté.
def _center_plan(tribe: Tribe, is_capital: bool, lvl: int) -> list[tuple[int, int]]:
    """Liste ordonnée `(building_id, niveau)` pour le centre du village (hors champs,
    rassemblement et muraille), calée sur le niveau cible `lvl`."""
    admin = (B.PALACE if is_capital else B.RESIDENCE, lvl)
    plan: list[tuple[int, int]] = [
        (B.MAIN_BUILDING, lvl),
        (B.GRAINMILL, 5), (B.BAKERY, 5),          # +50 % céréales → grosse armée
        (B.WAREHOUSE, lvl), (B.GRANARY, lvl), (B.GRANARY, lvl),
        (B.MARKETPLACE, 20),                       # 20 marchands (routes commerciales)
        (B.BARRACKS, lvl), (B.STABLES, lvl), (B.WORKSHOP, lvl),
        (B.ACADEMY, lvl), (B.SMITHY, lvl),
        admin,
        (B.HERO_MANSION, lvl),
        (B.TOWNHALL, lvl),
        (B.EMBASSY, min(lvl, 20)),
        (B.CRANNY, 10),
    ]
    # Spécial de tribu (remplace/complète) : abreuvoir romain, brasserie teutonne
    # (capitale), trappeur gaulois. Injecté tôt pour ne pas être tronqué.
    special = {
        Tribe.ROMANS: (B.HORSE_POOL, lvl),
        Tribe.TEUTONS: (B.BREWERY, 10) if is_capital else None,
        Tribe.GAULS: (B.TRAPPER, lvl),
    }.get(tribe)
    if special is not None:
        plan.insert(12, special)
    if is_capital:
        plan.insert(7, (B.TRADE_OFFICE, 20))       # +10 %/niv de capacité marchande
        plan.append((B.ARENA, lvl))
    return plan


_WALL = {Tribe.ROMANS: B.CITY_WALL, Tribe.TEUTONS: B.EARTH_WALL, Tribe.GAULS: B.PALISADE}


# --- Compositions d'armée par rôle (index d'unité -> poids de budget céréalier) ---
# Le budget de blé de l'armée est réparti selon ces poids, puis converti en effectifs
# en divisant par l'upkeep de chaque unité. Choix RP/tactique, pas des chiffres de jeu.
ARMY_TEMPLATES = {
    # Romains offensifs (légions) : Imperians en masse + cavalerie lourde + siège.
    "rome_off": {2: 0.42, 0: 0.15, 4: 0.16, 5: 0.12, 1: 0.05, 6: 0.06, 7: 0.03, 8: 0.01},
    # Gaulois défensifs (murailles) : phalanges + Haeduans + druides.
    "gaul_def": {0: 0.50, 5: 0.18, 4: 0.16, 1: 0.09, 6: 0.04, 7: 0.02, 8: 0.01},
    # Teutons pillards : massues + haches + cavaliers teutons + siège.
    "teuton_raid": {0: 0.40, 2: 0.26, 5: 0.15, 1: 0.09, 6: 0.06, 7: 0.03, 8: 0.01},
}


def _fill_army(v: V.Village, frac: float, template: dict[int, float]) -> None:
    """Peuple `v.troops` avec une armée dont l'upkeep ≈ `frac × (blé brut − population)`
    du village (⇒ pas de famine, cf. `village._starve`)."""
    gross_crop = V.gross_production(v)[W.CROP]
    budget = max(0.0, gross_crop - V.population(v)) * frac
    total_w = sum(template.values())
    troops = [0] * 10
    for idx, w in template.items():
        up = max(1, UNITS[v.tribe][idx].upkeep)
        troops[idx] = int(budget * (w / total_w) / up)
    v.troops = troops


def _developed_village(name: str, tribe: Tribe, x: int, y: int, player_id: int,
                       server_speed: int, is_capital: bool, field_layout: str,
                       field_level: int, center_level: int, wall_level: int,
                       army_frac: float, template: dict[int, float]) -> V.Village:
    """Construit (sans persister) un village pleinement développé + son armée."""
    slots: dict[int, V.Slot] = {}
    for i, bid in enumerate(W.layout_fields(field_layout), start=1):
        slots[i] = V.Slot(building_id=bid, level=field_level)
    # Centre : on remplit 19..38 dans l'ordre de priorité (les excédents sont ignorés).
    for slot_idx, (bid, lvl) in zip(V.CENTER_SLOTS, _center_plan(tribe, is_capital, center_level)):
        slots[slot_idx] = V.Slot(building_id=bid, level=min(lvl, BLD.get(bid).max_level))
    slots[V.RALLY_SLOT] = V.Slot(building_id=B.RALLY_POINT, level=min(center_level, 20))
    slots[V.WALL_SLOT] = V.Slot(building_id=_WALL[tribe], level=wall_level)
    v = V.Village(name=name, tribe=tribe, slots=slots, server_speed=server_speed,
                  x=x, y=y, player_id=player_id, is_capital=is_capital,
                  updated_at=_time.time())
    _fill_army(v, army_frac, template)
    # Ressources presque pleines : tampon de grenier (anti-famine au 1ᵉʳ tick) + butin.
    caps = V.capacities(v)
    v.resources = [0.85 * caps[i] for i in range(4)]
    return v


# --- Recherche de vallées libres (spirale, comme natars/main) ----------------
def _free_valley(near_x: int, near_y: int, occupied: set[tuple[int, int]],
                 want_layout: str | None = None, max_r: int = 60) -> tuple[int, int] | None:
    """Vallée libre la plus proche d'un point (spirale). Si `want_layout` est donné,
    ne retient qu'une vallée de cette distribution de champs (ex. 15-cropper)."""
    from app import store
    for radius in range(max_r + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                x, y = near_x + dx, near_y + dy
                if (x, y) in occupied or max(abs(x), abs(y)) > W.WORLD_RADIUS:
                    continue
                t = store.get_tile(x, y)
                if not t or t["kind"] != "valley":
                    continue
                if want_layout is None or t["layout"] == want_layout:
                    return x, y
    return None


# --- Description d'un compte rival (RP) --------------------------------------
class Rival:
    def __init__(self, name: str, tribe: Tribe, template_key: str, motto: str,
                 num_villages: int, tier: str, radius: int,
                 capital_prefix: str, hero_level: int):
        self.name = name
        self.tribe = tribe
        self.template = ARMY_TEMPLATES[template_key]
        self.motto = motto            # RP : affiché dans le profil / village
        self.num_villages = num_villages
        self.tier = tier              # "legend" | "mid"
        self.radius = radius          # anneau d'apparition (loin du centre Natar)
        self.capital_prefix = capital_prefix
        self.hero_level = hero_level


# Empires légendaires (une dizaine de villages, capitale 15-cropper max, armée énorme)
# + joueurs moyens. Noms & devises = clins d'œil historiques (RP).
LEGENDS = [
    Rival("Auguste", Tribe.ROMANS, "rome_off",
          "Imperator Caesar Divi Filius — je trouvai Rome de brique, je la laisse de marbre.",
          10, "legend", 95, "Roma", 100),
    Rival("Vercingétorix", Tribe.GAULS, "gaul_def",
          "Roi des Arvernes — que la Gaule soit un rempart et non une proie.",
          10, "legend", 115, "Gergovie", 90),
    Rival("Arminius", Tribe.TEUTONS, "teuton_raid",
          "Le fléau de Teutobourg — trois légions englouties par la forêt.",
          10, "legend", 135, "Teutobourg", 90),
]

MIDS = [
    Rival("Scipion l'Africain", Tribe.ROMANS, "rome_off",
          "Vainqueur de Zama — la patience d'abord, la foudre ensuite.",
          4, "mid", 80, "Liternum", 40),
    Rival("Boudica", Tribe.TEUTONS, "teuton_raid",
          "Reine des Icènes — le feu pour les colonies, la lance pour les légions.",
          4, "mid", 100, "Camulodunon", 38),
    Rival("Brennus", Tribe.GAULS, "gaul_def",
          "Vae victis ! — malheur aux vaincus, et gare à mon glaive sur la balance.",
          4, "mid", 122, "Senones", 35),
    Rival("Spartacus", Tribe.GAULS, "gaul_def",
          "Le gladiateur rebelle — mieux vaut tomber libre que vivre enchaîné.",
          3, "mid", 88, "Vésuve", 30),
    Rival("Alaric", Tribe.TEUTONS, "teuton_raid",
          "Roi des Wisigoths — les portes de Rome finiront par céder.",
          3, "mid", 128, "Noviodunum", 32),
]


# Paramètres de développement par tier (capitale vs secondaire).
def _params(tier: str, is_capital: bool) -> dict:
    if tier == "legend":
        if is_capital:
            return dict(field_layout="1-1-1-15", field_level=20, center_level=20,
                        wall_level=20, army_frac=0.90)
        # Secondaire de légende : champs niv 10 (plafond hors capitale), tout niv max,
        # petite garnison (garde son blé pour ravitailler la capitale par commerce).
        return dict(field_level=10, center_level=18, wall_level=20, army_frac=0.20)
    # Mid : niveaux intermédiaires, armées modérées.
    if is_capital:
        return dict(field_layout="3-3-3-9", field_level=10, center_level=12,
                    wall_level=12, army_frac=0.65)
    return dict(field_level=9, center_level=10, wall_level=10, army_frac=0.35)


def _anchor(index: int, radius: int) -> tuple[int, int]:
    """Point d'ancrage déterministe sur un anneau (angle réparti par le nombre d'or)."""
    angle = 2 * math.pi * (index * 0.61803398875)
    return int(round(radius * math.cos(angle))), int(round(radius * math.sin(angle)))


def _make_hero(player_id: int, capital_id: int, level: int, tribe: Tribe) -> None:
    """Dote un rival d'un héros aguerri (défenseur du village d'attache). RP/flavor :
    niveau et attributs élevés ; pas des chiffres de jeu (approximation documentée)."""
    h = HERO.new_hero(player_id, capital_id)
    h.level = level
    h.experience = HERO.xp_threshold(level)
    pts = level * 4
    h.fight = pts // 2                       # force de combat (défense à la maison)
    h.def_points = pts - h.fight             # bonus défensif d'armée
    h.points = 0
    HERO.save(h)


def spawn_rivals(server_speed: int) -> list[int]:
    """Crée (idempotent) les comptes rivaux avancés : légendes + joueurs moyens, avec
    leurs villages développés, héros, et routes commerciales secondaires → capitale.
    Renvoie les ids des joueurs créés. Ne fait rien si le marqueur existe déjà."""
    from app import store
    from app.engine import movement as MOV
    if store.find_player_by_name(SEED_MARKER) is not None:
        return []

    occupied = {(v["x"], v["y"]) for v in store.list_villages()}
    now = _time.time()
    created: list[int] = []
    # Les légendes en dernier : le marqueur (Auguste) doit clore le seeding pour que
    # l'idempotence soit correcte même en cas d'interruption → on met MIDS puis LEGENDS,
    # et à l'intérieur des LEGENDS on garde Auguste en tête (donc créé en premier des
    # légendes) — pour que le marqueur ne soit posé qu'une fois tout le reste écrit, on
    # ordonne : MIDS, puis légendes hors-marqueur, puis le marqueur.
    marker = next(r for r in LEGENDS if r.name == SEED_MARKER)
    order = MIDS + [r for r in LEGENDS if r.name != SEED_MARKER] + [marker]

    for ri, rival in enumerate(order):
        pid = store.create_player(rival.name, rival.tribe, is_npc=True)
        # Ancrage de l'empire, puis capitale sur un 15-cropper (légende) proche.
        ax, ay = _anchor(ri, rival.radius)
        cap_params = _params(rival.tier, is_capital=True)
        want = cap_params.pop("field_layout")
        spot = _free_valley(ax, ay, occupied, want_layout=want)
        if spot is None:                      # aucun 15-cropper à portée → vallée quelconque
            spot = _free_valley(ax, ay, occupied)
            if spot is None:
                continue
            store.set_tile_layout(spot[0], spot[1], want)  # on force la distribution
        cx, cy = spot
        occupied.add((cx, cy))
        cap = _developed_village(
            f"{rival.capital_prefix}", rival.tribe, cx, cy, pid, server_speed,
            is_capital=True, field_layout=want, template=rival.template, **cap_params)
        cap = store.insert_village(cap)
        created.append(pid)
        _make_hero(pid, cap.id, rival.hero_level, rival.tribe)

        # Villages secondaires : autour de la capitale, sur les vraies vallées.
        sec_ids: list[int] = []
        for k in range(2, rival.num_villages + 1):
            sp = _free_valley(cx + k, cy + (k % 3), occupied)
            if sp is None:
                break
            sx, sy = sp
            occupied.add((sx, sy))
            t = store.get_tile(sx, sy)
            sec_params = _params(rival.tier, is_capital=False)
            sec = _developed_village(
                f"{rival.capital_prefix} {k}", rival.tribe, sx, sy, pid, server_speed,
                is_capital=False, field_layout=t["layout"], template=rival.template,
                **sec_params)
            sec = store.insert_village(sec)
            sec_ids.append(sec.id)

        # Ravitaillement RP : chaque secondaire envoie du blé à la capitale (route
        # commerciale récurrente). Cargaison ≈ capacité marchande (marché 20), toutes
        # les 2 h de temps de base. Réutilise la machinerie enforced (movement).
        from app.data.tribes import MERCHANT_CAPACITY
        cargo = int(MERCHANT_CAPACITY[rival.tribe] * 20 * 0.9)  # ~18 marchands pleins
        for sid in sec_ids:
            try:
                MOV.create_trade_route(sid, cap.id, pid, [0, 0, 0, cargo],
                                       interval_hours=2.0, now=now)
            except MOV.MoveError:
                pass  # marché insuffisant (ne devrait pas arriver) → on ignore
    return created
