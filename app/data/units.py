"""Unités T4.6 — tables résolues depuis la cascade Kirilloid (base→t3→t4).

Stats de combat (attaque, déf. infanterie/cavalerie, vitesse, capacité) issues de
base/units ; coûts des colons ajustés par t3 ; temps d'entraînement/recherche par t4.
Trois tribus jouables : Romains, Teutons, Gaulois.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .buildings import B
from .tribes import Tribe


@dataclass
class Unit:
    name: str
    attack: int
    def_inf: int        # défense contre l'infanterie
    def_cav: int        # défense contre la cavalerie
    speed: int          # cases/heure (vitesse 1)
    cost: tuple         # [bois, argile, fer, céréales]
    upkeep: int         # consommation de céréales/h
    train_time: int     # temps d'entraînement (s, vitesse 1, bâtiment niv 1)
    capacity: int       # capacité de pillage
    infantry: bool      # True = infanterie, False = cavalerie
    research_time: int  # temps de recherche en académie (s)
    producer: int       # bâtiment producteur
    is_scout: bool = False    # unité d'éclairage
    is_ram: bool = False      # bélier (cible la muraille)
    is_catapult: bool = False  # catapulte (cible un bâtiment)
    is_chief: bool = False    # sénateur/chef (réduit la loyauté)
    is_settler: bool = False  # colon (fonde un village)


# Ordre des unités identique à Travian. producer = bâtiment d'entraînement.
ROMANS = [
    Unit("Légionnaire",            40,  35,  50,  6, (120, 100, 150, 30),  1,  1600,  50, True,  6600, B.BARRACKS),
    Unit("Prétorien",              30,  65,  35,  5, (100, 130, 160, 70),  1,  1760,  20, True,  7080, B.BARRACKS),
    Unit("Imperian",               70,  40,  25,  7, (150, 160, 210, 80),  1,  1920,  50, True,  7560, B.BARRACKS),
    Unit("Equites Legati",          0,  20,  10, 16, (140, 160,  20, 40),  2,  1360,   0, False, 5880, B.STABLES, is_scout=True),
    Unit("Equites Imperatoris",   120,  65,  50, 14, (550, 440, 320, 100), 3,  2640, 100, False, 9720, B.STABLES),
    Unit("Equites Caesaris",      180,  80, 105, 10, (550, 640, 800, 180), 4,  3520,  70, False, 12360, B.STABLES),
    Unit("Bélier",                 60,  30,  75,  4, (900, 360, 500, 70),  3,  4600,   0, True,  15600, B.WORKSHOP, is_ram=True),
    Unit("Catapulte à feu",        75,  60,  10,  3, (950, 1350, 600, 90), 6,  9000,   0, True,  28800, B.WORKSHOP, is_catapult=True),
    Unit("Sénateur",               50,  40,  30,  4, (30750, 27200, 45000, 37500), 5, 90700, 0, True, 24475, B.RESIDENCE, is_chief=True),
    Unit("Colon",                   0,  80,  80,  5, (5800, 5300, 7200, 5500), 1, 26900, 3000, True, 0, B.RESIDENCE, is_settler=True),
]

TEUTONS = [
    Unit("Combattant à la massue", 40,  20,   5,  7, (95, 75, 40, 40),     1,   720,  60, True,  3960, B.BARRACKS),
    Unit("Lancier",                10,  35,  60,  7, (145, 70, 85, 40),    1,  1120,  40, True,  5160, B.BARRACKS),
    Unit("Combattant à la hache",  60,  30,  30,  6, (130, 120, 170, 70),  1,  1200,  50, True,  5400, B.BARRACKS),
    Unit("Éclaireur",               0,  10,   5,  9, (160, 100, 50, 50),   1,  1120,   0, True,  5160, B.BARRACKS, is_scout=True),
    Unit("Paladin",                55, 100,  40, 10, (370, 270, 290, 75),  2,  2400, 110, False, 9000, B.STABLES),
    Unit("Cavalier teuton",       150,  50,  75,  9, (450, 515, 480, 80),  3,  2960,  80, False, 10680, B.STABLES),
    Unit("Bélier",                 65,  30,  80,  4, (1000, 300, 350, 70), 3,  4200,   0, True,  14400, B.WORKSHOP, is_ram=True),
    Unit("Catapulte",              50,  60,  10,  3, (900, 1200, 600, 60), 6,  9000,   0, True,  28800, B.WORKSHOP, is_catapult=True),
    Unit("Chef",                   40,  60,  40,  4, (35500, 26600, 25000, 27200), 4, 70500, 0, True, 19425, B.RESIDENCE, is_chief=True),
    Unit("Colon",                  10,  80,  80,  5, (7200, 5500, 5800, 6500), 1, 31000, 3000, True, 0, B.RESIDENCE, is_settler=True),
]

GAULS = [
    Unit("Phalange",               15,  40,  50,  7, (100, 130, 55, 30),   1,  1040,  35, True,  4920, B.BARRACKS),
    Unit("Épéiste",                65,  35,  20,  6, (140, 150, 185, 60),  1,  1440,  45, True,  6120, B.BARRACKS),
    Unit("Éclaireur",               0,  20,  10, 17, (170, 150, 20, 40),   2,  1360,   0, False, 5880, B.STABLES, is_scout=True),
    Unit("Cavalier Theutates",     90,  25,  40, 19, (350, 450, 230, 60),  2,  2480,  75, False, 9240, B.STABLES),
    Unit("Druide-cavalier",        45, 115,  55, 16, (360, 330, 280, 120), 2,  2560,  35, False, 9480, B.STABLES),
    Unit("Haeduan",               140,  60, 165, 13, (500, 620, 675, 170), 3,  3120,  65, False, 11160, B.STABLES),  # déf inf 60 (override T4)
    Unit("Bélier",                 50,  30, 105,  4, (950, 555, 330, 75),  3,  5000,   0, True,  16800, B.WORKSHOP, is_ram=True),
    Unit("Catapulte de guerre",    70,  45,  10,  3, (960, 1450, 630, 90), 6,  9000,   0, True,  28800, B.WORKSHOP, is_catapult=True),
    Unit("Chef de clan",           40,  50,  50,  4, (30750, 45400, 31000, 37500), 4, 90700, 0, True, 24475, B.RESIDENCE, is_chief=True),
    Unit("Colon",                   0,  80,  80,  5, (5500, 7000, 5300, 4900), 1, 22700, 3000, True, 0, B.RESIDENCE, is_settler=True),
]

# Animaux sauvages des oasis (« Nature »). Ils ne servent qu'en défense :
# producer = -1 (non entraînables), pas de coût/recherche. Stats T4 (att, déf
# inf/cav, vitesse) issues de la table officielle des animaux Travian.
NATURE = [
    Unit("Rat",            10,  25,  20,  40, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Araignée",       20,  35,  40,  40, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Serpent",        60,  40,  60,  40, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Chauve-souris",  80,  66,  50,  20, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Sanglier",       50,  70,  33,  40, (0, 0, 0, 0), 2, 0,  0, True, 0, -1),
    Unit("Loup",          100,  80,  70,  50, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Ours",          250, 140, 200,  40, (0, 0, 0, 0), 3, 0,  0, True, 0, -1),
    Unit("Crocodile",     450, 380, 240,  40, (0, 0, 0, 0), 3, 0,  0, True, 0, -1),
    Unit("Tigre",         200, 170, 250,  40, (0, 0, 0, 0), 1, 0,  0, True, 0, -1),
    Unit("Éléphant",      600, 440, 520,  25, (0, 0, 0, 0), 5, 0,  0, True, 0, -1),
]

# Tribu Natars (PNJ des villages Natars de l'endgame). Comme la Nature, ces unités
# ne sont **pas entraînables** (producer = -1, pas de coût/recherche) : elles ne
# servent qu'à garnisonner les villages Natars en défense (et, plus tard, à attaquer
# les villages-Merveille). Kirilloid est **muet sur leurs stats de combat** (son
# modèle t4 n'a que 3 tribus jouables + Nature ; l'`extend` t4 n'ajoute aux Natars
# que temps/coût d'entraînement, inutiles ici). Stats (att, déf inf/cav, vitesse)
# recoupées sur le **wiki Fandom** (API MediaWiki, infoboxes des unités) :
#   https://travian.fandom.com/wiki/Natars (+ pages par unité : Pikeman, Guardsman, …)
# Les valeurs absentes du wiki (Éléphant de guerre, vitesses, Baliste/Empereur/Colon
# natarien) sont des **approximations documentées** au statut « table officielle des
# animaux » de la Nature. Ordre identique à Travian (= ordre de `troops[10]`).
NATARS = [
    Unit("Piquier",              20,  35,  50,  6, (0, 0, 0, 0), 1, 0,    0, True,  0, -1),
    Unit("Guerrier épineux",     65,  20,  10,  6, (0, 0, 0, 0), 1, 0,   50, True,  0, -1),
    Unit("Garde",               100,  90,  75,  7, (0, 0, 0, 0), 1, 0,   50, True,  0, -1),
    Unit("Rapace",                0,  10,   0, 25, (0, 0, 0, 0), 1, 0,    0, True,  0, -1, is_scout=True),
    Unit("Cavalier à hache",    155,  80,  50, 14, (0, 0, 0, 0), 2, 0,   80, False, 0, -1),
    Unit("Chevalier natarien",  170, 140,  80, 14, (0, 0, 0, 0), 3, 0,   80, False, 0, -1),
    Unit("Éléphant de guerre",  250, 120, 150, 10, (0, 0, 0, 0), 6, 0,   80, True,  0, -1),
    Unit("Baliste",              60,  45,  10,  3, (0, 0, 0, 0), 6, 0,    0, True,  0, -1, is_catapult=True),
    Unit("Empereur natarien",    80,  50,  50,  4, (0, 0, 0, 0), 5, 0,    0, True,  0, -1, is_chief=True),
    Unit("Colon natarien",       30,  40,  60,  5, (0, 0, 0, 0), 1, 0, 3000, True,  0, -1, is_settler=True),
]

UNITS: dict[Tribe, list[Unit]] = {
    Tribe.ROMANS: ROMANS,
    Tribe.TEUTONS: TEUTONS,
    Tribe.GAULS: GAULS,
    Tribe.NATURE: NATURE,
    Tribe.NATARS: NATARS,
}
