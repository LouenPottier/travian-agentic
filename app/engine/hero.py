"""Héros, aventures et objets — un héros par joueur.

⚠️ **Source des chiffres** : kirilloid ne modélise PAS le héros (santé, XP,
attributs, aventures, objets). Tout ce module est donc une **approximation
documentée** (même statut que les marchands/pièges, cf. CLAUDE.md), calquée sur la
*mécanique* réelle de Travian T4.6 mais avec des valeurs inventées et tunées pour
rester cohérentes avec le moteur (force de combat ~100/point, échelle des stocks).

Mécanique fidèle reproduite :
- **Santé** 0..100, régénère lentement (base + bonus d'objets), accélérée par la
  vitesse serveur. À 0 : héros mort, à ressusciter au manoir du héros (coût + délai).
- **XP → niveau** : chaque niveau octroie 4 points d'attribut à répartir entre
  *force de combat*, *bonus d'attaque*, *bonus de défense*, *production*.
- **Combat** : présent à la maison, le héros défend (force + bonus déf.). Envoyé
  avec une armée, il attaque (force + bonus att.). Il gagne de l'XP (unités tuées)
  et perd de la santé (proportionnelle aux pertes de son camp). Santé 0 ⇒ il meurt.
- **Aventures** : des aventures apparaissent régulièrement sur la carte. Le héros y
  part (durée = trajet) et revient avec XP, ressources, parfois un objet et une
  perte de santé. Récompenses **pré-tirées à l'envoi** (déterministe, testable).
- **Objets** : équipement (6 emplacements) + consommables (soin). Les bonus des
  objets équipés s'ajoutent aux attributs (cf. data.items).

Persistance : un blob JSON par joueur (table `heroes`), production paresseuse comme
les villages (santé régénérée et production créditée au passage de la date).
"""
from __future__ import annotations

import random
import time as _time
from dataclasses import dataclass, field

from app import store
from app.data import items as IT
from app.data.buildings import B
from app.engine import village as V

# --- Constantes (approximations documentées) --------------------------------
START_HEALTH = 100.0
MAX_HEALTH = 100.0
POINTS_PER_LEVEL = 4            # points d'attribut gagnés par niveau (fidèle T4)
BASE_REGEN_PER_DAY = 10.0       # +10 %/jour de santé à vide d'objet (réel T4 ≈ 10 %)
STRENGTH_PER_FIGHT = 100.0     # force de combat par point de « force » (att+déf)
BASE_STRENGTH = 100.0          # force de combat de base du héros (niv 0)
BONUS_PER_POINT = 0.002        # +0,2 %/point d'attaque ou de défense (réel T4)
# Production de ressources par point d'attribut (valeurs **exactes** du vrai Travian T4) :
#   - ressource unique → +10/h de cette ressource par point ;
#   - « réparti » (toutes) → +3/h de **chaque** ressource par point.
# (Le total réparti, 4×3 = 12/pt, dépasse donc volontairement le mode unique, 10/pt.)
PROD_PER_POINT_SINGLE = 10     # +10/h d'une ressource par point
PROD_PER_POINT_EACH = 3        # +3/h de chaque ressource par point (mode réparti)
HERO_SPEED = 7                 # vitesse de base du héros (cases/h, comme un fantassin)

# Aventures
ADVENTURE_CAP = 3              # aventures simultanément disponibles au maximum
ADVENTURE_INTERVAL_H = 3.0    # une nouvelle aventure toutes les ~3 h (temps de base)
REVIVE_TIME = 24 * 3600       # délai de résurrection (s, vitesse 1) = 1 jour
REVIVE_COST = (500, 500, 500, 500)  # coût de résurrection (approximation)


# --- XP / niveau -------------------------------------------------------------
def xp_threshold(level: int) -> float:
    """XP cumulée nécessaire pour atteindre `level` (triangulaire, documentée).

    seuil(L) = 100 · L·(L+1)/2  →  L1=100, L2=300, L3=600, L4=1000…
    """
    return 100.0 * level * (level + 1) / 2.0


def level_for_xp(xp: float) -> int:
    level = 0
    while xp >= xp_threshold(level + 1):
        level += 1
    return level


# --- Dataclass ---------------------------------------------------------------
@dataclass
class Hero:
    player_id: int
    home_village_id: int
    name: str = "Héros"
    level: int = 0
    experience: float = 0.0
    health: float = START_HEALTH
    updated_at: float = field(default_factory=_time.time)
    # Attributs alloués (somme ≤ niveau·4) + points non dépensés.
    points: int = 0                       # points d'attribut disponibles
    fight: int = 0                        # force de combat
    off_points: int = 0                   # bonus d'attaque
    def_points: int = 0                   # bonus de défense
    res_points: int = 0                   # production de ressources
    res_choice: int = -1                  # -1 = réparti sur 4 ; 0..3 = une ressource
    # Statut : home (au village) | adventure (en aventure) | dead (à ressusciter)
    status: str = "home"
    busy_until: float = 0.0               # retour d'aventure / fin de résurrection
    adventure_id: int | None = None
    adventure_reward: dict = field(default_factory=dict)  # récompense pré-tirée
    # Objets : équipement par emplacement + sac de consommables {key: qty}.
    equipment: dict = field(default_factory=dict)
    bag: dict = field(default_factory=dict)
    next_adventure_at: float = 0.0        # date d'apparition de la prochaine aventure


def new_hero(player_id: int, home_village_id: int, now: float | None = None) -> Hero:
    now = now or _time.time()
    return Hero(player_id=player_id, home_village_id=home_village_id,
                updated_at=now, next_adventure_at=now)


# --- (dé)sérialisation -------------------------------------------------------
def to_dict(h: Hero) -> dict:
    return {k: getattr(h, k) for k in (
        "player_id", "home_village_id", "name", "level", "experience", "health",
        "updated_at", "points", "fight", "off_points", "def_points", "res_points",
        "res_choice", "status", "busy_until", "adventure_id", "adventure_reward",
        "equipment", "bag", "next_adventure_at")}


def from_dict(d: dict) -> Hero:
    return Hero(**d)


def load(player_id: int) -> Hero | None:
    d = store.get_hero_row(player_id)
    return from_dict(d) if d else None


def save(h: Hero) -> None:
    store.save_hero_row(h.player_id, to_dict(h))


def get_or_create(player_id: int, home_village_id: int,
                  now: float | None = None) -> Hero:
    h = load(player_id)
    if h is None:
        h = new_hero(player_id, home_village_id, now)
        save(h)
    return h


# --- Bonus cumulés des objets équipés ----------------------------------------
def equip_bonuses(h: Hero) -> dict:
    """Somme des effets de tous les objets équipés."""
    total: dict[str, float] = {}
    for key in h.equipment.values():
        it = IT.get(key)
        if it is None:
            continue
        for k, val in it.bonuses.items():
            total[k] = total.get(k, 0.0) + val
    return total


def effective(h: Hero) -> dict:
    """Stats effectives du héros (attributs + objets équipés)."""
    eq = equip_bonuses(h)
    strength = BASE_STRENGTH + h.fight * STRENGTH_PER_FIGHT + eq.get("strength", 0.0)
    return {
        "strength": strength,
        "off_bonus": h.off_points * BONUS_PER_POINT + eq.get("off", 0.0),
        "def_bonus": h.def_points * BONUS_PER_POINT + eq.get("def", 0.0),
        "regen_per_day": BASE_REGEN_PER_DAY + eq.get("regen", 0.0),
        "speed": HERO_SPEED + eq.get("speed", 0.0),
        "xp_bonus": eq.get("xp_bonus", 0.0),
        # Production **par ressource** /h (10/pt en mode unique, 3/pt en réparti).
        "production_per_hour": _prod_per_resource(h),
        "res_choice": h.res_choice,
    }


def _prod_per_resource(h: Hero) -> int:
    """Production horaire **par ressource** : +10/pt si ressource unique, +3/pt si réparti."""
    rate = PROD_PER_POINT_SINGLE if 0 <= h.res_choice <= 3 else PROD_PER_POINT_EACH
    return h.res_points * rate


def hero_production(h: Hero) -> list[float]:
    """Production de ressources/h du héros (échelle de base, hors vitesse serveur).

    Vrai Travian T4 : ressource unique → +10/pt sur cette seule ressource ;
    « réparti » → +3/pt sur **chacune** des 4 ressources."""
    if h.res_points <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    per_res = float(_prod_per_resource(h))
    if 0 <= h.res_choice <= 3:
        out = [0.0, 0.0, 0.0, 0.0]
        out[h.res_choice] = per_res
        return out
    return [per_res] * 4  # +3/pt de chaque ressource (« toutes ressources »)


# --- Production paresseuse : santé + ressources + aventure -------------------
def tick(h: Hero, home: V.Village | None, now: float | None = None) -> bool:
    """Avance l'état du héros jusqu'à `now`. Renvoie True si `home` a été modifié
    (ressources créditées) — l'appelant doit alors le persister."""
    now = now or _time.time()
    if now <= h.updated_at:
        return False
    speed = home.server_speed if home else 1
    elapsed_h = (now - h.updated_at) / 3600.0 * speed
    home_changed = False

    # Résolution d'une aventure terminée (héros revenu).
    if h.status == "adventure" and now >= h.busy_until:
        _apply_adventure_reward(h, home)
        home_changed = home is not None

    # Résurrection terminée.
    if h.status == "dead" and h.busy_until and now >= h.busy_until:
        h.status = "home"
        h.health = MAX_HEALTH
        h.busy_until = 0.0

    # Régénération de santé (uniquement à la maison et vivant).
    if h.status == "home":
        regen = effective(h)["regen_per_day"] / 24.0 * elapsed_h
        h.health = min(MAX_HEALTH, h.health + regen)

    # Production de ressources, créditée au village d'attache (plafonnée).
    # Seulement quand le héros est présent (pas en aventure / au combat / mort).
    if home is not None and h.status == "home":
        prod = hero_production(h)
        if any(prod):
            caps = V.capacities(home)
            for i in range(4):
                home.resources[i] = min(caps[i], home.resources[i] + prod[i] * elapsed_h)
            home_changed = True

    h.updated_at = now
    _refresh_level(h)
    return home_changed


def _refresh_level(h: Hero) -> None:
    """Recalcule le niveau d'après l'XP et crédite les points d'attribut gagnés."""
    new_level = level_for_xp(h.experience)
    if new_level > h.level:
        h.points += (new_level - h.level) * POINTS_PER_LEVEL
        h.level = new_level


# --- Allocation des points d'attribut ----------------------------------------
class HeroError(Exception):
    pass


_ATTRS = {"fight", "off_points", "def_points", "res_points"}


def allocate(h: Hero, attr: str, amount: int) -> None:
    if attr not in _ATTRS:
        raise HeroError("Attribut inconnu.")
    if amount < 1 or amount > h.points:
        raise HeroError("Points insuffisants.")
    setattr(h, attr, getattr(h, attr) + amount)
    h.points -= amount


def set_res_choice(h: Hero, choice: int) -> None:
    if not (-1 <= choice <= 3):
        raise HeroError("Choix de ressource invalide.")
    h.res_choice = choice


# --- Objets : équiper / déséquiper / utiliser --------------------------------
def _bag_add(h: Hero, key: str, qty: int = 1) -> None:
    h.bag[key] = h.bag.get(key, 0) + qty


def equip(h: Hero, key: str) -> None:
    """Équipe un objet pris dans le sac ; renvoie l'ancien au sac."""
    it = IT.get(key)
    if it is None or it.slot not in IT.EQUIP_SLOTS:
        raise HeroError("Objet non équipable.")
    if h.bag.get(key, 0) < 1:
        raise HeroError("Objet absent du sac.")
    h.bag[key] -= 1
    if h.bag[key] <= 0:
        del h.bag[key]
    previous = h.equipment.get(it.slot)
    h.equipment[it.slot] = key
    if previous:
        _bag_add(h, previous)


def unequip(h: Hero, slot: str) -> None:
    key = h.equipment.pop(slot, None)
    if key is None:
        raise HeroError("Aucun objet sur cet emplacement.")
    _bag_add(h, key)


def use_consumable(h: Hero, key: str) -> None:
    it = IT.get(key)
    if it is None or not it.consumable:
        raise HeroError("Objet non consommable.")
    if h.bag.get(key, 0) < 1:
        raise HeroError("Objet absent du sac.")
    if h.status == "dead":
        raise HeroError("Le héros est mort.")
    h.bag[key] -= 1
    if h.bag[key] <= 0:
        del h.bag[key]
    h.health = min(MAX_HEALTH, h.health + it.bonuses.get("heal", 0))


# --- Aventures ---------------------------------------------------------------
def _adv_rng(player_id: int, adv_id: int) -> random.Random:
    return random.Random((player_id * 2654435761) ^ (adv_id * 40503) ^ 0xADBEEF)


def replenish_adventures(player_id: int, h: Hero, now: float) -> None:
    """Fait apparaître de nouvelles aventures au fil du temps (jusqu'au plafond)."""
    speed = 1
    home = store.load_village(h.home_village_id)
    if home:
        speed = home.server_speed
    interval = ADVENTURE_INTERVAL_H * 3600.0 / speed
    while (store.count_adventures(player_id) < ADVENTURE_CAP
           and h.next_adventure_at <= now):
        rng = random.Random((player_id * 911) ^ int(h.next_adventure_at))
        # Position : autour du village d'attache (flavour pour le temps de trajet).
        ax = (home.x if home else 0) + rng.randint(-8, 8)
        ay = (home.y if home else 0) + rng.randint(-8, 8)
        difficulty = "hard" if rng.random() < 0.25 else "normal"
        store.insert_adventure(player_id, ax, ay, difficulty, h.next_adventure_at)
        h.next_adventure_at += interval


def roll_reward(h: Hero, adv: dict) -> dict:
    """Pré-tire la récompense d'une aventure (déterministe par aventure)."""
    rng = _adv_rng(adv["player_id"], adv["id"])
    hard = adv["difficulty"] == "hard"
    lvl = h.level
    # XP : croît avec la difficulté et le niveau du héros.
    xp = rng.randint(8, 16) + lvl * 2
    if hard:
        xp = rng.randint(20, 40) + lvl * 4
    # Ressources : petit butin (échelle de base, comme le commerce).
    base = rng.randint(80, 220) * (2 if hard else 1)
    resources = [int(base * rng.uniform(0.6, 1.4)) for _ in range(4)]
    # Perte de santé : faible en normal, plus risquée en difficile.
    health_loss = rng.randint(2, 12) if not hard else rng.randint(10, 30)
    # Drop d'objet : 25 % en normal, 55 % en difficile (objets rares plus durs).
    item = None
    if rng.random() < (0.55 if hard else 0.25):
        pool = IT.droppable()
        weights = [it.weight for it in pool]
        item = rng.choices(pool, weights=weights, k=1)[0].key
    bonus = effective(h)["xp_bonus"]
    xp = int(xp * (1 + bonus))
    return {"xp": xp, "resources": resources, "health_loss": health_loss,
            "item": item, "difficulty": adv["difficulty"]}


def send_to_adventure(player_id: int, adventure_id: int,
                      now: float | None = None) -> dict:
    now = now or _time.time()
    h = load(player_id)
    if h is None:
        raise HeroError("Pas de héros.")
    home = store.load_village(h.home_village_id)
    tick(h, home, now)
    if home is not None:
        store.save_village(home)
    if h.status != "home":
        raise HeroError("Le héros n'est pas disponible.")
    if h.health <= 0:
        raise HeroError("Le héros n'a plus de santé.")
    adv = store.get_adventure(adventure_id)
    if adv is None or adv["player_id"] != player_id or adv["state"] != "available":
        raise HeroError("Aventure indisponible.")

    # Durée = aller-retour à la vitesse du héros (min 30 s à l'échelle serveur).
    import math
    dist = math.hypot(adv["x"] - home.x, adv["y"] - home.y) if home else 5
    speed = home.server_speed if home else 1
    secs = max(30.0, dist / effective(h)["speed"] * 3600.0 / speed * 2)
    h.status = "adventure"
    h.busy_until = now + secs
    h.adventure_id = adventure_id
    h.adventure_reward = roll_reward(h, adv)
    save(h)
    return {"arrive_in": round(secs), "reward_hint": h.adventure_reward["difficulty"]}


def _apply_adventure_reward(h: Hero, home: V.Village | None) -> None:
    """Applique la récompense pré-tirée à la fin d'une aventure et notifie."""
    r = h.adventure_reward or {}
    h.experience += r.get("xp", 0)
    if home is not None:
        caps = V.capacities(home)
        for i in range(4):
            home.resources[i] = min(caps[i], home.resources[i] + r.get("resources", [0]*4)[i])
    h.health = max(0.0, h.health - r.get("health_loss", 0))
    item = r.get("item")
    if item:
        _bag_add(h, item)
    if h.adventure_id is not None:
        store.mark_adventure_done(h.adventure_id)
    died = h.health <= 0
    if died:
        h.status = "dead"
        h.busy_until = 0.0
    else:
        h.status = "home"
        h.busy_until = 0.0
    item_name = IT.get(item).name if item and IT.get(item) else None
    store.add_report(h.player_id, _time.time(),
                     "🗺️ Aventure terminée" + (" — héros tombé !" if died else ""),
                     {"type": "adventure", "xp": r.get("xp", 0),
                      "resources": r.get("resources", [0, 0, 0, 0]),
                      "health_loss": r.get("health_loss", 0),
                      "item": item_name, "difficulty": r.get("difficulty"),
                      "died": died})
    h.adventure_id = None
    h.adventure_reward = {}


# --- Résurrection ------------------------------------------------------------
def revive(player_id: int, now: float | None = None) -> dict:
    now = now or _time.time()
    h = load(player_id)
    if h is None:
        raise HeroError("Pas de héros.")
    home = store.load_village(h.home_village_id)
    tick(h, home, now)
    if h.status != "dead":
        raise HeroError("Le héros n'est pas mort.")
    # Résurrection déjà lancée (délai en cours) : ne pas re-payer / remettre le
    # compte à rebours à zéro. `tick` (ci-dessus) l'a fait repasser « home » si
    # le délai était échu ⇒ ici on est encore mort ET en attente.
    if h.busy_until and now < h.busy_until:
        raise HeroError("Résurrection déjà en cours.")
    if home is None:
        raise HeroError("Village d'attache introuvable.")
    if V.building_levels(home).get(B.HERO_MANSION, 0) < 1:
        raise HeroError("Manoir du héros requis pour ressusciter.")
    V.tick(home, now)
    if any(home.resources[i] < REVIVE_COST[i] for i in range(4)):
        raise HeroError("Ressources insuffisantes pour ressusciter.")
    for i in range(4):
        home.resources[i] -= REVIVE_COST[i]
    store.save_village(home)
    secs = REVIVE_TIME / home.server_speed
    h.busy_until = now + secs
    save(h)
    return {"revive_in": round(secs)}


# --- Combat : contribution du héros ------------------------------------------
def combat_power(h: Hero) -> float:
    """Force de combat effective du héros, modulée par sa santé (0..100 %)."""
    return effective(h)["strength"] * max(0.0, h.health) / 100.0


def apply_combat(h: Hero, side_losses: float, enemy_killed: int,
                 now: float | None = None) -> bool:
    """Met à jour le héros après une bataille à laquelle il a participé.

    `side_losses` = fraction de pertes de SON camp (0..1) → perte de santé.
    `enemy_killed` = nb d'unités ennemies tuées → XP. Renvoie True si le héros meurt.
    """
    now = now or _time.time()
    h.experience += enemy_killed
    _refresh_level(h)
    # Perte de santé proportionnelle aux pertes du camp (max 90 % d'un coup).
    h.health = max(0.0, h.health - min(90.0, side_losses * 100.0))
    if h.health <= 0:
        h.status = "dead"
        h.busy_until = 0.0
        return True
    return False
