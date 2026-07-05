"""Mise en pause de la **famine** et des **routes commerciales** pendant les arrêts
du serveur (éteint ou en veille).

Motivation (cf. CLAUDE.md) : la simulation est paresseuse — rien ne tourne serveur
éteint ; au redémarrage, tout le laps d'arrêt est rattrapé d'un coup. Deux effets
indésirables, alors que « dans le vrai Travian le serveur ne s'éteint jamais » :
  1. **Famine** : les troupes d'un village au blé net négatif (grosse armée) meurent
     jusqu'à l'équilibre sur tout le trou — alors que les **routes commerciales**
     l'auraient nourri en régime normal.
  2. **Routes** : elles enverraient une cargaison de rattrapage à la reprise.

On ne fige **que** ces deux mécaniques sur la durée d'arrêt. **Production, files de
construction, entraînement continuent** normalement (les bâtiments se construisent).

Détection = **battement de cœur en temps mural réel** : une tâche de fond écrit
`meta.last_alive = time.time()` toutes les `HEARTBEAT_EVERY` s **tant que le process
tourne** (indépendamment des requêtes). C'est ce qui distingue « serveur actif mais
inactif » (battement frais ⇒ rien figé, la simulation paresseuse rattrape normalement)
de « serveur éteint/en veille » (battement figé pendant l'arrêt). ⚠️ On ne se base
**pas** sur le `now` de *jeu* passé à `process_due` : celui-ci peut sauter (tests, gros
rattrapage) sans que le process se soit arrêté. Au redémarrage, un trou mural supérieur
à `GRACE` déclenche le gel : on rejoue **tous** les villages **sans famine** (les troupes
ne mangent pas, cf. `village.tick(starve=False)` — y compris les **rivaux** PNJ de tribu
jouable dont les armées géantes sont affamables) et on **fait glisser** les routes échues
au prochain créneau futur (reprise, sans rafale).
"""
from __future__ import annotations

import asyncio
import time as _time

from app import store
from app.engine import village as V

# Trou de **temps mural** (s) au-delà duquel on considère le serveur arrêté/en veille.
# Doit rester > HEARTBEAT_EVERY (sinon un simple délai de battement passerait pour un
# arrêt). En deçà = serveur actif : rien n'est figé.
GRACE = 120.0
# Cadence du battement de fond (temps mural réel).
HEARTBEAT_EVERY = 30.0

_KEY = "last_alive"


def absorb(now: float | None = None) -> float:
    """À appeler en tête de `process_due` (et au démarrage). Si le **temps mural** a
    fait un bond depuis le dernier battement (serveur resté éteint/en veille), met
    famine + routes en pause sur ce laps. `now` = borne haute de jeu du rattrapage
    (≈ temps mural en production). Renvoie la durée d'arrêt gelée (0 sinon)."""
    now = now if now is not None else _time.time()
    wall = _time.time()                   # DÉTECTION en temps mural réel (jamais le now de jeu)
    raw = store.get_meta(_KEY)
    if raw is None:                       # tout premier lancement : pose le battement
        store.set_meta(_KEY, wall)
        return 0.0
    try:
        last_alive = float(raw)
    except ValueError:
        store.set_meta(_KEY, wall)
        return 0.0

    if wall - last_alive > GRACE:         # process resté arrêté sur [last_alive, now]
        _freeze(last_alive, now)
        store.set_meta(_KEY, wall)
        return wall - last_alive
    return 0.0


def heartbeat() -> None:
    """Marque le serveur vivant (temps mural). Appelé par la tâche de fond."""
    store.set_meta(_KEY, _time.time())


async def heartbeat_loop() -> None:
    """Tâche de fond : rafraîchit le battement tant que le process tourne, pour ne pas
    confondre « serveur actif mais inactif » avec « serveur arrêté »."""
    while True:
        try:
            heartbeat()
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_EVERY)


def _freeze(last_alive: float, now: float) -> None:
    """Éponge la famine sur le laps d'arrêt là où elle frapperait, et fait glisser les
    routes commerciales échues au prochain créneau futur.

    **Coût de redémarrage borné** (retour utilisateur) : on ne rejoue **que** les villages
    réellement **exposés à la famine** — garnison non vide **et** blé net < 0. Un village
    sans troupe ou au blé net ≥ 0 ne peut **pas** être affamé ⇒ inutile de le re-ticker ici
    (son tick paresseux ultérieur ne tuera personne). Ça restreint le rejeu à la poignée de
    villages « armée > production » au lieu de re-ticker tout le monde — et écarte au passage
    les villages à **longue file de construction** (tick coûteux sur un grand trou ×vitesse,
    mais sans risque de famine). ⚠️ On ne saute **pas** les PNJ *jouables* : les **rivaux**
    (`engine.rivals`) sont `is_npc=True` mais de **tribu jouable** (hors `NPC_TRIBES`), donc
    affamables. Seule la **vraie tribu PNJ** (Nature/Natars) est écartée (déjà immunisée par
    `village._starve`)."""
    from app.data.tribes import NPC_TRIBES, Tribe
    for row in store.list_villages():
        if Tribe(row["tribe"]) in NPC_TRIBES:   # Nature/Natars : famine déjà impossible
            continue
        v = store.load_village(row["id"])
        if v is None or sum(v.troops) == 0:     # pas de garnison ⇒ rien à affamer
            continue
        net_crop = V.gross_production(v)[V.CROP] - V.population(v) - V.troop_upkeep(v)
        if net_crop >= 0:                       # blé soutenable ⇒ jamais de famine ⇒ rien à éponger
            continue
        V.tick(v, last_alive)              # avant l'arrêt : régime normal (famine active)
        V.tick(v, now, starve=False)       # pendant l'arrêt : famine en pause
        store.save_village(v)
    _roll_trade_routes(now)


def _roll_trade_routes(now: float) -> None:
    """Fait glisser `next_run` de chaque route échue au premier créneau **futur**,
    **sans** envoyer de cargaison (reprise propre, pas de rattrapage en rafale)."""
    for r in store.due_trade_routes(now):
        origin = store.load_village(r["origin_id"])
        if origin is None:
            continue
        effective = max(1.0, r["interval_hours"] * 3600.0 / max(1, origin.server_speed))
        nxt = r["next_run"]
        while nxt <= now:
            nxt += effective
        store.update_trade_route_next_run(r["id"], nxt)
