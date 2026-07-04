"""Exécuteur d'**ordres permanents** — la couche « 0 LLM » des agents (Phase 4).

Idée (retour utilisateur, cf. mémoire) : pour **minimiser le coût en tokens**, l'agent LLM
ne reste pas réveillé à `wait`-er en boucle. Il **pose une liste d'ordres déclaratifs** (via
l'outil `set_plan`) qu'un **exécuteur Python** réalise AUTOMATIQUEMENT dès que la garde passe
(assez de ressources / file de construction libre). Le LLM n'est ré-invoqué que pour les
**événements** (cf. `engine.situation.should_wake`).

**« Sans tricher » préservé :** chaque action passe par la **surface HTTP joueur enforced**
(`tools._req`, en-tête `X-Acting-Player` posé) — coûts/temps/files/ownership imposés par le
serveur, exactement comme pour un humain. L'exécuteur ne fait ici AUCUNE logique de jeu : il
lit l'état (pour savoir quel ordre n'est pas encore accompli) et **tente** l'action ; un refus
(`ERREUR …`) laisse l'ordre en place pour le cycle suivant.

Registre **en mémoire** (comme les macros) : les plans ne survivent pas au reload uvicorn.
Partagé par le défenseur ET les macros (mono-village).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app import store
from app.data.units import UNITS
from app.engine import village as V
from app.agents import tools as T

# Cadence de l'exécuteur : en ×100 les chantiers durent quelques secondes ⇒ un passage
# toutes les ~4 s suffit à enchaîner les actions sans marteler le serveur.
STEP_SECONDS = 4.0
# Batch d'entraînement max déclenché en un cycle (évite un unique gros ordre).
TRAIN_BATCH_CAP = 50

# Ordres valides : op -> champs requis (en plus de village_id).
_SCHEMA = {
    "build": ("slot", "level"),
    "construct": ("slot", "building_id", "level"),
    "train": ("building_id", "unit", "count"),
    "traps": ("count",),
    "research": ("unit",),
}


class PlanError(ValueError):
    """Plan invalide (schéma)."""


def normalize(actions: list[dict]) -> list[dict]:
    """Valide et normalise une liste d'ordres (lève PlanError si invalide)."""
    out = []
    for a in actions:
        if not isinstance(a, dict):
            raise PlanError("Chaque ordre doit être un objet.")
        op = a.get("op")
        if op not in _SCHEMA:
            raise PlanError(f"Ordre inconnu : {op!r}. Attendu : {', '.join(_SCHEMA)}.")
        o = {"op": op, "village_id": int(a["village_id"])}
        for f in _SCHEMA[op]:
            if f not in a:
                raise PlanError(f"Ordre {op} : champ « {f} » manquant.")
            o[f] = int(a[f])
        out.append(o)
    return out


# Registre : plan par village. {village_id: {"owner_id", "orders": [...]}}
_PLANS: dict[int, dict[str, Any]] = {}
_runner_task: asyncio.Task | None = None


def set_plan(village_id: int, owner_id: int, actions: list[dict]) -> list[dict]:
    """Enregistre/remplace le plan d'un village. Une liste vide efface le plan."""
    orders = normalize(actions)
    if orders:
        _PLANS[village_id] = {"owner_id": owner_id, "orders": orders}
        _ensure_runner()
    else:
        _PLANS.pop(village_id, None)
    return orders


def set_plan_multi(owner_id: int, actions: list[dict]) -> list[dict]:
    """Remplace TOUT le plan d'un joueur (les ordres portent chacun leur `village_id`).
    Regroupe par village ; une liste vide efface les plans du joueur."""
    orders = normalize(actions)
    clear_owner(owner_id)
    by_village: dict[int, list[dict]] = {}
    for o in orders:
        by_village.setdefault(o["village_id"], []).append(o)
    for vid, os_ in by_village.items():
        _PLANS[vid] = {"owner_id": owner_id, "orders": os_}
    if orders:
        _ensure_runner()
    return orders


def get_plan(village_id: int) -> list[dict]:
    p = _PLANS.get(village_id)
    return list(p["orders"]) if p else []


def has_plan(owner_id: int) -> bool:
    """Un plan (non vide) est-il posé pour l'un des villages de ce joueur ?"""
    return any(p["owner_id"] == owner_id for p in _PLANS.values())


def clear_owner(owner_id: int) -> None:
    """Retire tous les plans d'un joueur (arrêt de son agent)."""
    for vid in [v for v, p in _PLANS.items() if p["owner_id"] == owner_id]:
        _PLANS.pop(vid, None)


def clear_village(village_id: int) -> None:
    """Retire le plan d'un seul village (arrêt complet de son agent défensif)."""
    _PLANS.pop(village_id, None)


# --- Évaluation « ordre accompli ? » (lecture pure de l'état) -----------------

def _in_training(v: V.Village, unit_index: int) -> int:
    return sum(t.remaining for t in v.training if t.unit_index == unit_index)


def _order_done(v: V.Village, o: dict) -> bool:
    op = o["op"]
    # Construction : « accompli » dès que le niveau **projeté** (courant + file active +
    # file planifiée du moteur) atteint la cible — sinon la boucle réenfilerait des
    # niveaux en trop (la file de construction encaisse déjà les ordres à venir).
    if op == "build":
        return V._projected_slot_level(v, o["slot"]) >= o["level"]
    if op == "construct":
        s = v.slots.get(o["slot"])
        planned = any(p.slot_index == o["slot"] and p.building_id == o["building_id"]
                      for p in v.build_plan)
        if s is None and not planned:
            return False
        if s is not None and s.building_id != o["building_id"]:
            return False
        return V._projected_slot_level(v, o["slot"]) >= o["level"]
    if op == "train":
        return v.troops[o["unit"]] + _in_training(v, o["unit"]) >= o["count"]
    if op == "traps":
        return V.traps_total(v) >= o["count"]
    if op == "research":
        return V.is_researched(v, o["unit"])
    return True


def _train_batch(v: V.Village, o: dict) -> int:
    """Nombre d'unités entraînables ce cycle (borné par l'objectif, l'abordable, le cap)."""
    unit = UNITS[v.tribe][o["unit"]]
    mult = V.GREAT_COST_MULT if o["building_id"] in V.GREAT_TRAINERS else 1
    remaining = o["count"] - (v.troops[o["unit"]] + _in_training(v, o["unit"]))
    if remaining <= 0:
        return 0
    afford = remaining
    for k in range(4):
        c = unit.cost[k] * mult
        if c > 0:
            afford = min(afford, int(v.resources[k] // c))
    return max(0, min(remaining, afford, TRAIN_BATCH_CAP))


async def _fire(vid: int, path: str) -> str:
    return await T._req("POST", f"/api/village/{vid}{path}")


async def step(village_id: int) -> list[str]:
    """Tente **au plus une** action réussie pour le plan de ce village. Renvoie le journal.
    Les ordres déjà accomplis sont sautés ; on essaie les autres dans l'ordre et on
    s'arrête au premier succès (un refus serveur laisse l'ordre pour le cycle suivant)."""
    plan = _PLANS.get(village_id)
    if plan is None:
        return []
    owner_id = plan["owner_id"]
    v = store.load_village(village_id)
    if v is None or v.player_id != owner_id:
        # Village disparu / changé de propriétaire (conquête) ⇒ plan caduc.
        _PLANS.pop(village_id, None)
        return []
    V.tick(v)
    log: list[str] = []
    T.set_acting_player(owner_id)
    try:
        for o in plan["orders"]:
            if _order_done(v, o):
                continue
            op = o["op"]
            if op == "build":
                res = await _fire(village_id, f"/build/{o['slot']}")
            elif op == "construct":
                s = v.slots.get(o["slot"])
                if s is None:
                    res = await _fire(village_id, f"/construct/{o['slot']}/{o['building_id']}")
                else:
                    res = await _fire(village_id, f"/build/{o['slot']}")
            elif op == "train":
                batch = _train_batch(v, o)
                if batch <= 0:
                    continue  # pas assez de ressources ce cycle
                res = await _fire(village_id,
                                  f"/train/{o['building_id']}/{o['unit']}/{batch}")
            elif op == "traps":
                delta = o["count"] - V.traps_total(v)
                if delta <= 0:
                    continue
                res = await _fire(village_id, f"/traps/{delta}")
            elif op == "research":
                res = await _fire(village_id, f"/research/{o['unit']}")
            else:
                continue
            if res.startswith("ERREUR"):
                continue  # refus (ressources/file/…) ⇒ ordre suivant, réessai plus tard
            log.append(f"playbook[{village_id}] {op} : ok")
            break  # au plus une action réussie par cycle
    finally:
        T.set_acting_player(None)
    return log


async def _runner() -> None:
    """Boucle de fond : applique tous les plans à la cadence, s'arrête quand plus aucun."""
    global _runner_task
    try:
        while _PLANS:
            for vid in list(_PLANS.keys()):
                try:
                    await step(vid)
                except Exception:
                    pass  # un plan cassé ne doit pas tuer l'exécuteur
            await asyncio.sleep(STEP_SECONDS)
    finally:
        _runner_task = None


def _ensure_runner() -> None:
    global _runner_task
    if _runner_task is not None and not _runner_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # pas de boucle asyncio (contexte de test synchrone) : rien à démarrer
    _runner_task = loop.create_task(_runner())
