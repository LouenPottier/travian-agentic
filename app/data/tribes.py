"""Tribus jouables T4.6. (Égyptiens/Huns = extension 4.4, à ajouter plus tard.)"""
from __future__ import annotations

from enum import IntEnum


class Tribe(IntEnum):
    ROMANS = 0
    TEUTONS = 1
    GAULS = 2


TRIBE_NAMES_FR = {
    Tribe.ROMANS: "Romains",
    Tribe.TEUTONS: "Teutons",
    Tribe.GAULS: "Gaulois",
}
