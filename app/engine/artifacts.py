"""Artefacts (endgame Natars) : détention Natar, capture par le héros, effets.

Mécanique fidèle (recoupée doc officielle / wiki — kirilloid muet, cf. data/artifacts) :

- **Détention** : des villages Natars dédiés détiennent les artefacts (table `artifacts`,
  `holder='natar'`). Ils restent attaquables/pillables comme les autres Natars, mais
  **non conquérables** (garde-fou `conquest.conquer_eligible` sur les PNJ).
- **Capture** (vrai T4.6, recoupé support.travian.com « Artefacts » /
  unofficialtravian « How to get ready for the Artefact release » / wiki Fandom
  « Artifacts ») : *voler* un artefact =
    1. **détruire la trésorerie** du village-artefact (les villages-artefact Natars ont
       une trésorerie **niveau 20** dans TOUS les cas, même pour un petit artefact ⇒
       ~55 catapultes pour la raser) ; ET
    2. remporter une **attaque normale** (pas razzia) menée par le **héros** (présent et
       survivant) contre ce village, en **vainquant la garnison** (défenseurs réduits à 0).
  La vague de catapultes qui détruit la trésorerie et la vague du héros peuvent être la
  **même attaque**. Il faut en plus une **trésorerie assez grande et vide** pour stocker
  l'artefact capturé (vrai T4.6 : « emplacements de trésor », support.travian.com) :
    - **petit** artefact → trésorerie **niveau ≥ 10** (1 emplacement, `formulas.slots2`) ;
    - **grand / unique** → trésorerie **niveau ≥ 20** (2 emplacements).
  ⚠️ **Simplification documentée** : on exige la trésorerie **du village d'origine** de
  l'armée (et une trésorerie n'héberge qu'un artefact à la fois) ; le vrai jeu permet de
  stocker dans n'importe quel village au siège suffisant. Suffisant pour le modèle local.
- **Effets** (cf. `data.artifacts` pour les magnitudes & sources) : un **petit** artefact
  n'agit que dans **son** village de stockage ; un **grand/unique** agit sur **tout le
  compte**. **Tous branchés** : **durabilité des bâtiments** (siège), **vitesse des troupes**
  (trajet), **consommation de céréales** (entretien), **grand entrepôt/grenier** (déblocage
  de construction), **temps d'entraînement**, **capacité des cachettes**, **espionnage**
  (puissance de reconnaissance att./déf.), et l'**artefact du fou** (prend un effet aléatoire
  par fenêtre de 24 h, cf. `data.artifacts.fool_current`).
"""
from __future__ import annotations

import sqlite3
import time as _time

from app import store
from app.data import artifacts as AT
from app.data.buildings import B
from app.data.tribes import NPC_TRIBES
from app.engine import village as V

# Niveau de trésorerie requis pour stocker un artefact selon sa taille (vrai T4.6 :
# trésorerie vide niv 10 = petit, niv 20 = grand/unique ; cf. formulas.slots2).
TREASURY_SMALL = 10
TREASURY_BIG = 20


def required_treasury(size: str) -> int:
    return TREASURY_SMALL if size == "small" else TREASURY_BIG


def treasury_level(v: V.Village) -> int:
    return V.building_levels(v).get(B.TREASURY, 0)


def can_store(v: V.Village, size: str) -> bool:
    """Le village peut-il stocker un artefact de cette taille : trésorerie assez haute
    **et** non déjà occupée par un autre artefact."""
    return (treasury_level(v) >= required_treasury(size)
            and store.artifact_in_village(v.id) is None)


# --- Effets : agrégation par village / compte --------------------------------
def _resolve(a: dict, now: float, server_speed: int) -> tuple[str, float, str]:
    """(effet, magnitude, taille) effectifs d'un artefact. Cas normal = son effet fixe ;
    l'**artefact du fou** tire son effet + sa magnitude toutes les 24 h (cf. `fool_current`)."""
    t = AT.get(a["kind"])
    if t.effect != "fool":
        return t.effect, t.mag[a["size"]], a["size"]
    effect, mag = AT.fool_current(a["id"], a["size"], now, server_speed)
    return effect, mag, a["size"]


def _magnitudes(village: V.Village, effect: str, now: float | None = None) -> list[float]:
    """Magnitudes effectives de `effect` pour `village` (résout le fou) : les **petits**
    seulement s'ils sont stockés dans CE village, les **grands/uniques** partout (compte)."""
    if village.player_id is None:
        return []
    now = now if now is not None else _time.time()
    # Tolérant à une base sans table `artifacts` (monde non semé / tests en mémoire) :
    # pas de table ⇒ aucun artefact ⇒ effet neutre (aucun multiplicateur).
    try:
        owned = store.artifacts_owned_by(village.player_id)
    except sqlite3.OperationalError:
        return []
    out = []
    for a in owned:
        if a["village_id"] is None:           # artefact détaché (village conquis) : inactif
            continue
        eff, mag, size = _resolve(a, now, village.server_speed)
        if eff != effect:
            continue
        if size == "small" and a["village_id"] != village.id:
            continue
        out.append(mag)
    return out


def _factor(village: V.Village, effect: str, neutral: float, better,
            now: float | None = None) -> float:
    """Magnitude effective d'un effet pour `village` (neutre si aucun artefact).
    `better` = max (multiplicateurs : plus haut = mieux) ou min (consommation/temps)."""
    vals = _magnitudes(village, effect, now)
    if not vals:
        return neutral
    return better([neutral, *vals])


def durability_multiplier(village: V.Village, now: float | None = None) -> float:
    """Multiplicateur de durabilité des bâtiments (siège) accordé par l'artefact de
    l'architecte. 1,0 sinon. S'ajoute (multiplicativement) au tailleur de pierre."""
    return _factor(village, "durability", 1.0, max, now)


def crop_multiplier(village: V.Village, now: float | None = None) -> float:
    """Facteur de consommation de céréales des troupes (diète) : ×0,5 si actif, 1,0 sinon."""
    return _factor(village, "crop", 1.0, min, now)


def speed_multiplier(village: V.Village, now: float | None = None) -> float:
    """Multiplicateur de vitesse des troupes partant de ce village (bottes ailées)."""
    return _factor(village, "speed", 1.0, max, now)


def cranny_multiplier(village: V.Village, now: float | None = None) -> float:
    """Multiplicateur de capacité des cachettes (cartographe) : ×200/100/500, 1,0 sinon."""
    return _factor(village, "cranny", 1.0, max, now)


def spy_multiplier(village: V.Village, now: float | None = None) -> float:
    """Multiplicateur d'efficacité des éclaireurs (œil de l'aigle) : ×5/3/10, 1,0 sinon.
    Appliqué à la puissance de reconnaissance (attaque comme défense)."""
    return _factor(village, "spy", 1.0, max, now)


def training_multiplier(village: V.Village, now: float | None = None) -> float:
    """Facteur de temps d'entraînement des troupes (entraîneur) : <1 si actif, 1,0 sinon."""
    return _factor(village, "training", 1.0, min, now)


def great_storage_allowed(village: V.Village) -> bool:
    """Le grand entrepôt / grand grenier n'est constructible que si le joueur détient
    l'artefact du bâtisseur (`storage`) applicable ici (vrai T4.6 : cet artefact **débloque**
    la construction du grand entrepôt/grenier ; support.travian.com « Artefact Effects »).
    Petit ⇒ seulement dans le village de stockage ; grand/unique ⇒ tout le compte."""
    if village.player_id is None:
        return False
    return bool(_magnitudes(village, "storage"))


# --- Capture -----------------------------------------------------------------
def try_capture(origin: V.Village, target: V.Village, att_hero, hero_alive: bool,
                kind: str, won: bool, now: float | None = None) -> dict | None:
    """Tente la capture de l'artefact détenu par `target` (village Natar). Renvoie un
    récap pour le rapport (capturé ou non, avec la raison), ou None si `target` ne
    détient aucun artefact. À appeler après résolution du combat.

    `won` = la garnison ennemie est-elle vaincue (défenseurs réduits à 0) ?"""
    now = now or _time.time()
    art = store.artifact_held_by_natar(target.id)
    if art is None or target.tribe not in NPC_TRIBES:
        return None
    label = AT.describe(art["kind"], art["size"])
    if kind != "attack":
        return {"captured": False, "artefact": label, "raison": "razzia (attaque requise)"}
    if att_hero is None:
        return {"captured": False, "artefact": label, "raison": "héros requis"}
    if not hero_alive or not won:
        return {"captured": False, "artefact": label, "raison": "victoire du héros requise"}
    # Fidélité vrai T4.6 : la trésorerie du village-artefact doit être **détruite**
    # (catapultes) pour libérer l'artefact. Les slots de `target` sont déjà mutés par le
    # siège au moment de cet appel (cf. movement._resolve_battle). Cf. en-tête.
    if treasury_level(target) > 0:
        return {"captured": False, "artefact": label,
                "raison": "trésorerie du village-artefact à détruire (catapultes)"}
    if not can_store(origin, art["size"]):
        need = required_treasury(art["size"])
        return {"captured": False, "artefact": label,
                "raison": f"trésorerie vide niveau {need} requise à {origin.name}"}
    store.capture_artifact(art["id"], origin.player_id, origin.id)
    return {"captured": True, "artefact": label, "village": origin.name,
            "effet": AT.get(art["kind"]).desc}


# --- État (API / UI) ---------------------------------------------------------
def _entry(a: dict, captured: bool) -> dict:
    t = AT.get(a["kind"])
    return {"id": a["id"], "kind": a["kind"], "size": a["size"],
            "name": t.name, "label": AT.describe(a["kind"], a["size"]),
            "effect": t.effect, "desc": t.desc, "wired": t.wired,
            "scope": AT.scope(a["size"]), "active": captured}


def owned_status(player_id: int) -> list[dict]:
    """Artefacts capturés du joueur (avec le village de stockage)."""
    out = []
    for a in store.artifacts_owned_by(player_id):
        e = _entry(a, captured=True)
        e["village_id"] = a["village_id"]
        out.append(e)
    return out


def map_status() -> list[dict]:
    """Artefacts encore à conquérir (sur des villages Natars), avec leurs coordonnées."""
    out = []
    for a in store.uncaptured_artifacts():
        e = _entry(a, captured=False)
        e.update({"x": a["x"], "y": a["y"], "village": a["village_name"]})
        out.append(e)
    return out


def treasury_status(v: V.Village) -> dict:
    """État de la trésorerie d'un village (pour la modale) : niveau, capacité, artefact
    stocké le cas échéant."""
    lvl = treasury_level(v)
    stored = store.artifact_in_village(v.id)
    return {
        "level": lvl,
        "slots": int(lvl >= TREASURY_SMALL) + int(lvl >= TREASURY_BIG),
        "can_store_small": lvl >= TREASURY_SMALL,
        "can_store_big": lvl >= TREASURY_BIG,
        "stored": (AT.describe(stored["kind"], stored["size"]) if stored else None),
    }


def catalogue() -> list[dict]:
    """Descriptif des 8 types d'artefacts (référence pour la modale trésorerie) :
    effet, portée (petit = village / grand·unique = compte) et magnitude par taille."""
    out = []
    for kind in sorted(AT.TYPES):
        t = AT.get(kind)
        out.append({
            "kind": kind, "name": t.name, "effect": t.effect,
            "desc": t.desc, "wired": t.wired, "numeric": t.numeric,
            "mag": {s: t.mag[s] for s in AT.SIZES},
        })
    return out


# --- Spawn : villages Natars détenteurs d'artefacts --------------------------
# Plan déterministe : 8 artefacts (un par type), tailles variées. Les villages
# détenteurs sont placés **vers le centre** (zone Natar interne), donc fortement
# gardés (cf. natars.garrison_for). Idempotent via `artifacts_exist`.
ARTIFACT_PLAN = [
    (1, "small"), (2, "small"), (3, "small"), (4, "large"),
    (5, "large"), (6, "small"), (7, "large"), (8, "unique"),
]


def spawn_artifact_villages(player_id: int, server_speed: int) -> list:
    """Crée des villages Natars dédiés (avec trésorerie) détenant les artefacts du plan.
    Déterministe (s'accroche aux vallées libres de l'anneau Natar interne)."""
    from app.engine import natars as NAT
    import random
    rng = random.Random(NAT.W.WORLD_SEED ^ 0x41525446)  # "ARTF"
    occupied = {(v["x"], v["y"]) for v in store.list_villages()}
    # Cibles vers le centre (anneau interne) : artefacts bien défendus.
    inner, outer = NAT.NATAR_ZONE_INNER, NAT.NATAR_ZONE_INNER + 8
    created = []
    for i, (kind, size) in enumerate(ARTIFACT_PLAN):
        import math
        radius = inner + (i % max(1, outer - inner))
        angle = 2 * math.pi * (i * 0.61803398875)
        cx = int(round(radius * math.cos(angle))) + rng.randint(-2, 2)
        cy = int(round(radius * math.sin(angle))) + rng.randint(-2, 2)
        spot = NAT._nearest_free_valley(cx, cy, occupied)
        if spot is None:
            continue
        x, y = spot
        occupied.add((x, y))
        tile = store.get_tile(x, y)
        layout = tile["layout"] if tile else "4-4-4-6"
        v = NAT._natar_village(f"Trésor natar {i + 1:02d}", x, y, player_id,
                               server_speed, layout)
        # Trésorerie sur le village détenteur (il garde l'artefact). Vrai T4.6 : les
        # villages-artefact Natars ont une trésorerie **niveau 20 dans tous les cas**,
        # même pour un petit artefact — il faut la raser (catapultes) pour voler l'artefact
        # (cf. try_capture, support.travian.com / unofficialtravian « Artefacts »).
        v.slots[22] = V.Slot(building_id=B.TREASURY, level=TREASURY_BIG)
        v = store.insert_village(v)
        store.insert_artifact(kind, size, v.id)
        created.append(v)
    return created
