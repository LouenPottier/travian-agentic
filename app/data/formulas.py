"""Formules de jeu T4.6 — portage fidèle de github.com/kirilloid/travian.

Toutes les constantes et formules proviennent du modèle T4 de Kirilloid (la
référence communautaire pour les mécaniques exactes de Travian). Voir le
CLAUDE.md du dépôt pour la stratégie de fidélité.

Note d'arrondi : Kirilloid s'appuie sur `Math.round` de JavaScript, qui arrondit
les demis vers le haut (round(2.5) == 3, round(-2.5) == -2). Python utilise
l'arrondi bancaire, on réimplémente donc explicitement `js_round`.
"""
from __future__ import annotations

import math


def js_round(x: float) -> int:
    """Réplique `Math.round` de JS : arrondi du demi vers +infini."""
    return math.floor(x + 0.5)


def round_p(precision: float):
    """`roundP(p)` de Kirilloid : arrondit au multiple de `precision` le plus proche."""
    return lambda n: precision * js_round(n / precision)


round5 = round_p(5)
round10 = round_p(10)
round100 = round_p(100)


def make_time(a: float, k: float = 1.16, b: float = 1875):
    """Temps de construction de base (en s, vitesse 1, BP niveau 0).

    `time(a, k, b)(level) = a * k**(level-1) - b`
    """
    return lambda level: a * k ** (level - 1) - b


# Production horaire d'un champ de ressource par niveau (T3).
# En T4, la production est multipliée par 1.4 (cf. prod4).
_PROD = [2, 5, 9, 15, 22, 33, 50, 70, 100, 145, 200,
         280, 375, 495, 635, 800, 1000, 1300, 1600, 2000, 2450, 3050]


def prod(level: int) -> int:
    return _PROD[level]


def prod4(level: int) -> int:
    """Production horaire d'un champ en T4 (= T3 * 1.4, arrondi)."""
    return js_round(prod(level) * 1.4)


def capacity(level: int) -> int:
    """Capacité entrepôt/grenier : roundP(100)(2120 * 1.2**lvl - 1320)."""
    return round100(2120 * 1.2 ** level - 1320)


def great_capacity(level: int) -> int:
    """Grand entrepôt / grand grenier : 3x la capacité normale."""
    return 3 * capacity(level)


def cranny(level: int) -> int:
    """Capacité de cachette (par ressource).

    NB : la formule Kirilloid `roundP(10)(129.17**(lvl-1))` est buguée (donne 0
    puis explose). On utilise la formule réelle de Travian, qui reproduit la table
    officielle (200, 260, 340, 440, 570, 740, 970, 1260, 1640, 2120…).
    """
    return round10(200 * 1.3 ** (level - 1))


def percent(m: float):
    """Bonus linéaire en % : m * niveau."""
    return lambda level: m * level


p5 = percent(5)
p10 = percent(10)


def train_bonus(level: int) -> float:
    """Réduction du temps d'entraînement (caserne/écurie/atelier) : 0.9**(lvl-1)."""
    return 0.9 ** (level - 1)


def mb_like(level: int) -> float:
    """Réduction du temps de construction (bâtiment principal & co) : 0.964**(lvl-1)."""
    return 0.964 ** (level - 1)


def identity(level: int) -> int:
    return level


def slots2(level: int) -> int:
    """Slots d'expansion (résidence) : +1 au niv 10, +1 au niv 20."""
    return int(level >= 10) + int(level >= 20)


def slots3(level: int) -> int:
    """Slots d'expansion (palais / mansion héros) : niv 10, 15, 20."""
    return int(level >= 10) + int(level >= 15) + int(level >= 20)


def residence_benefit(level: int) -> dict:
    return {"slots": slots2(level), "def": 2 * level ** 2}


def palace_benefit(level: int) -> dict:
    return {"slots": slots3(level), "def": 2 * level ** 2}


def wall4(base: float, num: float):
    """Mur en T4 : bonus défensif multiplicatif + défense de base plate.

    defBonus = roundP(0.001)(base**lvl) - 1 ; def = num * lvl
    """
    r001 = round_p(0.001)
    return lambda level: {"def_bonus": r001(base ** level) - 1, "def": num * level}


def trapper_traps(level: int) -> int:
    """Nombre de pièges (trappeur gaulois), formule quadratique T3.5."""
    if level > 10:
        return (level * level + 19 * level + 20) // 2
    return (level * level + 21 * level - 2) // 2
