"""Tribus jouables T4.6. (Égyptiens/Huns = extension 4.4, à ajouter plus tard.)"""
from __future__ import annotations

from enum import IntEnum


class Tribe(IntEnum):
    ROMANS = 0
    TEUTONS = 1
    GAULS = 2
    NATURE = 3  # animaux sauvages des oasis (non jouable, défense uniquement)


# Tribus jouables par un humain (exclut la Nature).
PLAYABLE_TRIBES = (Tribe.ROMANS, Tribe.TEUTONS, Tribe.GAULS)


TRIBE_NAMES_FR = {
    Tribe.ROMANS: "Romains",
    Tribe.TEUTONS: "Teutons",
    Tribe.GAULS: "Gaulois",
    Tribe.NATURE: "Nature",
}


# --- Commerce (place de marché) ----------------------------------------------
# Kirilloid ne modélise PAS les marchands ; ces valeurs viennent du vrai Travian
# T4.6 (cf. wiki). Capacité = ressources transportées par un marchand ; vitesse en
# cases/h. Le nombre de marchands disponibles = niveau de la place de marché
# (1 marchand au niveau 1, +1 par niveau). Le comptoir commercial ajoute +10 %/niveau
# de capacité. La capacité n'est PAS multipliée par la vitesse serveur : dans ce
# moteur les stocks (entrepôt) restent à l'échelle de base, donc la capacité aussi —
# ce qui reproduit l'équilibre réel (on ne vide jamais un entrepôt plein d'un coup).
MERCHANT_CAPACITY = {Tribe.ROMANS: 500, Tribe.TEUTONS: 1000, Tribe.GAULS: 750}
MERCHANT_SPEED = {Tribe.ROMANS: 16, Tribe.TEUTONS: 12, Tribe.GAULS: 24}  # cases/h
