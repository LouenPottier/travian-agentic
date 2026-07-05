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
                 num_villages: int, tier: str, zone: str, dist: int,
                 capital_prefix: str, hero_level: int):
        self.name = name
        self.tribe = tribe
        self.template = ARMY_TEMPLATES[template_key]
        self.motto = motto            # RP : affiché dans le profil / village
        self.num_villages = num_villages
        self.tier = tier              # "legend" | "strong" | "mid"
        self.zone = zone              # "near" (autour du joueur) | "far" (frontière)
        self.dist = dist              # distance à l'ancre de zone (près du joueur / origine)
        self.capital_prefix = capital_prefix
        self.hero_level = hero_level


# Trois tiers de rivaux (noms & devises = clins d'œil historiques, RP) :
#   • **legend** — l'élite : ~10 villages, capitale 15-cropper niv 20, armée ~50 k.
#   • **strong** — les « très bons joueurs » : ~7 villages, capitale 15-cropper ~12 k.
#   • **mid**    — les joueurs solides : ~4-5 villages, capitale 9-cropper ~2 k.
# Chaque tier a **de vraies armées sur CHAQUE village** (secondaires ≥ ~1 000, cf.
# `_params` : les secondaires sont posés sur des croppers 9/15 avec une forte fraction
# de budget céréalier). Les légendes restent **loin** (frontière) ; strong et mid sont
# répartis **près du joueur ET au loin** (`zone`), pour peupler le voisinage proche.
LEGENDS = [
    Rival("Auguste", Tribe.ROMANS, "rome_off",
          "Imperator Caesar Divi Filius — je trouvai Rome de brique, je la laisse de marbre.",
          10, "legend", "far", 95, "Roma", 100),
    Rival("Vercingétorix", Tribe.GAULS, "gaul_def",
          "Roi des Arvernes — que la Gaule soit un rempart et non une proie.",
          10, "legend", "far", 118, "Gergovie", 90),
    Rival("Arminius", Tribe.TEUTONS, "teuton_raid",
          "Le fléau de Teutobourg — trois légions englouties par la forêt.",
          10, "legend", "far", 138, "Teutobourg", 90),
]

STRONG = [
    Rival("Scipion l'Africain", Tribe.ROMANS, "rome_off",
          "Vainqueur de Zama — la patience d'abord, la foudre ensuite.",
          7, "strong", "near", 42, "Liternum", 65),
    Rival("Brennus", Tribe.GAULS, "gaul_def",
          "Vae victis ! — malheur aux vaincus, et gare à mon glaive sur la balance.",
          7, "strong", "near", 52, "Senones", 60),
    Rival("Boudica", Tribe.TEUTONS, "teuton_raid",
          "Reine des Icènes — le feu pour les colonies, la lance pour les légions.",
          6, "strong", "far", 105, "Camulodunon", 62),
    Rival("Ambiorix", Tribe.GAULS, "gaul_def",
          "Prince des Éburons — une légion entière n'est jamais revenue de mes forêts.",
          6, "strong", "far", 125, "Atuatuca", 58),
]

MIDS = [
    Rival("Spartacus", Tribe.GAULS, "gaul_def",
          "Le gladiateur rebelle — mieux vaut tomber libre que vivre enchaîné.",
          5, "mid", "near", 26, "Vésuve", 40),
    Rival("Marius", Tribe.ROMANS, "rome_off",
          "Consul aux sept mandats — j'ai refait la légion à mon image.",
          5, "mid", "near", 34, "Cereatae", 42),
    Rival("Ariovist", Tribe.TEUTONS, "teuton_raid",
          "Roi des Suèves — la Gaule de l'est m'appartient, et je la garde par le fer.",
          4, "mid", "near", 48, "Magetobriga", 36),
    Rival("Sylla", Tribe.ROMANS, "rome_off",
          "Dictateur de Rome — heureux, et sans pitié pour qui me défie.",
          4, "mid", "far", 92, "Nola", 38),
    Rival("Alaric", Tribe.TEUTONS, "teuton_raid",
          "Roi des Wisigoths — les portes de Rome finiront par céder.",
          4, "mid", "far", 128, "Noviodunum", 34),
]

# Rivaux **très proches** du joueur (`zone="near"`, `dist` ~12-22, sous les ~26+ des
# `MIDS`/`STRONG` near) : de vrais voisins immédiats, à sa porte. Un `strong` (Marbod)
# pour une menace sérieuse à côté, entouré de mid. Mêmes règles (croppers + armées ≥ ~1 k).
CLOSE = [
    Rival("Crixus", Tribe.GAULS, "gaul_def",
          "Lieutenant de Spartacus — j'ai choisi de mourir l'épée à la main, pas de fuir.",
          4, "mid", "near", 12, "Garganus", 40),
    Rival("Marbod", Tribe.TEUTONS, "teuton_raid",
          "Roi des Marcomans — mon royaume borde le tien, et mes hordes n'attendent qu'un mot.",
          6, "strong", "near", 16, "Boiohaemum", 58),
    Rival("Catilina", Tribe.ROMANS, "rome_off",
          "Conjuré de Rome — puisqu'on me refuse le consulat, je prendrai tout par les armes.",
          5, "mid", "near", 19, "Faesulae", 42),
    Rival("Divico", Tribe.GAULS, "gaul_def",
          "Chef des Helvètes — j'ai déjà fait passer une armée romaine sous le joug.",
          5, "mid", "near", 22, "Genava", 40),
]

# Ordre de peuplement recommandé (le marqueur d'idempotence `Auguste` doit être posé
# **en dernier**, cf. `spawn_rivals`). Regroupe tous les tiers (les très-proches d'abord).
ALL_RIVALS = CLOSE + MIDS + STRONG + LEGENDS


# Paramètres de développement par tier (capitale vs secondaire). `sec_layouts` = motif
# de distributions de champs imposé aux secondaires (répété) ⇒ **bonne proportion de
# croppers 9/15** (`want_layout`, forcé si aucune vallée de ce type n'est libre à côté),
# pour loger de vraies armées de secondaire (≥ ~1 000).
def _params(tier: str, is_capital: bool) -> dict:
    if tier == "legend":
        if is_capital:                             # 15-cropper niv 20 ⇒ ~50 k troupes
            return dict(field_layout="1-1-1-15", field_level=20, center_level=20,
                        wall_level=20, army_frac=0.90)
        # Secondaire de légende sur cropper (champs plafonnés à 10 hors capitale) : de
        # vraies armées (~1-3 k), et un surplus de blé qui ravitaille la capitale (routes).
        return dict(field_level=10, center_level=18, wall_level=20, army_frac=0.60,
                    sec_layouts=("1-1-1-15", "3-3-3-9", "3-3-3-9"))
    if tier == "strong":
        if is_capital:                             # 15-cropper champs 14 ⇒ ~12 k troupes
            return dict(field_layout="1-1-1-15", field_level=14, center_level=16,
                        wall_level=20, army_frac=0.90)
        return dict(field_level=10, center_level=14, wall_level=15, army_frac=0.85,
                    sec_layouts=("3-3-3-9", "1-1-1-15", "3-3-3-9", "4-4-4-6"))
    # Mid : capitale 9-cropper ⇒ ~2 k ; secondaires ~1-1,5 k sur croppers/vallées riches.
    if is_capital:
        return dict(field_layout="3-3-3-9", field_level=10, center_level=13,
                    wall_level=12, army_frac=0.90)
    return dict(field_level=10, center_level=11, wall_level=10, army_frac=0.85,
                sec_layouts=("3-3-3-9", "4-4-4-6", "3-3-3-9"))


def _anchor(index: int, dist: int, center: tuple[int, int]) -> tuple[int, int]:
    """Point d'ancrage déterministe autour de `center`, à distance `dist` (angle réparti
    par le nombre d'or). `center` = origine (frontière) ou position du joueur (voisinage)."""
    angle = 2 * math.pi * (index * 0.61803398875)
    return (center[0] + int(round(dist * math.cos(angle))),
            center[1] + int(round(dist * math.sin(angle))))


def _human_ref() -> tuple[int, int]:
    """Position de référence du **voisinage du joueur** (pour les rivaux `zone="near"`).
    = capitale du premier compte non-PNJ (le joueur humain, créé en premier). Repli sur
    `HUMAN_START` (60, 60) si aucun humain n'est encore posé (ex. monde de test)."""
    from app import store
    for p in store.all_players():
        if p["is_npc"]:
            continue
        vids = store.player_villages(p["id"])
        cap = next((store.load_village(v) for v in vids
                    if store.load_village(v).is_capital), None)
        if cap is not None:
            return cap.x, cap.y
        if vids:
            v = store.load_village(vids[0])
            return v.x, v.y
    return (60, 60)


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


def _place_valley(near_x: int, near_y: int, occupied: set[tuple[int, int]],
                  want_layout: str | None) -> tuple[int, int, str] | None:
    """Trouve une vallée libre proche de la distribution voulue ; si aucune de ce type
    n'est libre, prend la plus proche et **force** la distribution (comme le fait la
    capitale 15-cropper). Renvoie `(x, y, layout_effectif)` ou `None`."""
    from app import store
    if want_layout is not None:
        spot = _free_valley(near_x, near_y, occupied, want_layout=want_layout)
        if spot is not None:
            return spot[0], spot[1], want_layout
    spot = _free_valley(near_x, near_y, occupied)
    if spot is None:
        return None
    if want_layout is not None:
        store.set_tile_layout(spot[0], spot[1], want_layout)
        return spot[0], spot[1], want_layout
    t = store.get_tile(spot[0], spot[1])
    return spot[0], spot[1], t["layout"]


def spawn_rivals(server_speed: int) -> list[int]:
    """Crée (idempotent) les comptes rivaux avancés (3 tiers, ~12 joueurs, proche & loin),
    avec leurs villages développés, héros, et routes commerciales secondaires → capitale.
    Renvoie les ids des joueurs **créés à cet appel**.

    **Purement additif** : chaque rival est créé s'il n'existe **pas encore par son nom**,
    sinon **sauté** (aucune modification/suppression d'un joueur ou village existant). Sur
    un monde déjà semé, on ne fait qu'**ajouter les rivaux manquants** (ex. de nouveaux
    noms introduits après coup) ; sur un monde neuf, on crée les 12. L'ancrage `_anchor`
    utilise l'index **fixe** dans `order` ⇒ chaque rival tombe toujours au même endroit,
    indépendamment de ceux déjà présents (les collisions sont évitées via `occupied`)."""
    from app import store
    from app.engine import movement as MOV

    occupied = {(v["x"], v["y"]) for v in store.list_villages()}
    now = _time.time()
    created: list[int] = []
    href = _human_ref()                        # centre du « voisinage proche » du joueur
    # Le marqueur (`Auguste`) reste ordonné en dernier (compat historique) ; l'idempotence
    # est désormais garantie **par nom**, donc un seeding interrompu se complète au réappel.
    marker = next(r for r in ALL_RIVALS if r.name == SEED_MARKER)
    order = [r for r in ALL_RIVALS if r.name != SEED_MARKER] + [marker]

    for ri, rival in enumerate(order):
        if store.find_player_by_name(rival.name) is not None:
            continue                           # déjà présent ⇒ on n'y touche pas
        pid = store.create_player(rival.name, rival.tribe, is_npc=True)
        # Ancrage de l'empire : autour du joueur (`near`) ou sur la frontière (`far`).
        center = href if rival.zone == "near" else (0, 0)
        ax, ay = _anchor(ri, rival.dist, center)
        cap_params = _params(rival.tier, is_capital=True)
        want = cap_params.pop("field_layout")
        placed = _place_valley(ax, ay, occupied, want)
        if placed is None:
            continue
        cx, cy, _ = placed
        occupied.add((cx, cy))
        cap = _developed_village(
            f"{rival.capital_prefix}", rival.tribe, cx, cy, pid, server_speed,
            is_capital=True, field_layout=want, template=rival.template, **cap_params)
        cap = store.insert_village(cap)
        created.append(pid)
        _make_hero(pid, cap.id, rival.hero_level, rival.tribe)

        # Villages secondaires : autour de la capitale, sur des vallées **croppers** (motif
        # `sec_layouts` répété ⇒ bonne proportion de 9/15-croppers) avec de vraies armées.
        sec_params = _params(rival.tier, is_capital=False)
        sec_layouts = sec_params.pop("sec_layouts", None)
        sec_ids: list[int] = []
        for k in range(2, rival.num_villages + 1):
            want_sec = sec_layouts[(k - 2) % len(sec_layouts)] if sec_layouts else None
            placed = _place_valley(cx + k, cy + (k % 3), occupied, want_sec)
            if placed is None:
                break
            sx, sy, layout = placed
            occupied.add((sx, sy))
            sec = _developed_village(
                f"{rival.capital_prefix} {k}", rival.tribe, sx, sy, pid, server_speed,
                is_capital=False, field_layout=layout, template=rival.template,
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
