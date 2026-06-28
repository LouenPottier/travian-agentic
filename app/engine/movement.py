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


def travel_seconds(ax, ay, bx, by, tribe, units, server_speed) -> float:
    d = distance(ax, ay, bx, by)
    return d / army_speed(tribe, units) * 3600.0 / server_speed


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
         with_hero: bool = False, targets: list[int] | None = None) -> dict:
    """Envoie une armée vers un village (`target_id`) **ou** une oasis (`target_x/y`).

    Cible village : `target_id` renseigné. Cible oasis : `target_id=None` et
    coordonnées de la case (qui doit être une oasis ; on n'attaque pas une vallée
    vide). Les oasis ne peuvent recevoir que des razzias/attaques, pas de renfort.
    `with_hero` : envoie aussi le héros (s'il est présent dans ce village), pour les
    attaques/razzias uniquement. `targets` : ids de bâtiments visés par les catapultes
    (siège) — pris en compte uniquement sur une **attaque** de village, plafonné par
    l'atelier (cf. `catapult_target_limit`).
    """
    now = now or _time.time()
    origin = store.load_village(origin_id)
    if origin is None:
        raise MoveError("Village introuvable.")
    if origin.player_id != player_id:
        raise MoveError("Ce village ne t'appartient pas.")
    if sum(units) <= 0 and not with_hero:
        raise MoveError("Aucune troupe sélectionnée.")

    # Héros : on ne l'embarque que sur une attaque/razzia, depuis son village
    # d'attache, s'il est disponible et en bonne santé.
    from app.engine import hero as H
    hero_flag = 0
    if with_hero:
        if kind not in ("attack", "raid"):
            raise MoveError("Le héros n'accompagne que les attaques et razzias.")
        h = H.load(player_id)
        if h is None or h.home_village_id != origin_id:
            raise MoveError("Pas de héros dans ce village.")
        H.tick(h, origin, now)
        if h.status != "home" or h.health <= 0:
            raise MoveError("Le héros n'est pas disponible.")
        h.status = "attacking"
        H.save(h)
        hero_flag = 1

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
            raise MoveError("On ne peut pas renforcer une oasis.")
        if (target_x, target_y) == (origin.x, origin.y):
            raise MoveError("Cible identique à l'origine.")
        tx, ty = target_x, target_y

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

    secs = travel_seconds(origin.x, origin.y, tx, ty,
                          origin.tribe, units, origin.server_speed)
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
    return C.Place(tribe=int(target.tribe), pop=V.population(target),
                   wall_level=wall_level, wall_bonus=wall_bonus)


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

        # Persistance du siège (attaque seulement) : niveaux réappliqués au défenseur.
        if kind == "attack":
            wall_slot = _wall_slot(target)
            if wall_slot is not None and res.wall != target.slots[wall_slot].level:
                siege["mur"] = {"batiment": BLD.get(target.slots[wall_slot].building_id).name,
                                "avant": target.slots[wall_slot].level, "apres": res.wall}
                target.slots[wall_slot].level = res.wall
            for si, newlvl in zip(cata_slots, res.buildings):
                before = target.slots[si].level
                if newlvl != before:
                    siege["degats"].append({
                        "batiment": BLD.get(target.slots[si].building_id).name,
                        "avant": before, "apres": newlvl})
                    target.slots[si].level = newlvl

    survivors = [round(fight[i] * (1 - off_losses)) for i in range(10)]

    # Butin : capacité de transport des survivants
    cap = sum(survivors[i] * UNITS[origin.tribe][i].capacity for i in range(10))
    avail = sum(target.resources)
    take = min(cap, avail)
    if avail > 0 and take > 0:
        for i in range(4):
            loot[i] = round(take * target.resources[i] / avail)
            target.resources[i] = max(0.0, target.resources[i] - loot[i])

    # Les assaillants capturés deviennent prisonniers du village défenseur.
    if sum(trapped) > 0:
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

    # Rapports (captures = assaillants piégés ; survivants = ceux qui ont combattu)
    store.add_report(origin.player_id, now, f"⚔️ Attaque sur {target.name}", {
        "type": "offensive", "cible": target.name, "kind": kind,
        "envoyees": list(units), "survivantes": survivors, "captures": trapped,
        "pertes_pct": round(off_losses * 100), "butin": loot,
        "hero": att_hero is not None, "hero_alive": hero_alive, "siege": siege})
    store.add_report(target.player_id, now, f"🛡️ Défense de {target.name}", {
        "type": "defensive", "attaquant": origin.name, "kind": kind,
        "def_avant": def_before, "def_apres": target.troops, "captures": trapped,
        "pertes_pct": round(def_losses * 100), "butin_pille": loot,
        "hero_def": def_hero is not None, "siege": siege})
    return survivors, loot, hero_alive


def _resolve_oasis(origin, tile, units, kind, now, att_hero=None):
    """Combat à l'arrivée sur une oasis : troupes du joueur vs animaux (Nature).

    Pas de butin (les oasis ne stockent aucune ressource) ; on réduit la garnison
    d'animaux survivants. Place.pop = pop de l'attaquant pour neutraliser le bonus
    de moral (pas d'avantage « gros village » face aux animaux). Renvoie
    (survivants offensifs, hero_alive)."""
    from app.engine import hero as H
    animals = tile["animals"] or [0] * 10
    off = C.Off(units=UNITS[origin.tribe], numbers=list(units),
                upgrades=list(origin.upgrades), pop=V.population(origin), kind=kind)
    if att_hero is not None:
        off.hero_power = H.combat_power(att_hero)
        off.bonus = H.effective(att_hero)["off_bonus"]
    deff = C.Defender(units=UNITS[Tribe.NATURE], numbers=list(animals), upgrades=[0] * 10)
    place = C.Place(tribe=int(Tribe.NATURE), pop=off.pop, wall_level=0)
    res = C.combat(place, off, [deff])

    survivors = [round(units[i] * (1 - res.off_losses)) for i in range(10)]
    animals_after = [round(animals[i] * (1 - res.def_losses)) for i in range(10)]
    store.update_tile_animals(tile["x"], tile["y"], animals_after)

    hero_alive = True
    if att_hero is not None:
        killed = sum(animals[i] - animals_after[i] for i in range(10))
        hero_alive = not H.apply_combat(att_hero, res.off_losses, killed, now)
        H.save(att_hero)

    # Re-conquête : oasis tenue par un autre joueur, nettoyée, attaque victorieuse
    # (des troupes survivent) ⇒ on la lui vole au profit d'un village éligible.
    conquest = None
    if (tile.get("owner_id") is not None and sum(animals_after) == 0
            and sum(survivors) > 0):
        from app.engine import oasis as O
        conquest = O.conquer(tile, origin, now)

    label = W.oasis_label(tile["layout"])
    store.add_report(origin.player_id, now,
                     f"🐾 Attaque d'oasis ({tile['x']}|{tile['y']})", {
                         "type": "oasis", "oasis": label, "kind": kind,
                         "coords": [tile["x"], tile["y"]],
                         "envoyees": list(units), "survivantes": survivors,
                         "pertes_pct": round(res.off_losses * 100),
                         "animaux_avant": W.animal_breakdown(animals),
                         "animaux_apres": W.animal_breakdown(animals_after),
                         "cleared": sum(animals_after) == 0,
                         "conquete": conquest,
                         "hero": att_hero is not None, "hero_alive": hero_alive})
    return survivors, hero_alive


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
            store.add_report(target.player_id, now, f"➕ Renfort à {target.name}",
                             {"type": "reinforce", "de": origin.name, "unites": units})
            store.delete_movement(m["id"])
            continue

        # attack / raid — cible village (target_id) ou oasis (coordonnées seules)
        from app.engine import hero as H
        att_hero = H.load(m["owner_id"]) if m["hero"] else None
        loot = (0, 0, 0, 0)
        if m["target_id"] is not None:
            V.tick(target, now)
            cata_targets = json.loads(m["targets"]) if m["targets"] else []
            survivors, loot, hero_alive = _resolve_battle(
                origin, target, units, m["kind"], now, att_hero, cata_targets)
        else:
            tile = store.get_tile(m["target_x"], m["target_y"])
            survivors, hero_alive = _resolve_oasis(
                origin, tile, units, m["kind"], now, att_hero)
        # Les pertes au combat quittent définitivement les effectifs en vol de
        # l'origine ; les survivants y restent jusqu'à leur retour.
        V.tick(origin, now)
        for i in range(10):
            origin.away[i] = max(0, origin.away[i] - (units[i] - survivors[i]))
        store.save_village(origin)
        store.delete_movement(m["id"])
        # Le héros mort ne revient pas (status déjà passé à "dead" par apply_combat).
        hero_back = 1 if (att_hero is not None and hero_alive) else 0
        if sum(survivors) > 0 or hero_back:
            # Retour depuis le lieu du combat (coordonnées de la cible) vers l'origine.
            # Si seul le héros survit, le trajet retour suit sa propre vitesse.
            ret_units = survivors if sum(survivors) > 0 else units
            secs = travel_seconds(m["target_x"], m["target_y"], origin.x, origin.y,
                                  origin.tribe, ret_units, origin.server_speed)
            store.insert_movement(m["origin_id"], m["target_id"], m["owner_id"],
                                  m["kind"], "back", survivors, now + secs, loot,
                                  target_x=m["target_x"], target_y=m["target_y"],
                                  hero=hero_back)
    return count
