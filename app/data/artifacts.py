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
t4 s'arrête aux 3 tribus + Nature). Tout ce module est donc une **approximation
documentée** (même statut que le héros / la brasserie, cf. CLAUDE.md), calquée sur la
*mécanique* réelle décrite par la doc officielle / wiki communautaire :
- **support.travian.com** « Artefacts » (autorité de comportement) ;
- **unofficialtravian.com** et **travian.fandom.com/wiki/Artifacts** (détails/valeurs).

Les 8 effets correspondent aux pouvoirs canoniques de Travian (durabilité des
bâtiments, vitesse des troupes, consommation de céréales, vitesse d'entraînement,
stockage, cachette, espionnage, confusion). Tous sont **catalogués** ici ; seuls
ceux marqués `wired=True` sont **réellement branchés** dans le moteur pour l'instant
(durabilité, vitesse, céréales). Les autres restent à brancher (comme les bonus
d'alliance à venir), au même statut « effet documenté, non encore actif ».
"""
from __future__ import annotations

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


# Catalogue des 8 types. Les magnitudes sont des **approximations documentées**
# (cf. en-tête) ; pour les effets « durabilité » et « vitesse » ce sont des
# **multiplicateurs** (×3/×4/×5, ×1,5/×2/×2), pour « céréales » un **facteur de
# consommation** (×0,5 : moitié moins de blé), etc.
TYPES: dict[int, ArtifactType] = {
    1: ArtifactType(1, "Artefact de l'architecte", "durability",
                    "durabilité des bâtiments (résiste mieux au siège)",
                    {"small": 3.0, "large": 4.0, "unique": 5.0}, wired=True),
    2: ArtifactType(2, "Bottes ailées", "speed",
                    "vitesse de déplacement des troupes",
                    {"small": 1.5, "large": 2.0, "unique": 2.0}, wired=True),
    3: ArtifactType(3, "Diète du soldat", "crop",
                    "consommation de céréales des troupes",
                    {"small": 0.5, "large": 0.5, "unique": 0.5}, wired=True),
    4: ArtifactType(4, "Artefact de l'entraîneur", "training",
                    "temps d'entraînement des troupes",
                    {"small": 0.5, "large": 0.5, "unique": 0.5}, wired=False),
    5: ArtifactType(5, "Artefact du bâtisseur", "storage",
                    "capacité des entrepôts et greniers",
                    {"small": 2.0, "large": 2.0, "unique": 3.0}, wired=False),
    6: ArtifactType(6, "Artefact du cartographe", "cranny",
                    "capacité des cachettes",
                    {"small": 2.0, "large": 2.0, "unique": 3.0}, wired=False),
    7: ArtifactType(7, "Œil de l'aigle", "spy",
                    "espionnage : visibilité des mouvements ennemis",
                    {"small": 1.0, "large": 1.0, "unique": 1.0}, wired=False),
    8: ArtifactType(8, "Artefact de confusion", "confuser",
                    "neutralise les artefacts ennemis (unique)",
                    {"small": 1.0, "large": 1.0, "unique": 1.0}, wired=False),
}


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
