"""Description de l'effet d'un bâtiment à un niveau donné (« ce qu'il fait »).

Utilisé par l'API pour afficher, dans la modale d'un bâtiment, son effet au niveau
actuel et au niveau suivant. Les valeurs (production, capacités, %) proviennent des
formules kirilloid déjà portées (`app.data.formulas`) ; les libellés sont en français.

Ce module ne fait que *décrire* : il ne modifie aucun état.
"""
from __future__ import annotations

from app.data import buildings as BLD
from app.data import formulas as F
from app.data.buildings import B
from app.data.tribes import MERCHANT_CAPACITY

RES_FR = ("bois", "argile", "fer", "céréales")
_REFINE = {B.SAWMILL: "bois", B.BRICKYARD: "argile",
           B.IRONFOUNDRY: "fer", B.GRAINMILL: "céréales", B.BAKERY: "céréales"}


def _pct(x: float) -> str:
    """Formate un pourcentage (1 décimale si non entier)."""
    return f"{x:.0f}" if abs(x - round(x)) < 1e-6 else f"{x:.1f}"


def building_effect(v, building_id: int, level: int) -> str:
    """Phrase décrivant l'effet du bâtiment `building_id` au niveau `level`."""
    if level < 1:
        return "non construit"
    b = BLD.get(building_id)

    # Champs de ressources : production horaire (échelle de base).
    if building_id in (B.WOODCUTTER, B.CLAYPIT, B.IRONMINE, B.CROPLAND):
        res = RES_FR[(B.WOODCUTTER, B.CLAYPIT, B.IRONMINE, B.CROPLAND).index(building_id)]
        return f"production {F.prod4(level)} {res}/h"

    # Raffinage : bonus de production en % d'un type de ressource.
    if building_id in _REFINE:
        return f"+{_pct(F.p5(level))} % {_REFINE[building_id]}"

    # Stockage.
    if building_id in (B.WAREHOUSE, B.GREAT_WAREHOUSE):
        return f"capacité {F.capacity(level) if building_id == B.WAREHOUSE else F.great_capacity(level)}"
    if building_id in (B.GRANARY, B.GREAT_GRANARY):
        return f"capacité {F.capacity(level) if building_id == B.GRANARY else F.great_capacity(level)}"
    if building_id == B.CRANNY:
        return f"cache {F.cranny(level)} / ressource"

    # Réduction des temps de construction (bâtiment principal & assimilés).
    if building_id == B.MAIN_BUILDING:
        red = (1 - F.mb_like(level)) * 100
        return f"−{_pct(red)} % temps de construction"
    if building_id in (B.TOWNHALL,):
        return "organise des fêtes (points de culture)"

    # Bâtiments d'entraînement : réduction du temps d'entraînement.
    if building_id in (B.BARRACKS, B.STABLES, B.WORKSHOP,
                       B.GREAT_BARRACKS, B.GREAT_STABLES):
        red = (1 - F.train_bonus(level)) * 100
        kind = {B.BARRACKS: "infanterie", B.GREAT_BARRACKS: "infanterie",
                B.STABLES: "cavalerie", B.GREAT_STABLES: "cavalerie",
                B.WORKSHOP: "machines de siège"}[building_id]
        return f"entraîne l'{kind} · −{_pct(red)} % temps d'entraînement"

    if building_id == B.ACADEMY:
        return "recherche des unités militaires (débloque l'entraînement)"
    if building_id == B.SMITHY:
        return f"améliore les unités jusqu'au niveau {level} (attaque & défense)"

    if building_id == B.MARKETPLACE:
        return f"{level} marchand(s) · capacité {MERCHANT_CAPACITY.get(v.tribe, 0)}/marchand"
    if building_id == B.TRADE_OFFICE:
        return f"+{_pct(F.p10(level))} % capacité des marchands"
    if building_id == B.EMBASSY:
        return f"alliance : jusqu'à {3 * level} membres"

    if building_id == B.RALLY_POINT:
        return "envoi et réception des armées"
    if building_id == B.ARENA:
        return f"+{_pct(F.p10(level))} % vitesse des troupes (longs trajets)"

    if building_id == B.RESIDENCE:
        bn = F.residence_benefit(level)
        return f"défense +{bn['def']} · {bn['slots']} emplacement(s) d'expansion"
    if building_id == B.PALACE:
        bn = F.palace_benefit(level)
        return f"défense +{bn['def']} · {bn['slots']} emplacement(s) · désigne la capitale"
    if building_id == B.TREASURY:
        return f"{F.slots2(level)} emplacement(s) de trésor (artéfacts / merveille)"
    if building_id == B.HERO_MANSION:
        return f"héros & occupation d'oasis · {F.slots3(level)} emplacement(s)"

    if b.slot == "wall":
        bn = b.benefit(level)
        return f"+{_pct(bn['def_bonus'] * 100)} % défense · +{_pct(bn['def'])} déf. de base"
    if building_id == B.STONEMASON:
        return f"+{_pct(F.p10(level))} % durabilité des bâtiments"
    if building_id == B.BREWERY:
        return f"+{_pct(F.percent(1)(level))} % attaque (Teutons) · fêtes"
    if building_id == B.TRAPPER:
        return f"jusqu'à {F.trapper_traps(level)} pièges"
    if building_id == B.HORSE_POOL:
        return "réduit la consommation de blé de la cavalerie (Romains)"

    return b.name
