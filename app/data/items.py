"""Objets du héros — catalogue (équipement + consommables).

⚠️ Kirilloid ne modélise PAS les objets du héros : ce catalogue est une
**approximation documentée** (au même titre que les marchands, cf. tribes.py),
inspirée des objets réels de Travian T4.6 (casque, armure, arme, bouclier, bottes,
monture + consommables). Les noms et la nature des bonus collent au vrai jeu ; les
**valeurs chiffrées sont inventées** et tunées pour rester cohérentes avec
l'échelle du moteur (force de combat ~100/point d'attribut, cf. engine.hero).

Emplacements d'équipement (un objet par emplacement) :
    head  casque · body armure · right arme · left bouclier/étendard ·
    shoes bottes · horse monture

Effets possibles (clé → sens) — appliqués par engine.hero.effective() :
    strength    force de combat plate (attaque ET défense du héros au combat)
    off         bonus d'attaque de l'armée accompagnée (fraction, 0.05 = +5 %)
    def         bonus de défense de l'armée du héros au village (fraction)
    regen       régénération de santé en +points/jour (s'ajoute à la base)
    speed       vitesse du héros en +cases/h (longs trajets / aventures)
    production  production de ressources en +unités/h (réparties, cf. hero)
    xp_bonus    gain d'XP d'aventure majoré (fraction)

Les **consommables** (slot "bag") ne s'équipent pas : ils se cumulent en quantité
et ont un effet immédiat à l'usage (soin). Seul le bandage est implémenté ;
les autres (cages, parchemins, œuvres d'art) sont listés comme butin/flavour.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    slot: str                 # head|body|right|left|shoes|horse|bag
    emoji: str
    bonuses: dict = field(default_factory=dict)
    weight: float = 1.0       # poids de tirage dans le butin (rare = faible)
    consumable: bool = False


# --- Catalogue ---------------------------------------------------------------
_ITEMS = [
    # Casques (head) : régénération / XP.
    Item("helmet_regen",   "Casque du soigneur",   "head",  "⛑️", {"regen": 5}, 6),
    Item("helmet_xp",      "Casque de l'archonte", "head",  "🪖", {"xp_bonus": 0.15}, 4),
    # Armures (body) : force / défense.
    Item("armor_segmented", "Armure segmentée",    "body",  "🛡️", {"strength": 250, "def": 0.05}, 6),
    Item("armor_scale",     "Armure d'écailles",   "body",  "🦎", {"strength": 400}, 3),
    # Armes (right) : attaque.
    Item("sword_short",    "Épée courte",          "right", "🗡️", {"strength": 200}, 7),
    Item("sword_great",    "Grande épée",          "right", "⚔️", {"strength": 450, "off": 0.05}, 3),
    Item("spear",          "Lance de cavalerie",   "right", "🔱", {"strength": 300, "off": 0.03}, 4),
    # Boucliers / étendards (left) : défense / attaque d'armée.
    Item("shield_round",   "Bouclier rond",        "left",  "🛡️", {"strength": 150, "def": 0.08}, 6),
    Item("standard",       "Étendard de guerre",   "left",  "🚩", {"off": 0.10}, 3),
    # Bottes (shoes) : vitesse / régénération.
    Item("boots_speed",    "Bottes de l'éclaireur", "shoes", "🥾", {"speed": 4}, 6),
    Item("boots_mercenary", "Bottes du mercenaire", "shoes", "👢", {"regen": 3, "speed": 2}, 4),
    # Montures (horse) : vitesse forte.
    Item("horse_pony",     "Poney",                "horse", "🐴", {"speed": 5}, 6),
    Item("horse_warhorse", "Destrier",             "horse", "🐎", {"speed": 8, "strength": 100}, 2),
    # Consommables (bag) : soin immédiat (+santé). Tirés en butin d'aventure aussi.
    Item("bandage",        "Bandage",              "bag",   "🩹", {"heal": 25}, 8, consumable=True),
    Item("ointment",       "Onguent",              "bag",   "🧴", {"heal": 50}, 4, consumable=True),
]

ITEMS: dict[str, Item] = {it.key: it for it in _ITEMS}

EQUIP_SLOTS = ("head", "body", "right", "left", "shoes", "horse")
SLOT_LABELS = {"head": "Casque", "body": "Armure", "right": "Arme",
               "left": "Bouclier", "shoes": "Bottes", "horse": "Monture",
               "bag": "Sac"}


def get(key: str) -> Item | None:
    return ITEMS.get(key)


def droppable() -> list[Item]:
    """Objets pouvant tomber en butin d'aventure (tout le catalogue)."""
    return list(_ITEMS)


def item_dict(key: str) -> dict | None:
    """Représentation sérialisable d'un objet (pour l'API/l'UI)."""
    it = ITEMS.get(key)
    if it is None:
        return None
    return {"key": it.key, "name": it.name, "slot": it.slot, "emoji": it.emoji,
            "bonuses": it.bonuses, "consumable": it.consumable,
            "slot_label": SLOT_LABELS.get(it.slot, it.slot)}
