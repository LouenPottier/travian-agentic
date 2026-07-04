"""Mouvements d'armées : envoi, trajet, résolution du combat, butin, retour, rapports.

Trajet : temps = distance (cases) / vitesse de la troupe la plus lente (cases/h),
accéléré par la vitesse serveur. Le combat à l'arrivée utilise le moteur validé
`engine.combat`. Les survivants repartent vers le village d'origine avec le butin.
"""
from __future__ import annotations

import json
import math
import threading
import time as _time

from app import store
from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe, MERCHANT_CAPACITY, MERCHANT_SPEED
from app.data.units import UNITS
from app.engine import combat as C
from app.engine import village as V
from app.engine import world as W


def distance(ax, ay, bx, by) -> float:
    return math.hypot(ax - bx, ay - by)


def army_speed(tribe, units: list[int]) -> float:
    speeds = [UNITS[tribe][i].speed for i, n in enumerate(units) if n > 0]
    return min(speeds) if speeds else 1.0


# Place de tournoi (vrai T4.6, support.travian.com / unofficialtravian) : les
# **20 premières cases** sont parcourues à vitesse normale ; au-delà, la vitesse est
# augmentée de +20 %/niveau (cf. buildings ARENA.benefit). Le bonus est celui du
# village **d'origine** de l'armée (ses troupes), aller comme retour.
TOURNAMENT_FREE_FIELDS = 20


def arena_level(v) -> int:
    return V.building_levels(v).get(B.ARENA, 0)


def _arena_multiplier(level: int) -> float:
    return 1.0 + BLD.get(B.ARENA).benefit(level) / 100.0 if level > 0 else 1.0


def _leg_seconds(d: float, speed: float, server_speed, arena: int = 0) -> float:
    """Temps de trajet (s) pour `d` cases à `speed` cases/h, ÷ vitesse serveur, avec
    le bonus de la place de tournoi (niveau `arena`) au-delà des 20 premières cases."""
    mult = _arena_multiplier(arena)
    if d <= TOURNAMENT_FREE_FIELDS or mult <= 1.0:
        hours = d / speed
    else:
        hours = (TOURNAMENT_FREE_FIELDS / speed
                 + (d - TOURNAMENT_FREE_FIELDS) / (speed * mult))
    return hours * 3600.0 / server_speed


def travel_seconds(ax, ay, bx, by, tribe, units, server_speed, arena: int = 0) -> float:
    return _leg_seconds(distance(ax, ay, bx, by), army_speed(tribe, units),
                        server_speed, arena)


class MoveError(Exception):
    pass


# --- Commerce (place de marché) ---------------------------------------------
def merchant_capacity(v) -> int:
    """Cargaison maximale d'un marchand : base tribu, +10 %/niveau de comptoir
    commercial. Non multipliée par la vitesse serveur (cf. tribes.MERCHANT_CAPACITY)."""
    office = V.building_levels(v).get(B.TRADE_OFFICE, 0)
    return round(MERCHANT_CAPACITY[v.tribe] * (1 + 0.10 * office))


def merchants_total(v) -> int:
    """Nombre total de marchands = niveau de la place de marché."""
    return V.building_levels(v).get(B.MARKETPLACE, 0)


def merchants_available(v) -> int:
    """Marchands libres = total − ceux en route (aller ou retour)."""
    return merchants_total(v) - store.merchants_out(v.id)


def merchant_seconds(ax, ay, bx, by, tribe, server_speed) -> float:
    """Durée d'un trajet de marchands (vitesse propre à la tribu, pas celle des troupes)."""
    return distance(ax, ay, bx, by) / MERCHANT_SPEED[tribe] * 3600.0 / server_speed


def send_resources(origin_id: int, target_id: int, player_id: int,
                   amounts: list[int], now: float | None = None) -> dict:
    """Envoie des ressources d'un de tes villages vers un village (le tien ou un autre).

    Marchands mobilisés = plafond(total / capacité d'un marchand) ; ils restent
    indisponibles jusqu'à leur retour à vide. Les ressources quittent l'origine
    immédiatement ; elles sont déposées (et plafonnées par le stockage) à l'arrivée.
    """
    now = now or _time.time()
    origin = store.load_village(origin_id)
    if origin is None:
        raise MoveError("Village introuvable.")
    if origin.player_id != player_id:
        raise MoveError("Ce village ne t'appartient pas.")
    target = store.load_village(target_id)
    if target is None:
        raise MoveError("Village cible introuvable.")
    if origin_id == target_id:
        raise MoveError("Cible identique à l'origine.")
    if merchants_total(origin) < 1:
        raise MoveError("Construis une place de marché pour commercer.")

    amounts = [max(0, int(a)) for a in (amounts + [0, 0, 0, 0])[:4]]
    total = sum(amounts)
    if total <= 0:
        raise MoveError("Aucune ressource à envoyer.")

    V.tick(origin, now)
    for i in range(4):
        if amounts[i] > origin.resources[i]:
            raise MoveError("Ressources insuffisantes.")

    capacity = merchant_capacity(origin)
    need = math.ceil(total / capacity)
    free = merchants_available(origin)
    if need > free:
        raise MoveError(f"Pas assez de marchands libres ({free} dispo, {need} requis).")

    for i in range(4):
        origin.resources[i] -= amounts[i]
    store.save_village(origin)

    secs = merchant_seconds(origin.x, origin.y, target.x, target.y,
                            origin.tribe, origin.server_speed)
    mid = store.insert_movement(origin_id, target_id, player_id, "trade", "outbound",
                                [0] * 10, now + secs, loot=amounts,
                                target_x=target.x, target_y=target.y, merchants=need)
    return {"id": mid, "arrive_in": round(secs), "merchants": need}


# --- Routes commerciales récurrentes ----------------------------------------
# Une route renvoie périodiquement une cargaison fixe d'un village vers un autre.
# Elle se déclenche au passage de `next_run` (sim paresseuse), via la même
# machinerie que `send_resources` (marchands, capacité, trajet, plafonnement).
# La cadence (heures de temps de base) est divisée par la vitesse serveur, comme
# toutes les durées. Si un cycle n'a pas assez de ressources/marchands, il est
# simplement sauté (réessai au cycle suivant).
def create_trade_route(origin_id: int, target_id: int, player_id: int,
                       amounts: list[int], interval_hours: float,
                       now: float | None = None) -> dict:
    now = now or _time.time()
    origin = store.load_village(origin_id)
    if origin is None or origin.player_id != player_id:
        raise MoveError("Village invalide.")
    if merchants_total(origin) < 1:
        raise MoveError("Construis une place de marché pour commercer.")
    target = store.load_village(target_id)
    if target is None:
        raise MoveError("Village cible introuvable.")
    if origin_id == target_id:
        raise MoveError("Cible identique à l'origine.")
    amounts = [max(0, int(a)) for a in (amounts + [0, 0, 0, 0])[:4]]
    if sum(amounts) <= 0:
        raise MoveError("Aucune ressource à envoyer.")
    if interval_hours <= 0:
        raise MoveError("Intervalle invalide.")
    # next_run = now ⇒ premier envoi dès le prochain passage (création réactive).
    rid = store.insert_trade_route(origin_id, target_id, player_id, amounts,
                                   interval_hours, now)
    return {"id": rid}


def _process_trade_routes_locked(now: float) -> None:
    """Déclenche les routes commerciales arrivées à échéance (sous `_PROCESS_LOCK`)."""
    for r in store.due_trade_routes(now):
        origin = store.load_village(r["origin_id"])
        if origin is None:
            store.delete_trade_route(r["id"], r["origin_id"])
            continue
        effective = r["interval_hours"] * 3600.0 / max(1, origin.server_speed)
        amounts = json.loads(r["amounts"])
        try:
            send_resources(r["origin_id"], r["target_id"], r["owner_id"], amounts, now)
        except MoveError:
            pass  # ressources/marchands indisponibles ce cycle : réessai au suivant
        nxt = r["next_run"]
        while nxt <= now:
            nxt += effective
        store.update_trade_route_next_run(r["id"], nxt)


def catapult_target_limit(v) -> int:
    """Nombre de cibles distinctes que les catapultes peuvent viser : 2 à partir
    d'un atelier de niveau 20 (vrai Travian), sinon 1."""
    return 2 if V.building_levels(v).get(B.WORKSHOP, 0) >= 20 else 1


def send(origin_id: int, target_id: int | None, player_id: int, kind: str,
         units: list[int], now: float | None = None,
         target_x: int | None = None, target_y: int | None = None,
         with_hero: bool = False, targets: list[int] | None = None,
         scout_mode: str | None = None) -> dict:
    """Envoie une armée vers un village (`target_id`) **ou** une oasis (`target_x/y`).

    Cible village : `target_id` renseigné. Cible oasis : `target_id=None` et
    coordonnées de la case (qui doit être une oasis ; on n'attaque pas une vallée
    vide). Les oasis ne peuvent recevoir que des razzias/attaques, pas de renfort.
    `with_hero` : envoie aussi le héros (s'il est présent dans ce village), pour les
    attaques/razzias uniquement. `targets` : ids de bâtiments visés par les catapultes
    (siège) — pris en compte uniquement sur une **attaque** de village, plafonné par
    l'atelier (cf. `catapult_target_limit`).

    `kind="scout"` : **espionnage** — n'embarque **que des éclaireurs** (cf.
    engine.scouting), vers un **village** uniquement ; `scout_mode` = "res" (ressources)
    ou "def" (défenses). Pas de héros, pas de butin, pas de siège.
    """
    now = now or _time.time()
    origin = store.load_village(origin_id)
    if origin is None:
        raise MoveError("Village introuvable.")
    if origin.player_id != player_id:
        raise MoveError("Ce village ne t'appartient pas.")
    if sum(units) <= 0 and not with_hero:
        raise MoveError("Aucune troupe sélectionnée.")

    # Résolution de la cible d'abord (le contrôle d'éligibilité du héros en dépend).
    target = None
    if target_id is not None:
        target = store.load_village(target_id)
        if target is None:
            raise MoveError("Village introuvable.")
        if origin_id == target_id:
            raise MoveError("Cible identique à l'origine.")
        tx, ty = target.x, target.y
    else:
        tile = store.get_tile(target_x, target_y)
        if tile is None or tile["kind"] != "oasis":
            raise MoveError("Cible invalide : seules les oasis sont attaquables sur la carte.")
        if kind == "reinforce":
            # Renfort d'oasis : autorisé **uniquement** vers une oasis que tu occupes
            # (vrai T4.6). La garnison la défendra (cf. _resolve_oasis).
            owner_vid = tile.get("owner_id")
            owner = store.load_village(owner_vid) if owner_vid is not None else None
            if owner is None or owner.player_id != player_id:
                raise MoveError("Tu ne peux renforcer qu'une oasis que tu occupes.")
        if (target_x, target_y) == (origin.x, origin.y):
            raise MoveError("Cible identique à l'origine.")
        tx, ty = target_x, target_y

    # Espionnage : uniquement des éclaireurs, vers un village, sans héros ni siège.
    from app.engine import scouting as SC
    scout_mode_final = None
    if kind == "scout":
        if target is None:
            raise MoveError("L'espionnage vise un village, pas une oasis.")
        if with_hero:
            raise MoveError("Le héros n'accompagne pas une mission d'espionnage.")
        scout_idx = set(SC.scout_indices(origin.tribe))
        if any(units[i] > 0 for i in range(10) if i not in scout_idx):
            raise MoveError("L'espionnage n'embarque que des éclaireurs.")
        if sum(units) <= 0:
            raise MoveError("Aucun éclaireur sélectionné.")
        scout_mode_final = scout_mode if scout_mode in ("res", "def") else "res"

    # Héros : embarqué sur une attaque/razzia, OU envoyé en **assistance** (renfort)
    # vers un de tes propres villages — il s'y **réinstalle** (nouveau rattachement)
    # à l'arrivée. Toujours depuis son village d'attache, disponible et en vie.
    from app.engine import hero as H
    hero_flag = 0
    hero = None
    if with_hero:
        if kind == "reinforce":
            if target is None or target.player_id != player_id:
                raise MoveError("Le héros ne renforce que tes propres villages.")
        elif kind not in ("attack", "raid"):
            raise MoveError("Le héros n'accompagne qu'attaques, razzias et renforts.")
        hero = H.load(player_id)
        if hero is None or hero.home_village_id != origin_id:
            raise MoveError("Pas de héros dans ce village.")
        H.tick(hero, origin, now)
        if hero.status != "home" or hero.health <= 0:
            raise MoveError("Le héros n'est pas disponible.")
        # « moving » = en transit vers un renfort (ré-attache à l'arrivée) ;
        # « attacking » = accompagne une attaque/razzia (rentre après le combat).
        hero.status = "moving" if kind == "reinforce" else "attacking"
        H.save(hero)
        hero_flag = 1

    V.tick(origin, now)
    for i in range(10):
        if units[i] > origin.troops[i]:
            raise MoveError("Pas assez de troupes.")
    # Les troupes quittent la garnison mais restent à la charge du village
    # d'origine (elles continuent d'y consommer du blé pendant le trajet).
    for i in range(10):
        origin.troops[i] -= units[i]
        origin.away[i] += units[i]
    store.save_village(origin)

    # Cibles de catapulte : conservées seulement pour une attaque de village
    # (les béliers s'occupent du mur tout seuls ; pas de siège sur oasis/razzia,
    # cf. _resolve_battle). On plafonne dès l'envoi par l'atelier de l'origine.
    cata = []
    if kind == "attack" and target_id is not None and targets:
        cata = [int(t) for t in targets][:catapult_target_limit(origin)]
    elif kind == "scout":
        # La colonne `targets` porte le mode d'espionnage (réutilisée, cf. _resolve_scout).
        cata = [scout_mode_final]

    # Vitesse : l'armée (min des unités) ; si seul le héros voyage, sa propre vitesse.
    # Place de tournoi du village d'origine : bonus de vitesse au-delà de 20 cases.
    arena = arena_level(origin)
    if sum(units) > 0:
        secs = travel_seconds(origin.x, origin.y, tx, ty,
                              origin.tribe, units, origin.server_speed, arena)
    else:
        hspeed = H.effective(hero)["speed"] if hero is not None else 1.0
        secs = _leg_seconds(distance(origin.x, origin.y, tx, ty), hspeed,
                            origin.server_speed, arena)
    # Artefact « bottes ailées » (s'il est actif) : troupes plus rapides ⇒ trajet réduit.
    # ⚠️ Simplification documentée : appliqué à l'**aller** (cf. engine.artifacts) ; le
    # trajet retour suit la vitesse ordinaire (comme le bonus n'est lu qu'à l'envoi ici).
    from app.engine import artifacts as ART
    secs /= ART.speed_multiplier(origin)
    arrive_at = now + secs
    mid = store.insert_movement(origin_id, target_id, player_id, kind, "outbound",
                                units, arrive_at, target_x=tx, target_y=ty,
                                hero=hero_flag, targets=cata)
    return {"id": mid, "arrive_in": round(secs)}


def _build_place(target: V.Village) -> C.Place:
    wall_level, wall_bonus = 0, (lambda lvl: {"def_bonus": 0.0})
    for s in target.slots.values():
        b = BLD.get(s.building_id)
        if b.slot == "wall" and s.level > 0:
            wall_level, wall_bonus = s.level, b.benefit
    # Tailleur de pierre (capitale) : durabilité des bâtiments +10 %/niveau ⇒ les
    # engins de siège (catapultes vs bâtiments, béliers vs muraille) sont d'autant
    # moins efficaces (combat.demolish_points/_wall divisent par la durabilité).
    # Kirilloid muet sur la valeur → +10 %/niveau (vrai T4, support.travian.com).
    stonemason = V.building_levels(target).get(B.STONEMASON, 0)
    dur = 1.0 + 0.10 * stonemason
    # Artefact de l'architecte (s'il est actif pour le défenseur) : durabilité ×3/4/5,
    # cumulée multiplicativement au tailleur de pierre. Cf. engine.artifacts.
    if target.player_id is not None:
        from app.engine import artifacts as ART
        dur *= ART.durability_multiplier(target)
    return C.Place(tribe=int(target.tribe), pop=V.population(target),
                   wall_level=wall_level, wall_bonus=wall_bonus,
                   dur_bonus=dur, wall_durability=dur)


def _wall_slot(target: V.Village) -> int | None:
    """Index de l'emplacement de muraille (présent, niveau > 0), sinon None."""
    for si, s in target.slots.items():
        if BLD.get(s.building_id).slot == "wall" and s.level > 0:
            return si
    return None


def _cata_target_slots(target: V.Village, building_ids: list[int],
                       limit: int) -> list[int]:
    """Résout les ids de bâtiments visés en index d'emplacements concrets de la cible.

    Pour chaque id demandé (dédupliqué, plafonné par `limit`), on retient
    l'emplacement de plus haut niveau de ce type (>0), hors muraille (béliers). Les
    types absents de la cible sont ignorés (catapultes « perdues », fidélité Travian).
    """
    slots: list[int] = []
    seen: set[int] = set()
    for bid in building_ids:
        if bid in seen or len(slots) >= limit:
            continue
        seen.add(bid)
        if BLD.get(bid).slot == "wall":
            continue
        cands = [(si, s.level) for si, s in target.slots.items()
                 if s.building_id == bid and s.level > 0]
        if not cands:
            continue
        cands.sort(key=lambda c: (-c[1], c[0]))
        slots.append(cands[0][0])
    return slots


def _resolve_battle(origin, target, units, kind, now, att_hero=None,
                    cata_targets=None):
    """Résout un combat à l'arrivée et renvoie (survivants, butin, hero_alive).

    `att_hero` : héros de l'attaquant (Hero) s'il accompagne l'armée. Le héros du
    défenseur (présent dans la cible) est chargé et renforce la défense.
    `cata_targets` : ids de bâtiments visés par les catapultes (siège). Le siège
    (démolition de muraille par les béliers, de bâtiments par les catapultes) n'est
    **persisté que sur une attaque normale** (`kind=="attack"`), jamais en razzia
    (fidélité Travian / TravianZ : les engins ne détruisent rien lors d'un pillage).
    """
    from app.engine import hero as H
    # Pièges du trappeur (Gaulois) : capture pré-combat des assaillants. Jusqu'à
    # `free_traps` unités sont retenues (réparties au prorata) et ne combattent pas ;
    # le surplus livre bataille. Le héros n'est pas piégeable (il combat toujours).
    trapped = V.distribute_traps(list(units), V.free_traps(target))
    fight = [units[i] - trapped[i] for i in range(10)]
    no_battle = sum(fight) == 0 and att_hero is None

    def_before = list(target.troops)
    def_after = list(target.troops)  # snapshot post-combat (avant conquête, cf. classement)
    def_owner = target.player_id  # capturé avant une éventuelle conquête (le rapport
                                  # défensif doit aller à l'ancien propriétaire)
    def_tribe = target.tribe      # idem : la conquête fait adopter la tribu du conquérant
    loot = [0, 0, 0, 0]
    hero_alive = True
    def_hero = None
    off_losses = def_losses = 0.0
    siege = {"mur": None, "degats": []}  # récap destructions (attaque seulement)

    if not no_battle:
        off = C.Off(units=UNITS[origin.tribe], numbers=list(fight),
                    upgrades=list(origin.upgrades), pop=V.population(origin), kind=kind)
        if att_hero is not None:
            off.hero_power = H.combat_power(att_hero)
            off.bonus = H.effective(att_hero)["off_bonus"]
        # Brasserie teutonne : fête de la bière active ⇒ +1 %/niveau d'attaque pour
        # toutes les troupes du compte (s'ajoute au bonus d'attaque du héros).
        if origin.tribe == Tribe.TEUTONS:
            from app.engine import brewery as BR
            off.bonus += BR.attack_bonus(origin.player_id, now)
        deff = C.Defender(units=UNITS[target.tribe], numbers=list(target.troops),
                          upgrades=list(target.upgrades))
        place = _build_place(target)

        # Héros défenseur : présent et vivant dans la cible.
        def_hero = H.load(target.player_id)
        if def_hero is not None and (def_hero.home_village_id != target.id
                                     or def_hero.status != "home" or def_hero.health <= 0):
            def_hero = None
        if def_hero is not None:
            place.def_extra += H.combat_power(def_hero)
            place.def_bonus_extra = H.effective(def_hero)["def_bonus"]

        # Siège : ciblage des catapultes (attaque normale uniquement). On résout les
        # ids demandés en emplacements concrets et on passe leurs niveaux au moteur,
        # qui calcule le niveau restant après démolition (béliers→mur, catas→cibles).
        cata_slots = []
        if kind == "attack":
            has_cats = any(fight[i] for i in range(10)
                           if UNITS[origin.tribe][i].is_catapult)
            if has_cats and cata_targets:
                cata_slots = _cata_target_slots(
                    target, list(cata_targets), catapult_target_limit(origin))
            off.targets = [target.slots[si].level for si in cata_slots]

        res = C.combat(place, off, [deff])
        off_losses, def_losses = res.off_losses, res.def_losses
        target.troops = [round(target.troops[i] * (1 - def_losses)) for i in range(10)]
        def_after = list(target.troops)  # pertes défensives (avant conquête/prisonniers)

        # Persistance du siège (attaque seulement) : niveaux réappliqués au défenseur.
        # On récapitule **tout** ce qui a été visé — y compris sans dégât (avant==apres)
        # ⇒ le rapport affiche « n'a pas été endommagé » le cas échéant.
        if kind == "attack":
            has_rams = any(fight[i] for i in range(10)
                           if UNITS[origin.tribe][i].is_ram)
            wall_slot = _wall_slot(target)
            if wall_slot is not None and has_rams:
                siege["mur"] = {"batiment": BLD.get(target.slots[wall_slot].building_id).name,
                                "avant": target.slots[wall_slot].level, "apres": res.wall}
                target.slots[wall_slot].level = res.wall
            for si, newlvl in zip(cata_slots, res.buildings):
                before = target.slots[si].level
                siege["degats"].append({
                    "batiment": BLD.get(target.slots[si].building_id).name,
                    "avant": before, "apres": newlvl})
                target.slots[si].level = newlvl

    survivors = [round(fight[i] * (1 - off_losses)) for i in range(10)]

    # Conquête : un administrateur (chef/sénateur) survivant réduit la loyauté sur
    # **attaque normale**, si la cible est éligible (résidence/palais détruit — évalué
    # APRÈS le siège —, pas une capitale ni l'unique village, culture/slot suffisants).
    # À 0 % de loyauté ⇒ changement de propriétaire (cf. engine.conquest).
    from app.engine import conquest as CQ
    conquest = None
    loyalty_event = None
    if kind == "attack" and not no_battle:
        n_chiefs = CQ.count_chiefs(origin.tribe, survivors)
        if n_chiefs > 0:
            ok, reason = CQ.conquer_eligible(target, origin.player_id, now)
            if ok:
                from app.engine import celebration as CEL
                great = CEL.great_celebration_active(origin, now)
                before = target.loyalty
                drop = CQ.loyalty_drop(origin.tribe, n_chiefs, great_celebration=great)
                target.loyalty = max(0.0, before - drop)
                if target.loyalty <= 0.0:
                    conquest = CQ.conquer_village(target, origin.player_id,
                                                  origin.tribe, survivors, now)
                loyalty_event = {"chefs": n_chiefs, "baisse": round(drop),
                                 "avant": round(before), "apres": round(target.loyalty),
                                 "grande_fete": great, "conquis": conquest is not None}
            else:
                loyalty_event = {"chefs": n_chiefs, "bloque": reason}

    # Butin : capacité de transport des survivants. Sur une **conquête**, les
    # ressources restent au village (elles changent de propriétaire avec lui) : pas de
    # butin rapporté.
    cap = 0
    if conquest is None:
        cap = sum(survivors[i] * UNITS[origin.tribe][i].capacity for i in range(10))
        avail = sum(target.resources)
        take = min(cap, avail)
        if avail > 0 and take > 0:
            for i in range(4):
                loot[i] = round(take * target.resources[i] / avail)
                target.resources[i] = max(0.0, target.resources[i] - loot[i])

    # Assaillants capturés par les pièges : prisonniers du défenseur ; mais si le
    # village vient d'être conquis, ils rejoignent la garnison du nouveau propriétaire.
    if sum(trapped) > 0:
        if conquest is not None:
            for i in range(10):
                target.troops[i] += trapped[i]
        else:
            V.add_prisoners(target, origin.player_id, origin.id, int(origin.tribe), trapped)
    store.save_village(target)

    # Héros : XP (unités ennemies tuées) + perte de santé (pertes de son camp).
    att_killed = sum(fight[i] - survivors[i] for i in range(10))
    def_killed = sum(def_before[i] - target.troops[i] for i in range(10))
    if att_hero is not None:
        hero_alive = not H.apply_combat(att_hero, off_losses, def_killed, now)
        H.save(att_hero)
    if def_hero is not None:
        H.apply_combat(def_hero, def_losses, att_killed, now)
        H.save(def_hero)

    # Conquête : le héros du défenseur rattaché au village conquis **meurt**
    # (fidélité — support.travian.com / unofficialtravian « Conquering Villages » : un
    # héros dont le village d'attache est conquis meurt ; on ne le sauve qu'en le
    # re-rattachant *avant*, cf. renfort héros). On le re-rattache à un village survivant
    # du défenseur pour qu'il puisse y ressusciter (manoir requis). Géré ici — et non
    # dans conquer_village — pour ne pas entrer en conflit avec H.save(def_hero) ci-dessus.
    hero_def_lost = False
    if conquest is not None:
        dh = def_hero if def_hero is not None else H.load(def_owner)
        if dh is not None and dh.home_village_id == target.id:
            new_home = CQ.pick_surviving_home(def_owner, target.id)
            if new_home is not None:
                dh.home_village_id = new_home
                dh.status = "dead"
                dh.health = 0.0
                dh.busy_until = 0.0
                dh.updated_at = now
                H.save(dh)
                hero_def_lost = True

    # Artefact : si la cible (village Natar) en détient un, le héros peut le capturer
    # en remportant l'attaque (garnison vaincue) et avec une trésorerie vide suffisante
    # au village d'origine. Cf. engine.artifacts.try_capture.
    from app.engine import artifacts as ART
    won = not no_battle and sum(target.troops) == 0
    artefact = ART.try_capture(origin, target, att_hero, hero_alive, kind, won, now)

    # Classement : points d'attaque/défense = upkeep des troupes ennemies tuées
    # (attaquant ↔ défenseurs tués, défenseur ↔ assaillants tués) + ressources
    # pillées à l'attaquant. Cf. engine.ranking (mécanique TravianZ Ranking.php).
    from app.engine import ranking as RK
    att_losses_vec = [fight[i] - survivors[i] for i in range(10)]
    def_losses_vec = [def_before[i] - def_after[i] for i in range(10)]
    RK.credit_battle(origin.player_id, origin.tribe, att_losses_vec,
                     def_owner, def_tribe, def_losses_vec, raided=sum(loot))

    # Rapports (captures = assaillants piégés ; survivants = ceux qui ont combattu).
    # `loyaute`/`conquete` : effet d'un administrateur (chef/sénateur) sur la cible.
    conquis = conquest is not None
    titre_off = (f"👑 Conquête de {target.name}" if conquis
                 else f"⚔️ Attaque sur {target.name}")
    # Noms d'unités figés dans le rapport (snapshot) : le front affiche le détail des
    # pertes par type sans avoir à connaître la tribu — et un village qui change de
    # tribu à la conquête n'altère pas les anciens rapports.
    noms_off = [u.name for u in UNITS[origin.tribe]]
    noms_def = [u.name for u in UNITS[def_tribe]]
    store.add_report(origin.player_id, now, titre_off, {
        "type": "offensive", "cible": target.name, "kind": kind,
        "envoyees": list(units), "survivantes": survivors, "captures": trapped,
        "pertes_pct": round(off_losses * 100), "butin": loot, "capacite": cap,
        "def_avant": def_before, "def_apres": def_after, "noms_def": noms_def,
        "hero": att_hero is not None, "hero_alive": hero_alive, "siege": siege,
        "loyaute": loyalty_event, "conquete": conquis, "artefact": artefact,
        "noms": noms_off})
    titre_def = (f"👑 {target.name} a été conquis !" if conquis
                 else f"🛡️ Défense de {target.name}")
    store.add_report(def_owner, now, titre_def, {
        "type": "defensive", "attaquant": origin.name, "kind": kind,
        "def_avant": def_before, "def_apres": target.troops, "captures": trapped,
        "pertes_pct": round(def_losses * 100), "butin_pille": loot,
        "hero_def": def_hero is not None, "hero_def_perdu": hero_def_lost,
        "siege": siege, "loyaute": loyalty_event, "conquete": conquis,
        "noms_def": noms_def, "noms": noms_off})
    return survivors, loot, hero_alive, conquis


def _resolve_oasis(origin, tile, units, kind, now, att_hero=None):
    """Combat à l'arrivée sur une oasis.

    - **Oasis libre** : troupes du joueur vs **animaux** (Nature), sans butin.
    - **Oasis occupée par un autre joueur** : troupes vs la **garnison** du propriétaire
      (sa tribu, sans bonus mur/résidence ; vrai T4.6). La reprendre exige de **détruire
      la garnison** par une **attaque normale** (pas razzia) ; les survivants la volent
      alors au profit d'un village éligible (`oasis.conquer`).

    Place.pop = pop de l'attaquant pour neutraliser le bonus de moral. Renvoie
    (survivants offensifs, hero_alive)."""
    from app.engine import hero as H
    from app.engine import oasis as O
    owner_vid = tile.get("owner_id")
    owner = store.load_village(owner_vid) if owner_vid is not None else None
    occupied = owner is not None and owner.player_id != origin.player_id

    off = C.Off(units=UNITS[origin.tribe], numbers=list(units),
                upgrades=list(origin.upgrades), pop=V.population(origin), kind=kind)
    if att_hero is not None:
        off.hero_power = H.combat_power(att_hero)
        off.bonus = H.effective(att_hero)["off_bonus"]
    if origin.tribe == Tribe.TEUTONS:
        from app.engine import brewery as BR
        off.bonus += BR.attack_bonus(origin.player_id, now)

    if occupied:
        V.tick(owner, now)
        garrison = O.oasis_garrison(owner, tile["x"], tile["y"])
        deff = C.Defender(units=UNITS[owner.tribe], numbers=list(garrison),
                          upgrades=list(owner.upgrades))
        place = C.Place(tribe=int(owner.tribe), pop=off.pop, wall_level=0)
        defender_before = garrison
    else:
        animals = tile["animals"] or [0] * 10
        deff = C.Defender(units=UNITS[Tribe.NATURE], numbers=list(animals), upgrades=[0] * 10)
        place = C.Place(tribe=int(Tribe.NATURE), pop=off.pop, wall_level=0)
        defender_before = animals

    res = C.combat(place, off, [deff])
    survivors = [round(units[i] * (1 - res.off_losses)) for i in range(10)]
    defender_after = [round(defender_before[i] * (1 - res.def_losses)) for i in range(10)]
    if occupied:
        O.set_oasis_garrison(owner, tile["x"], tile["y"], defender_after)
        store.save_village(owner)
    else:
        store.update_tile_animals(tile["x"], tile["y"], defender_after)

    hero_alive = True
    if att_hero is not None:
        killed = sum(defender_before[i] - defender_after[i] for i in range(10))
        hero_alive = not H.apply_combat(att_hero, res.off_losses, killed, now)
        H.save(att_hero)

    # Classement : l'attaquant gagne des points d'attaque pour les défenseurs tués
    # (animaux Nature ou garnison ennemie). La Nature n'est pas un joueur classé
    # (def_pid=None) ; une garnison ennemie crédite son propriétaire en défense.
    from app.engine import ranking as RK
    def_tribe = owner.tribe if occupied else Tribe.NATURE
    def_pid = owner.player_id if occupied else None
    att_losses_vec = [units[i] - survivors[i] for i in range(10)]
    def_losses_vec = [defender_before[i] - defender_after[i] for i in range(10)]
    RK.credit_battle(origin.player_id, origin.tribe, att_losses_vec,
                     def_pid, def_tribe, def_losses_vec)

    # Re-conquête : oasis tenue par un autre joueur, **garnison détruite**, **attaque
    # normale** victorieuse (des troupes survivent) ⇒ on la lui vole (village éligible).
    # Une **razzia** ne prend jamais l'oasis (fidélité Travian / TravianZ).
    conquest = None
    if (occupied and kind == "attack" and sum(defender_after) == 0
            and sum(survivors) > 0):
        conquest = O.conquer(tile, origin, now)

    label = W.oasis_label(tile["layout"])
    store.add_report(origin.player_id, now,
                     f"🐾 Attaque d'oasis ({tile['x']}|{tile['y']})", {
                         "type": "oasis", "oasis": label, "kind": kind,
                         "coords": [tile["x"], tile["y"]],
                         "occupee": occupied,
                         "envoyees": list(units), "survivantes": survivors,
                         "pertes_pct": round(res.off_losses * 100),
                         "animaux_avant": W.animal_breakdown([0] * 10 if occupied else defender_before),
                         "animaux_apres": W.animal_breakdown([0] * 10 if occupied else defender_after),
                         "garnison_avant": sum(defender_before) if occupied else 0,
                         "garnison_apres": sum(defender_after) if occupied else 0,
                         "cleared": sum(defender_after) == 0,
                         "conquete": conquest,
                         "hero": att_hero is not None, "hero_alive": hero_alive,
                         "noms": [u.name for u in UNITS[origin.tribe]]})
    return survivors, hero_alive


def _resolve_scout(origin, target, units, mode, now):
    """Résout une mission d'espionnage à l'arrivée (cf. engine.scouting).

    Éclaireurs seuls (validé à l'envoi). L'attaquant ne perd d'éclaireurs **que si** la
    cible en abrite (« détecté ») : sinon rapport complet et aucune perte. Si la
    puissance de reconnaissance défensive **≥** offensive, les éclaireurs sont anéantis
    (aucune info renvoyée) et le défenseur est notifié. Renvoie les survivants (retour
    à vide vers l'origine).
    """
    from app.engine import scouting as SC
    n_off = sum(units)
    # Puissance offensive : éclaireurs améliorés (forge), pénalisée par le moral
    # (gros village attaquant ⇒ malus, comme au combat).
    off_power = SC.scout_power(origin.tribe, units, origin.upgrades)
    off_power *= C.morale(V.population(origin), V.population(target))
    # Artefact « Œil de l'aigle » (`spy`) : ×5/3/10 l'efficacité des éclaireurs, en
    # **attaque** (village d'origine) comme en **défense** (cible). Cf. engine.artifacts.
    from app.engine import artifacts as ART
    off_power *= ART.spy_multiplier(origin)
    # Puissance défensive : éclaireurs de la cible (renforts inclus, fusionnés dans
    # troops) améliorés (forge), renforcés par la muraille.
    def_power = SC.scout_power(target.tribe, target.troops, target.upgrades)
    def_power *= 1 + SC.wall_def_bonus(target)
    def_power *= ART.spy_multiplier(target)
    n_def = SC.scout_count(target.tribe, target.troops)

    off_loss, def_loss, detected = SC.resolve_losses(off_power, def_power, n_off, n_def)
    survivors = [round(units[i] * (1 - off_loss)) for i in range(10)]
    got_info = sum(survivors) > 0

    # Pertes des éclaireurs défenseurs (indices d'éclaireur uniquement).
    if detected and def_loss > 0:
        for i in SC.scout_indices(target.tribe):
            target.troops[i] = round(target.troops[i] * (1 - def_loss))
        store.save_village(target)

    intel = SC.gather_intel(target, mode) if got_info else None
    store.add_report(origin.player_id, now,
                     (f"🔍 Espionnage de {target.name}" if got_info
                      else f"🔍 Espionnage repoussé — {target.name}"), {
                         "type": "scout_off", "cible": target.name, "mode": mode,
                         "envoyes": sum(units), "survivants": sum(survivors),
                         "pertes_pct": round(off_loss * 100), "detecte": detected,
                         "info": intel, "coords": [target.x, target.y]})
    # Le défenseur n'est prévenu que s'il a **détecté** l'intrusion (il avait des
    # éclaireurs). Un village PNJ (player_id None) n'a pas de boîte de rapports.
    if detected and target.player_id is not None:
        store.add_report(target.player_id, now, f"🔍 {target.name} espionné !", {
            "type": "scout_def", "attaquant": origin.name, "mode": mode,
            "vu": got_info, "pertes_pct": round(def_loss * 100),
            "coords": [origin.x, origin.y]})
    return survivors


# FastAPI exécute les endpoints synchrones dans un pool de threads : deux requêtes
# concurrentes (poll de l'UI + navigation) appelaient process_due en parallèle,
# lisaient le même mouvement « arrivé » avant suppression et réintégraient donc les
# troupes deux fois (duplication au retour). Ce verrou sérialise le traitement.
_PROCESS_LOCK = threading.Lock()


def process_due(now: float | None = None) -> int:
    """Traite tous les mouvements arrivés à échéance. Renvoie le nombre traité."""
    now = now or _time.time()
    with _PROCESS_LOCK:
        return _process_due_locked(now)


def _process_due_locked(now: float) -> int:
    # Routes commerciales d'abord : leurs envois créent des mouvements traités
    # ensuite (et leurs arrivées passées sont résolues dans la même passe).
    _process_trade_routes_locked(now)
    count = 0
    for m in store.due_movements(now):
        count += 1
        origin = store.load_village(m["origin_id"])
        target = store.load_village(m["target_id"])
        units = list(json.loads(m["units"]))

        if m["phase"] == "back":
            # Retour : survivants + butin réintégrés à l'origine ; ils quittent
            # les effectifs « en vol » pour rejoindre la garnison.
            V.tick(origin, now)
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - units[i])
                origin.troops[i] += units[i]
            loot = json.loads(m["loot"])
            caps = V.capacities(origin)
            for i in range(4):
                origin.resources[i] = min(caps[i], origin.resources[i] + loot[i])
            store.save_village(origin)
            # Le héros rentre à la maison (s'il accompagnait ce mouvement).
            if m["hero"]:
                from app.engine import hero as H
                h = H.load(m["owner_id"])
                if h is not None and h.status == "attacking":
                    h.status = "home"
                    h.busy_until = 0.0
                    H.save(h)
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "settle":
            # Arrivée de colons : fondation d'un nouveau village sur la vallée cible.
            from app.engine import expansion as EXP
            EXP.found_on_arrival(m, now)
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "trade":
            # Livraison de ressources : on dépose la cargaison chez la cible
            # (plafonnée par son stockage ; le surplus est perdu), puis les
            # marchands repartent à vide vers l'origine (géré par la phase "back").
            cargo = json.loads(m["loot"])
            V.tick(target, now)
            caps = V.capacities(target)
            lost = [0, 0, 0, 0]
            for i in range(4):
                avant = target.resources[i]
                target.resources[i] = min(caps[i], avant + cargo[i])
                lost[i] = round(max(0, avant + cargo[i] - caps[i]))
            store.save_village(target)
            store.add_report(target.player_id, now, f"📦 Ressources reçues à {target.name}",
                             {"type": "trade", "de": origin.name, "cargaison": cargo,
                              "perdu": lost, "coords": [origin.x, origin.y]})
            secs = merchant_seconds(target.x, target.y, origin.x, origin.y,
                                    origin.tribe, origin.server_speed)
            store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                  "trade", "back", [0] * 10, now + secs,
                                  target_x=origin.x, target_y=origin.y,
                                  merchants=m["merchants"])
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "reinforce" and m["target_id"] is None:
            # Renfort d'une **oasis** occupée : les troupes rejoignent sa garnison
            # (défense de l'oasis). Si l'oasis a été perdue entre-temps, elles rentrent.
            from app.engine import oasis as O
            tile = store.get_tile(m["target_x"], m["target_y"])
            owner_vid = tile.get("owner_id") if tile else None
            owner = store.load_village(owner_vid) if owner_vid is not None else None
            if owner is not None and owner.player_id == m["owner_id"]:
                # Les troupes quittent les effectifs en vol de l'origine pour la garnison.
                V.tick(origin, now)
                for i in range(10):
                    origin.away[i] = max(0, origin.away[i] - units[i])
                store.save_village(origin)
                V.tick(owner, now)
                g = O.oasis_garrison(owner, m["target_x"], m["target_y"])
                for i in range(10):
                    g[i] += units[i]
                O.set_oasis_garrison(owner, m["target_x"], m["target_y"], g)
                store.save_village(owner)
                store.add_report(m["owner_id"], now,
                                 f"➕ Renfort d'oasis ({m['target_x']}|{m['target_y']})",
                                 {"type": "reinforce_oasis", "de": origin.name,
                                  "unites": units, "coords": [m["target_x"], m["target_y"]]})
            else:
                # Oasis perdue : demi-tour vers l'origine (troupes laissées « en vol »,
                # la phase « back » les réintégrera à la garnison de l'origine).
                secs = travel_seconds(m["target_x"], m["target_y"], origin.x, origin.y,
                                      origin.tribe, units, origin.server_speed,
                                      arena_level(origin))
                store.insert_movement(m["origin_id"], None, m["owner_id"],
                                      "reinforce", "back", units, now + secs,
                                      target_x=m["target_x"], target_y=m["target_y"])
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "reinforce":
            # Les renforts cessent de consommer chez l'origine et passent à la
            # charge de la cible où ils stationnent désormais.
            V.tick(origin, now)
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - units[i])
            store.save_village(origin)
            V.tick(target, now)
            for i in range(10):
                target.troops[i] += units[i]
            store.save_village(target)
            # Héros en assistance : il se **réinstalle** dans le village renforcé
            # (nouveau rattachement → il y défend et y produit désormais).
            rehomed = False
            if m["hero"]:
                from app.engine import hero as H
                h = H.load(m["owner_id"])
                if h is not None and h.status == "moving":
                    h.home_village_id = target.id
                    h.status = "home"
                    h.busy_until = 0.0
                    h.updated_at = now
                    H.save(h)
                    rehomed = True
            store.add_report(target.player_id, now, f"➕ Renfort à {target.name}",
                             {"type": "reinforce", "de": origin.name, "unites": units,
                              "hero": rehomed})
            store.delete_movement(m["id"])
            continue

        if m["kind"] == "scout":
            # Espionnage : reconnaissance à l'arrivée, puis retour à vide des survivants.
            V.tick(target, now)
            mode = (json.loads(m["targets"]) or ["res"])[0]
            survivors = _resolve_scout(origin, target, units, mode, now)
            V.tick(origin, now)
            for i in range(10):  # les éclaireurs tués quittent les effectifs en vol
                origin.away[i] = max(0, origin.away[i] - (units[i] - survivors[i]))
            store.save_village(origin)
            store.delete_movement(m["id"])
            if sum(survivors) > 0:
                secs = travel_seconds(m["target_x"], m["target_y"], origin.x, origin.y,
                                      origin.tribe, survivors, origin.server_speed,
                                      arena_level(origin))
                store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                      "scout", "back", survivors, now + secs,
                                      target_x=m["target_x"], target_y=m["target_y"])
            continue

        # attack / raid — cible village (target_id) ou oasis (coordonnées seules)
        from app.engine import hero as H
        att_hero = H.load(m["owner_id"]) if m["hero"] else None
        loot = (0, 0, 0, 0)
        conquered = False
        if m["target_id"] is not None:
            V.tick(target, now)
            cata_targets = json.loads(m["targets"]) if m["targets"] else []
            survivors, loot, hero_alive, conquered = _resolve_battle(
                origin, target, units, m["kind"], now, att_hero, cata_targets)
        else:
            tile = store.get_tile(m["target_x"], m["target_y"])
            survivors, hero_alive = _resolve_oasis(
                origin, tile, units, m["kind"], now, att_hero)
        V.tick(origin, now)
        if conquered:
            # Conquête : les survivants garnisonnent le village conquis ⇒ ils quittent
            # définitivement les effectifs en vol de l'origine (avec les pertes).
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - units[i])
        else:
            # Les pertes au combat quittent définitivement les effectifs en vol de
            # l'origine ; les survivants y restent jusqu'à leur retour.
            for i in range(10):
                origin.away[i] = max(0, origin.away[i] - (units[i] - survivors[i]))
        store.save_village(origin)
        store.delete_movement(m["id"])
        # Le héros mort ne revient pas (status déjà passé à "dead" par apply_combat).
        hero_back = 1 if (att_hero is not None and hero_alive) else 0
        if conquered:
            # Seul le héros rentre (s'il survit) ; les troupes restent en garnison.
            if hero_back:
                secs = travel_seconds(m["target_x"], m["target_y"], origin.x, origin.y,
                                      origin.tribe, units, origin.server_speed,
                                      arena_level(origin))
                store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                      m["kind"], "back", [0] * 10, now + secs,
                                      target_x=m["target_x"], target_y=m["target_y"],
                                      hero=1)
        elif sum(survivors) > 0 or hero_back:
            # Retour depuis le lieu du combat (coordonnées de la cible) vers l'origine.
            # Si seul le héros survit, le trajet retour suit sa propre vitesse.
            ret_units = survivors if sum(survivors) > 0 else units
            secs = travel_seconds(m["target_x"], m["target_y"], origin.x, origin.y,
                                  origin.tribe, ret_units, origin.server_speed,
                                  arena_level(origin))
            store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                  m["kind"], "back", survivors, now + secs, loot,
                                  target_x=m["target_x"], target_y=m["target_y"],
                                  hero=hero_back)
    return count
