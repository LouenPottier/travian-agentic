"""Artefacts T4.6 (endgame Natars) — catalogue des 8 types × 3 tailles.

Les **artefacts** sont détenus par des villages Natars dédiés ; le **héros** les
capture en remportant une attaque, à condition d'avoir une **trésorerie** assez grande
et **vide** pour les stocker (cf. `app/engine/artifacts.py`). Chaque artefact existe en
trois **tailles** :
- **petit** (`small`) : n'affecte que **le village** où il est stocké ;
- **grand** (`large`) : affecte **tout le compte** ;
- **unique** (`unique`) : effet de compte, le plus puissant (un seul par serveur dans
  le vrai jeu — ici simplement la plus forte des trois valeurs).

⚠️ **Source des chiffres** : kirilloid **ne modélise pas** les artefacts (son modèle
t4 s'arrête aux 3 tribus + Nature). Les **valeurs de jeu** (magnitudes, portées) sont
donc reprises de la doc officielle / wiki communautaire (autorité de comportement) :
- **support.travian.com** « Artefacts » / « Artefact Effects »
  (support.travian.com/en/articles/102-artefact-effects) ;
- **travian.fandom.com/wiki/Artifacts** et **unofficialtravian.com** (détails/valeurs).

**Portée par taille** (recoupée, cf. support « Artefact Effects ») :
- **petit** (`small`) : **un seul village** (celui qui détient l'artefact) ;
- **grand** (`large`) : **tout le compte** (tous les villages) ;
- **unique** (`unique`) : **tout le compte** aussi, mais **effet plus fort**.
⚠️ Contre-intuition **fidèle** : le **petit** a souvent la **plus forte magnitude**
(concentrée sur un village) ; le **grand** est plus faible mais s'applique partout ;
l'**unique** est fort ET partout. (ex. durabilité : petit ×4, grand ×3, unique ×5.)

Les 8 effets correspondent aux pouvoirs canoniques de Travian (durabilité des
bâtiments, vitesse d'entraînement, consommation de céréales, vitesse des troupes,
espionnage, grand entrepôt/grenier, cachette, artefact du fou). Tous sont **catalogués**
ici et **tous branchés** (`wired=True`) dans le moteur : durabilité, vitesse, céréales,
grand entrepôt/grenier, entraînement, cachette, espionnage, et l'**artefact du fou**
(effet aléatoire, cf. `fool_current`). Les effets dont la magnitude exacte n'est pas
publiée (entraînement) ou n'est pas un facteur chiffrable (grand entrepôt = permission,
fou = aléatoire) sont marqués `numeric=False` (portée seule affichée, pas de « ×N »).
"""
from __future__ import annotations

import random as _random
from dataclasses import dataclass

SIZES = ("small", "large", "unique")
SIZE_FR = {"small": "petit", "large": "grand", "unique": "unique"}


@dataclass(frozen=True)
class ArtifactType:
    kind: int          # 1..8
    name: str
    effect: str        # clé d'effet (cf. engine.artifacts)
    desc: str          # libellé court (FR)
    mag: dict          # magnitude de l'effet par taille {small/large/unique}
    wired: bool        # True = effet réellement appliqué par le moteur
    numeric: bool = True  # True = magnitude chiffrée affichable (× …) ; False = portée seule


# Catalogue des 8 types (valeurs = doc officielle / wiki, cf. en-tête). Selon l'effet la
# `mag` est un **multiplicateur** (durabilité ×4/×3/×5, vitesse ×2/×1,5/×2, espionnage
# ×5/×3/×10, cachette ×200/×100/×500) ou un **facteur de consommation** (céréales
# ×0,5/×0,75/×0,5 : plus bas = mieux). Pour « entraînement » (valeur exacte non publiée),
# « grand entrepôt/grenier » (permission, pas un facteur) et « fou » (effet aléatoire),
# `numeric=False` : on affiche l'effet et la portée, pas de chiffre inventé.
TYPES: dict[int, ArtifactType] = {
    1: ArtifactType(1, "Artefact de l'architecte", "durability",
                    "durabilité des bâtiments contre béliers/catapultes",
                    {"small": 4.0, "large": 3.0, "unique": 5.0}, wired=True),
    2: ArtifactType(2, "Bottes ailées", "speed",
                    "vitesse de déplacement des troupes",
                    {"small": 2.0, "large": 1.5, "unique": 2.0}, wired=True),
    3: ArtifactType(3, "Diète du soldat", "crop",
                    "consommation de céréales des troupes",
                    {"small": 0.5, "large": 0.75, "unique": 0.5}, wired=True),
    4: ArtifactType(4, "Artefact de l'entraîneur", "training",
                    "vitesse d'entraînement des troupes",
                    {"small": 0.5, "large": 0.66, "unique": 0.5}, wired=True,
                    numeric=False),
    5: ArtifactType(5, "Artefact du bâtisseur", "storage",
                    "permet de bâtir le grand entrepôt / grand grenier",
                    {"small": 1.0, "large": 1.0, "unique": 1.0}, wired=True,
                    numeric=False),
    6: ArtifactType(6, "Artefact du cartographe", "cranny",
                    "capacité des cachettes",
                    {"small": 200.0, "large": 100.0, "unique": 500.0}, wired=True),
    7: ArtifactType(7, "Œil de l'aigle", "spy",
                    "efficacité des éclaireurs (espionnage et défense)",
                    {"small": 5.0, "large": 3.0, "unique": 10.0}, wired=True),
    8: ArtifactType(8, "Artefact du fou", "fool",
                    "effet aléatoire, change toutes les 24 h",
                    {"small": 1.0, "large": 1.0, "unique": 1.0}, wired=True,
                    numeric=False),
}

# Effet du **fou** : il prend, par fenêtres de 24 h de jeu, l'un des effets **chiffrables**
# des autres artefacts (jamais « grand entrepôt » ni lui-même — règle officielle
# support.travian.com « Artefacts »). Magnitude = celle de l'effet retenu à la taille du
# fou. ⚠️ Simplifications documentées : effets **positifs uniquement** (le vrai fou peut
# être négatif pour petit/grand) et **portée figée** à la taille de l'artefact (le vrai jeu
# tire aussi la portée village/compte).
FOOL_EFFECTS = ("durability", "speed", "crop", "training", "cranny", "spy")
_KIND_OF_EFFECT = {t.effect: k for k, t in TYPES.items()}


def fool_current(art_id: int, size: str, now: float, server_speed: int) -> tuple[str, float]:
    """Effet + magnitude **courants** de l'artefact du fou (`kind==8`), tirés au sort
    toutes les **24 h de jeu** (fenêtre réelle = 86400 ÷ vitesse serveur), de façon
    **déterministe** par (id d'artefact, fenêtre) ⇒ stable tant qu'on ne change pas de
    fenêtre, sans état persistant. Renvoie (clé d'effet, magnitude à la taille `size`)."""
    window = 86400.0 / max(1, server_speed)
    bucket = int(now // window)
    rng = _random.Random((int(art_id) * 2654435761) ^ bucket)
    effect = rng.choice(FOOL_EFFECTS)
    return effect, TYPES[_KIND_OF_EFFECT[effect]].mag[size]


def get(kind: int) -> ArtifactType:
    return TYPES[kind]


def magnitude(kind: int, size: str) -> float:
    return TYPES[kind].mag[size]


def scope(size: str) -> str:
    """Portée d'un artefact selon sa taille : « village » (petit) ou « compte »."""
    return "village" if size == "small" else "account"


def describe(kind: int, size: str) -> str:
    """Libellé complet (pour l'API / l'UI), ex. « Bottes ailées (grand) »."""
    t = TYPES[kind]
    return f"{t.name} ({SIZE_FR[size]})"
