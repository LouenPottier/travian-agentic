"""Serveur MCP in-process : les actions de jeu offertes à l'agent LLM (macro).

**Principe « sans tricher » :** chaque outil ne fait que **forwarder vers l'endpoint
HTTP joueur existant** (`http://127.0.0.1:8000/api/...`). Il n'y a donc AUCUNE logique de
jeu ici — les coûts, les temps, les files, la loyauté et surtout l'ownership
(`v.player_id != HUMAN_PLAYER_ID` ⇒ 403) sont enforced par le serveur, exactement comme
pour un humain cliquant dans l'UI. L'agent ne peut rien faire d'autre que ces outils :
le lanceur de macro interdit les outils intégrés de Claude Code (Bash/Read/Write/…), si
bien qu'il ne peut jamais toucher `game.db` directement.

Les erreurs 400/403 des endpoints sont renvoyées telles quelles à l'agent (texte français
« ERREUR : … ») : c'est un excellent feedback pour qu'il corrige son action.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
from typing import Any

import httpx

from claude_agent_sdk import create_sdk_mcp_server, tool

# Base du serveur local (le process uvicorn lui-même). Configurable pour les tests.
BASE_URL = os.environ.get("TRAVIAN_BASE_URL", "http://127.0.0.1:8000")

# Joueur agissant de la session courante (Phase 4). Posé par le défenseur / l'exécuteur
# `playbook` (et éventuellement une macro) pour agir AU NOM d'un autre compte via
# l'en-tête `X-Acting-Player`. Absent (None) ⇒ pas d'en-tête ⇒ le serveur retombe sur
# `HUMAN_PLAYER_ID` (comportement historique de la macro humaine, inchangé).
_ACTING: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "tools_acting_player", default=None)


def set_acting_player(pid: int | None) -> None:
    """Fixe (ou efface avec None) le joueur agissant de la session/tâche courante."""
    _ACTING.set(pid)

# Un `wait` ne peut pas dormir indéfiniment : borne par appel (le garde-fou de durée
# totale vit dans macro.py). En vitesse ×100 la plupart des chantiers durent quelques
# secondes/minutes réelles ⇒ 300 s suffisent largement par palier d'attente.
WAIT_CAP_SECONDS = 300

_client: httpx.AsyncClient | None = None


async def _http() -> httpx.AsyncClient:
    """Client HTTP paresseux, lié à la boucle asyncio courante."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
    return _client


async def _req(method: str, path: str, *, json_body: Any | None = None,
               params: dict | None = None) -> str:
    """Appelle l'endpoint et renvoie un texte compact (état JSON ou « ERREUR : … »)."""
    pid = _ACTING.get()
    headers = {"X-Acting-Player": str(pid)} if pid is not None else None
    try:
        r = await (await _http()).request(method, path, json=json_body, params=params,
                                          headers=headers)
    except httpx.HTTPError as e:  # serveur injoignable, timeout…
        return f"ERREUR RÉSEAU : {e}"
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        return f"ERREUR ({r.status_code}) : {detail}"
    try:
        return json.dumps(r.json(), ensure_ascii=False)
    except Exception:
        return r.text


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


# --- Observation (uniquement ce que voit un joueur ; état déjà owner-gated) ---------

@tool("get_state", "État complet d'un de TES villages (ressources, production, "
      "emplacements/bâtiments avec coût et temps du niveau suivant, files, troupes, "
      "unités entraînables, mouvements, loyauté…). Point de départ de toute décision.",
      {"village_id": int})
async def get_state(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/village/{args['village_id']}"))


@tool("list_villages", "Liste tous les villages du monde (id, nom, coordonnées, joueur, "
      "is_own). Sert à connaître tes villages et à repérer des cibles.", {})
async def list_villages(_args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/villages"))


@tool("get_map", "Fenêtre de la carte autour de (cx, cy), rayon r (1..15) : vallées "
      "libres, oasis (bonus/animaux) et villages. Pour trouver des cibles ou une vallée "
      "où fonder.", {"cx": int, "cy": int, "r": int})
async def get_map(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/map",
                          params={"cx": args["cx"], "cy": args["cy"], "r": args["r"]}))


@tool("get_tile", "Détail d'une case (x, y) : village, oasis (bonus/animaux/détenteur/"
      "garnison si à toi/villages éligibles pour l'annexer) ou vallée.",
      {"x": int, "y": int})
async def get_tile(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/tile/{args['x']}/{args['y']}"))


@tool("get_hero", "État de ton héros (santé, niveau/XP, attributs, équipement/sac, "
      "aventures disponibles, statut).", {})
async def get_hero(_args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/hero"))


@tool("get_situation", "Digest COMPACT de tout ton compte (défense) : menaces entrantes "
      "(kind/ETA/effectif, composition inconnue comme dans l'UI), nouveaux rapports, et "
      "résumé terse par village (ressources/prod, mur, pièges, loyauté, troupes, file). "
      "Point de départ du défenseur — bien plus léger que get_state.", {})
async def get_situation(_args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/agent/situation"))


@tool("get_reports", "Tes derniers rapports (combats subis, espionnage détecté, renforts "
      "reçus…). Indispensable pour savoir ce qui vient de t'arriver.", {})
async def get_reports(_args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/reports"))


@tool("get_trapper", "État du trappeur (gaulois) d'un village : capacité, pièges "
      "construits/en cours/libres, prisonniers retenus.", {"village_id": int})
async def get_trapper(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/village/{args['village_id']}/trapper"))


@tool("academy_info", "Académie d'un village : unités recherchables (coût/temps), déjà "
      "recherchées, en cours de recherche.", {"village_id": int})
async def academy_info(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/village/{args['village_id']}/academy"))


@tool("smithy_info", "Forge d'un village : niveau d'amélioration courant et coût/temps du "
      "niveau suivant par unité.", {"village_id": int})
async def smithy_info(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/village/{args['village_id']}/smithy"))


@tool("market_info", "Place de marché d'un village : marchands (total/libres), capacité, "
      "ressources, et cibles d'envoi possibles avec distance et temps de trajet.",
      {"village_id": int})
async def market_info(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", f"/api/village/{args['village_id']}/market"))


@tool("expansion_info", "Points de culture, emplacements d'expansion et colons "
      "disponibles pour fonder un nouveau village.", {})
async def expansion_info(_args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("GET", "/api/expansion"))


# --- Actions : bâtiments / champs ---------------------------------------------------

@tool("build", "Enfile la montée d'UN niveau du bâtiment/champ à l'emplacement `slot_index`. "
      "File **illimitée** : l'ordre démarre dès qu'un créneau se libère ET que les "
      "ressources sont là (payées **au démarrage**, pas à la mise en file). Répète l'appel "
      "pour enfiler plusieurs niveaux du même emplacement. `get_state` liste la file "
      "(`build_plan`) et le niveau projeté.",
      {"village_id": int, "slot_index": int})
async def build(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/build/{args['slot_index']}"))


@tool("construct", "Enfile la pose d'un NOUVEAU bâtiment `building_id` sur un emplacement "
      "vide `slot_index` (même file illimitée, paiement au démarrage). Les emplacements "
      "vides et leurs bâtiments constructibles (avec id) figurent dans get_state.",
      {"village_id": int, "slot_index": int, "building_id": int})
async def construct(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req(
        "POST",
        f"/api/village/{args['village_id']}/construct/{args['slot_index']}/{args['building_id']}"))


@tool("cancel_build", "Annule l'ordre de construction **en attente** à la position `pos` "
      "de la file de planification (`build_plan` dans get_state). Les ordres déjà démarrés "
      "ne sont pas annulables.",
      {"village_id": int, "pos": int})
async def cancel_build(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST",
                          f"/api/village/{args['village_id']}/build/cancel/{args['pos']}"))


@tool("demolish", "Démolit l'emplacement `slot_index` (nécessite bâtiment principal niv "
      "10+). `target_level` optionnel : niveau visé (omis ⇒ un seul niveau ; 0 ⇒ "
      "destruction complète).",
      {"type": "object",
       "properties": {"village_id": {"type": "integer"},
                      "slot_index": {"type": "integer"},
                      "target_level": {"type": "integer"}},
       "required": ["village_id", "slot_index"]})
async def demolish(args: dict[str, Any]) -> dict[str, Any]:
    params = {"target_level": args["target_level"]} if "target_level" in args else None
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/demolish/{args['slot_index']}",
                          params=params))


@tool("make_capital", "Déclare ce village comme capitale (exige un palais niv ≥ 1 ; "
      "rétrograde l'ancienne capitale).", {"village_id": int})
async def make_capital(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/make-capital"))


# --- Actions : militaire / recherche / forge ----------------------------------------

@tool("train", "Entraîne `count` unités d'index `unit_index` dans le bâtiment "
      "`building_id` (caserne/écurie/atelier/résidence/palais, ou leurs grandes "
      "variantes). Les unités entraînables par bâtiment figurent dans get_state (champ "
      "`military`).",
      {"village_id": int, "building_id": int, "unit_index": int, "count": int})
async def train(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req(
        "POST",
        f"/api/village/{args['village_id']}/train/{args['building_id']}/{args['unit_index']}/{args['count']}"))


@tool("research", "Recherche l'unité `unit_index` à l'académie (débloque son "
      "entraînement). Voir academy_info pour la liste et les coûts.",
      {"village_id": int, "unit_index": int})
async def research(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/research/{args['unit_index']}"))


@tool("upgrade", "Améliore d'un niveau l'unité `unit_index` à la forge (plafonné par le "
      "niveau de la forge). Voir smithy_info.", {"village_id": int, "unit_index": int})
async def upgrade(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/upgrade/{args['unit_index']}"))


@tool("set_traps", "Construit `count` pièges chez le trappeur (Gaulois).",
      {"village_id": int, "count": int})
async def set_traps(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/traps/{args['count']}"))


@tool("celebration", "Lance une célébration à l'hôtel de ville : `ctype` 1 = petite fête "
      "(niv 1+), 2 = grande fête (niv 10+). Génère des points de culture.",
      {"village_id": int, "ctype": int})
async def celebration(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/celebration/{args['ctype']}"))


# --- Actions : envoi d'armée (attaque / razzia / renfort / espionnage) ---------------

@tool("send_army",
      "Envoie des troupes depuis `village_id`. `kind` : 'attack' (bataille + siège), "
      "'raid' (razzia, butin, pas de siège), 'reinforce' (renfort d'un village/oasis à "
      "toi ; le héros s'y réinstalle), 'scout' (espionnage, uniquement des éclaireurs). "
      "`units` = liste de 10 entiers (nombre par index d'unité). Cible : soit "
      "`target_id` (village), soit `target_x`/`target_y` (oasis). Options : `with_hero` "
      "(embarquer le héros), `targets` (liste d'ids de bâtiments visés par les "
      "catapultes, attaque seulement), `scout_mode` ('res' ou 'def').",
      {"type": "object",
       "properties": {
           "village_id": {"type": "integer"},
           "kind": {"type": "string", "enum": ["attack", "raid", "reinforce", "scout"]},
           "units": {"type": "array", "items": {"type": "integer"}},
           "target_id": {"type": "integer"},
           "target_x": {"type": "integer"},
           "target_y": {"type": "integer"},
           "with_hero": {"type": "boolean"},
           "targets": {"type": "array", "items": {"type": "integer"}},
           "scout_mode": {"type": "string", "enum": ["res", "def"]}},
       "required": ["village_id", "kind", "units"]})
async def send_army(args: dict[str, Any]) -> dict[str, Any]:
    body = {
        "kind": args["kind"],
        "units": args.get("units", []),
        "target_id": args.get("target_id"),
        "target_x": args.get("target_x"),
        "target_y": args.get("target_y"),
        "with_hero": args.get("with_hero", False),
        "targets": args.get("targets"),
        "scout_mode": args.get("scout_mode"),
    }
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/send", json_body=body))


# --- Actions : commerce -------------------------------------------------------------

@tool("trade", "Envoie des ressources (marchands) de `village_id` vers le village "
      "`target_id`. `amounts` = [bois, argile, fer, céréales].",
      {"type": "object",
       "properties": {"village_id": {"type": "integer"}, "target_id": {"type": "integer"},
                      "amounts": {"type": "array", "items": {"type": "integer"}}},
       "required": ["village_id", "target_id", "amounts"]})
async def trade(args: dict[str, Any]) -> dict[str, Any]:
    body = {"target_id": args["target_id"], "amounts": args["amounts"]}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/trade", json_body=body))


@tool("create_trade_route", "Crée une route commerciale récurrente : envoie "
      "périodiquement `amounts` [bois, argile, fer, céréales] de `village_id` vers "
      "`target_id` toutes les `interval_hours` heures (temps de base).",
      {"type": "object",
       "properties": {"village_id": {"type": "integer"}, "target_id": {"type": "integer"},
                      "amounts": {"type": "array", "items": {"type": "integer"}},
                      "interval_hours": {"type": "number"}},
       "required": ["village_id", "target_id", "amounts", "interval_hours"]})
async def create_trade_route(args: dict[str, Any]) -> dict[str, Any]:
    body = {"target_id": args["target_id"], "amounts": args["amounts"],
            "interval_hours": args["interval_hours"]}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/trade_route", json_body=body))


# --- Actions : farm list ------------------------------------------------------------

@tool("farmlist_add", "Ajoute une cible à la liste de fermes de `village_id` avec un "
      "modèle de troupes `units` (10 entiers). Cible : `target_id` (village) ou "
      "`target_x`/`target_y` (oasis).",
      {"type": "object",
       "properties": {"village_id": {"type": "integer"},
                      "units": {"type": "array", "items": {"type": "integer"}},
                      "target_id": {"type": "integer"},
                      "target_x": {"type": "integer"}, "target_y": {"type": "integer"}},
       "required": ["village_id", "units"]})
async def farmlist_add(args: dict[str, Any]) -> dict[str, Any]:
    body = {"units": args.get("units", []), "target_id": args.get("target_id"),
            "target_x": args.get("target_x"), "target_y": args.get("target_y")}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/farmlist", json_body=body))


@tool("farmlist_raid", "Lance une razzia groupée sur toutes les cibles de la liste de "
      "fermes de `village_id` (saute celles aux troupes insuffisantes).",
      {"village_id": int})
async def farmlist_raid(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/farmlist/raid"))


# --- Actions : expansion / oasis ----------------------------------------------------

@tool("settle", "Envoie 3 colons depuis `village_id` fonder un village sur la vallée "
      "libre (x, y). Exige colons + points de culture + emplacement d'expansion (voir "
      "expansion_info).", {"village_id": int, "x": int, "y": int})
async def settle(args: dict[str, Any]) -> dict[str, Any]:
    body = {"x": args["x"], "y": args["y"]}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/settle", json_body=body))


@tool("occupy_oasis", "Annexe l'oasis libre et nettoyée (x, y) à `village_id` (exige un "
      "manoir du héros de niveau suffisant et la portée).",
      {"village_id": int, "x": int, "y": int})
async def occupy_oasis(args: dict[str, Any]) -> dict[str, Any]:
    body = {"x": args["x"], "y": args["y"]}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/oasis/occupy", json_body=body))


@tool("abandon_oasis", "Abandonne l'oasis (x, y) occupée par `village_id` (rapatrie la "
      "garnison).", {"village_id": int, "x": int, "y": int})
async def abandon_oasis(args: dict[str, Any]) -> dict[str, Any]:
    body = {"x": args["x"], "y": args["y"]}
    return _ok(await _req("POST", f"/api/village/{args['village_id']}/oasis/abandon", json_body=body))


# --- Ordres permanents (exécutés par Python, 0 LLM) ---------------------------------

@tool("set_plan",
      "Enregistre/remplace tes ORDRES PERMANENTS pour un village : un exécuteur Python les "
      "réalise AUTOMATIQUEMENT quand les ressources/la file le permettent (tu n'as PAS à "
      "rester réveillé). Économise énormément de tokens. `actions` = liste d'ordres, chacun "
      "un objet avec `op` et `village_id` :\n"
      "- {op:'build', village_id, slot, level} : monter l'emplacement `slot` jusqu'à `level`.\n"
      "- {op:'construct', village_id, slot, building_id, level} : bâtir puis monter à `level`.\n"
      "- {op:'train', village_id, building_id, unit, count} : maintenir `count` unités "
      "(index `unit`) — ré-entraîne après pertes.\n"
      "- {op:'traps', village_id, count} : maintenir `count` pièges (trappeur gaulois).\n"
      "- {op:'research', village_id, unit} : rechercher l'unité à l'académie.\n"
      "Une liste vide efface le plan. Priorise les ordres (les premiers passent d'abord).",
      {"type": "object",
       "properties": {"actions": {"type": "array", "items": {"type": "object"}}},
       "required": ["actions"]})
async def set_plan(args: dict[str, Any]) -> dict[str, Any]:
    from app.agents import playbook as PB
    pid = _ACTING.get()
    if pid is None:
        # Repli : le serveur nous dira qui est le joueur humain via /api/villages.
        try:
            data = json.loads(await _req("GET", "/api/villages"))
            pid = data.get("human_player_id")
        except Exception:
            pid = None
    if pid is None:
        return _ok("ERREUR : joueur agissant inconnu.")
    actions = args.get("actions", [])
    # Chaque ordre doit être rattaché à un village ; on tente, on renvoie l'erreur sinon.
    try:
        orders = PB.set_plan_multi(int(pid), actions)
    except PB.PlanError as e:
        return _ok(f"ERREUR : {e}")
    return _ok(f"Plan enregistré ({len(orders)} ordre(s)). L'exécuteur les réalisera "
               "automatiquement ; réveille-toi seulement sur événement.")


# --- Contrôle de boucle -------------------------------------------------------------

@tool("wait", "Laisse le temps s'écouler `seconds` secondes réelles (borné à 300), puis "
      "renvoie l'état frais de `village_id`. À utiliser pour attendre la fin d'un "
      "chantier / l'accumulation de ressources avant de réévaluer (la production et les "
      "constructions avancent au fil du temps).",
      {"village_id": int, "seconds": int})
async def wait(args: dict[str, Any]) -> dict[str, Any]:
    delay = max(0, min(int(args.get("seconds", 0)), WAIT_CAP_SECONDS))
    await asyncio.sleep(delay)
    state = await _req("GET", f"/api/village/{args['village_id']}")
    return _ok(f"(attendu {delay}s) État à jour :\n{state}")


@tool("finish", "Signale que l'objectif est atteint (ou qu'il ne peut plus progresser) "
      "avec un court résumé. Termine la macro.", {"summary": str})
async def finish(args: dict[str, Any]) -> dict[str, Any]:
    return _ok(f"Macro terminée : {args.get('summary', '')}")


# Tous les outils exposés à l'agent. `finish` doit être le dernier appel d'une macro.
ALL_TOOLS = [
    get_state, list_villages, get_map, get_tile, get_hero,
    get_situation, get_reports, get_trapper,
    academy_info, smithy_info, market_info, expansion_info,
    build, construct, cancel_build, demolish, make_capital,
    train, research, upgrade, set_traps, celebration,
    send_army, trade, create_trade_route,
    farmlist_add, farmlist_raid,
    settle, occupy_oasis, abandon_oasis,
    set_plan, wait, finish,
]

SERVER_NAME = "travian"
# Préfixe des noms d'outils tels que vus par le CLI : mcp__{server}__{tool}.
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
ALLOWED_TOOL_NAMES = [f"{TOOL_PREFIX}{t.name}" for t in ALL_TOOLS]

# Sous-ensemble DÉFENSIF (Phase 4, joueur IA défenseur) : lecture + fortification +
# renfort + ordres permanents, sans attaque/expansion/commerce (doctrine plus simple).
DEFENSIVE_TOOLS = [
    get_situation, get_state, get_reports, get_trapper, list_villages, get_hero,
    academy_info, smithy_info,
    build, construct, cancel_build, train, research, upgrade, set_traps,
    send_army,  # renfort uniquement (la doctrine l'impose ; le serveur autorise reinforce)
    set_plan, finish,
]
DEFENSIVE_TOOL_NAMES = [f"{TOOL_PREFIX}{t.name}" for t in DEFENSIVE_TOOLS]


def build_server():
    """Construit le serveur MCP in-process (config passée à ClaudeAgentOptions)."""
    return create_sdk_mcp_server(SERVER_NAME, "1.0.0", ALL_TOOLS)


async def aclose() -> None:
    """Ferme le client HTTP (utile en test)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
