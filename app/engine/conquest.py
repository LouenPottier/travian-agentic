"""Conquête de village : loyauté + administrateurs (sénateur / chef / chef de clan).

⚠️ **Source des chiffres** : kirilloid ne modélise PAS la conquête. Toutes les
valeurs ci-dessous viennent de la **doc officielle / wiki communautaire** (cf.
hiérarchie des sources, CLAUDE.md), recoupées et citées :

- Réduction de loyauté par administrateur **survivant**, sur **attaque normale**
  (jamais razzia) : Sénateur (Romains) 20–30 %, Chef (Teutons) 20–25 %, Chef de clan
  (Gaulois) 20–25 %. (support.travian.com « Administrator » ; travianlibrary)
- Régénération : **+⅔ × niveau du bâtiment d'administration** (résidence/palais) par
  heure — un bâtiment niv 6 ⇒ +4/h. (support.travian.com « Loyalty » ; appliquée
  paresseusement dans `village._accumulate`.)
- Conditions de conquête (https://unofficialtravian.com/2025/10/conquering-villages/,
  recoupé support.travian.com) : **attaque normale**, au moins un administrateur
  **survit**, la cible n'a **plus de bâtiment d'administration actif** (résidence/palais
  détruit dans la même attaque suffit ⇒ on évalue **après** le siège), la cible **n'est
  pas une capitale**, **ni l'unique village** du défenseur, et l'attaquant a **assez de
  points de culture + un emplacement d'expansion** libre.
- À **0 %** : changement de propriétaire. L'administrateur disparaît ; les troupes
  survivantes de l'attaquant **garnisonnent** le village conquis ; **toutes les troupes
  du village conquis sont perdues** (y compris en déplacement / en file) ; recherche
  (académie) et améliorations (forge) **réinitialisées** ; **mur supprimé** ; **bâtiments
  spécifiques à l'ancienne tribu supprimés** (si tribu différente) ; **oasis annexées
  libérées** ; le village **adopte la tribu** du conquérant et **n'est jamais capitale**.

`RESET_LOYALTY` (loyauté après conquête) n'est pas chiffré par les sources consultées :
**approximation documentée** = 25 (valeur communément citée pour empêcher une re-conquête
immédiate, le temps que la loyauté remonte une fois la résidence reconstruite).

Simplifications documentées : les renforts que le village conquis avait stationnés
**dans d'autres villages** ne sont pas tracés individuellement ⇒ non détruits ici
(on ne perd que `troops` + `away` + files du village lui-même). Les administrateurs
gaulois/teutons/romains piégés au combat ne donnent pas de prisonniers à un défenseur
qui vient d'être conquis (cf. movement._resolve_battle).
"""
from __future__ import annotations

import random as _random

from app import store
from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe
from app.data.units import UNITS
from app.engine import village as V

# Plage de réduction de loyauté (%) par administrateur survivant, par tribu.
LOYALTY_DROP = {
    Tribe.ROMANS: (20, 30),   # Sénateur
    Tribe.TEUTONS: (20, 25),  # Chef
    Tribe.GAULS: (20, 25),    # Chef de clan
}

# Loyauté d'un village fraîchement conquis (approximation documentée, cf. en-tête).
RESET_LOYALTY = 25.0


def chief_indices(tribe: Tribe) -> list[int]:
    """Index des unités « administrateur » (sénateur/chef) de la tribu."""
    return [i for i, u in enumerate(UNITS[tribe]) if u.is_chief]


def count_chiefs(tribe: Tribe, units: list[int]) -> int:
    """Nombre d'administrateurs dans un vecteur d'effectifs de cette tribu."""
    return sum(units[i] for i in chief_indices(tribe))


def loyalty_drop(tribe: Tribe, n_chiefs: int, rng: _random.Random | None = None) -> float:
    """Réduction totale de loyauté (%) pour `n_chiefs` administrateurs survivants.

    Chaque administrateur tire indépendamment dans la plage de sa tribu (vrai Travian).
    `rng` injectable pour des tests déterministes."""
    if n_chiefs <= 0:
        return 0.0
    lo, hi = LOYALTY_DROP.get(tribe, (20, 25))
    r = rng or _random
    return float(sum(r.randint(lo, hi) for _ in range(n_chiefs)))


def conquer_eligible(target: V.Village, attacker_player_id: int,
                     now: float) -> tuple[bool, str]:
    """La cible peut-elle être conquise par cet attaquant (hors survie du chef, gérée
    par l'appelant après combat) ? Renvoie (ok, raison_si_non)."""
    if V.admin_building_level(target) > 0:
        return False, "bâtiment d'administration encore debout"
    if target.is_capital:
        return False, "on ne conquiert pas une capitale"
    if len(store.player_villages(target.player_id)) <= 1:
        return False, "unique village du défenseur"
    # Culture + emplacement d'expansion (import local : expansion dépend de village).
    from app.engine import expansion as EXP
    if not EXP.expansion_status(attacker_player_id, now)["can_settle"]:
        return False, "points de culture ou emplacement d'expansion insuffisants"
    return True, ""


def _is_old_tribe_building(building_id: int, new_tribe: Tribe) -> bool:
    """Bâtiment spécifique à une tribu ≠ celle du conquérant (à supprimer)."""
    b = BLD.get(building_id)
    return b.tribe is not None and b.tribe != new_tribe


def conquer_village(target: V.Village, attacker_player_id: int, attacker_tribe: Tribe,
                    survivors: list[int], now: float) -> dict:
    """Applique le changement de propriétaire (loyauté tombée à 0). Modifie `target`
    en place (l'appelant le sauvegarde) et renvoie un récap pour les rapports.

    `survivors` = effectifs survivants de l'attaquant (indexés sur sa tribu). Les
    administrateurs en sont retirés (ils disparaissent) ; le reste garnisonne."""
    old_owner = target.player_id
    old_tribe = target.tribe
    old_name = target.name

    # Garnison = survivants moins les administrateurs (qui disparaissent).
    chiefs = set(chief_indices(attacker_tribe))
    garrison = [0 if i in chiefs else survivors[i] for i in range(10)]

    # Oasis annexées : libérées (redeviennent indépendantes).
    freed_oases = list(target.oases)
    for o in target.oases:
        store.set_tile_owner(o["x"], o["y"], None)
    target.oases = []

    # Armées du village conquis : perdues, y compris en déplacement (suppression des
    # mouvements partant d'ici pour éviter des retours fantômes).
    store.delete_movements_by_origin(target.id)

    # Bâtiments : mur supprimé ; bâtiments de l'ancienne tribu supprimés si la tribu
    # change ; recherche/forge réinitialisées.
    target.slots.pop(V.WALL_SLOT, None)
    if attacker_tribe != old_tribe:
        for si in [si for si, s in target.slots.items()
                   if _is_old_tribe_building(s.building_id, attacker_tribe)]:
            target.slots.pop(si, None)

    # Changement de propriétaire & remise à zéro de l'état militaire.
    target.player_id = attacker_player_id
    target.tribe = attacker_tribe
    target.is_capital = False
    target.troops = garrison
    target.away = [0] * 10
    target.training = []
    target.research = [0] * 10
    target.research_queue = []
    target.upgrades = [0] * 10
    target.upgrade_queue = []
    target.traps = 0
    target.trap_queue = []
    target.prisoners = []
    target.loyalty = RESET_LOYALTY
    target.updated_at = now

    return {"old_owner": old_owner, "old_tribe": int(old_tribe),
            "new_owner": attacker_player_id, "new_tribe": int(attacker_tribe),
            "village": old_name, "garrison": garrison,
            "oases_freed": [[o["x"], o["y"]] for o in freed_oases]}
