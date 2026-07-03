"""Macros pilotées par Claude Code (Claude Agent SDK) — registre + boucle.

Une **macro** = un agent LLM (Claude Code local, via `claude-agent-sdk`, donc l'auth de
ton abonnement, pas d'API) qui gère un village vers un objectif en langage naturel, en
n'utilisant QUE les outils de jeu de `app.agents.tools` (surface joueur légitime).

Garde-fous : outils intégrés de Claude Code interdits (gate `can_use_tool` +
`disallowed_tools`), `max_turns`, deadline wall-clock, `wait()` borné, une macro à la
fois par village, bouton stop = `interrupt()`.

Le registre est **en mémoire** (les macros ne survivent pas à un redémarrage uvicorn —
acceptable pour un outil de dev local).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.agents import tools as T

# Garde-fous par macro.
MAX_TURNS = 120                 # tours agent max (le SDK coupe au-delà)
DEADLINE_SECONDS = 30 * 60      # 30 min réelles max par macro
LOG_MAX = 400                   # entrées de journal conservées (fenêtre glissante)
TOOL_RESULT_PREVIEW = 1200      # troncature des résultats d'outil dans le journal

# Modèles proposés (alias acceptés par le CLI Claude Code).
MODELS = ("sonnet", "opus", "haiku")
DEFAULT_MODEL = "sonnet"

_SYSTEM_TEMPLATE = """\
Tu es un agent qui joue à une réimplémentation fidèle de Travian T4.6 (serveur en vitesse \
×100). Tu gères le village « {village_name} » (id {village_id}, tribu {tribe}) du joueur \
humain, vers cet OBJECTIF :

    {goal}

RÈGLES ABSOLUES — tu ne peux PAS tricher :
- Tu agis UNIQUEMENT via les outils `mcp__travian__*` fournis. Tu n'as aucun autre outil \
(pas de shell, pas de fichiers, pas de base de données). N'essaie pas d'en utiliser \
d'autres : ils sont refusés.
- Chaque outil passe par l'interface joueur normale : coûts en ressources, temps de \
construction/entraînement/trajet, files d'attente, portée, loyauté et propriété sont \
imposés par le jeu. Une action impossible renvoie « ERREUR : … » — lis le message et \
adapte-toi (ne réessaie pas à l'identique).

MÉTHODE :
1. Commence toujours par `get_state({village_id})` pour voir ressources, production, \
emplacements (avec coût/temps du niveau suivant), files et troupes.
2. Enchaîne les actions utiles jusqu'à saturer ce qui est faisable maintenant (files \
pleines ou ressources épuisées).
3. Quand tu dois attendre (chantier en cours, ressources à accumuler), appelle \
`wait({village_id}, seconds)` — le temps s'écoule vraiment et l'outil te renvoie l'état à \
jour. Choisis une durée d'attente raisonnable d'après les temps affichés (souvent \
quelques dizaines de secondes en ×100). Puis réévalue.
4. Répète observe → agis → attends jusqu'à ce que l'objectif soit atteint.
5. Termine par `finish(summary)` avec un court bilan.

Sois efficace : peu de bavardage, va droit aux appels d'outils. Ne dépasse pas ce que \
l'objectif demande.
"""


@dataclass
class MacroRun:
    village_id: int
    goal: str
    model: str
    status: str = "running"           # running | done | stopped | error
    log: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    turns: int = 0
    cost_usd: float = 0.0
    summary: str = ""
    error: str = ""
    deadline: float = 0.0
    stop_requested: bool = False
    task: asyncio.Task | None = None
    client: Any | None = None         # ClaudeSDKClient

    def add(self, kind: str, **data: Any) -> None:
        entry = {"t": round(time.time() - self.started_at, 1), "kind": kind, **data}
        self.log.append(entry)
        if len(self.log) > LOG_MAX:
            del self.log[: len(self.log) - LOG_MAX]

    def view(self) -> dict:
        return {
            "village_id": self.village_id, "goal": self.goal, "model": self.model,
            "status": self.status, "turns": self.turns,
            "cost_usd": round(self.cost_usd, 4),
            "elapsed": round((self.ended_at or time.time()) - self.started_at),
            "summary": self.summary, "error": self.error,
            "running": self.status == "running", "log": self.log,
        }


# Registre : une macro (au plus) par village.
MACROS: dict[int, MacroRun] = {}


def status_for(village_id: int) -> dict:
    mr = MACROS.get(village_id)
    return mr.view() if mr else {"status": "none", "running": False, "log": []}


def is_running(village_id: int) -> bool:
    mr = MACROS.get(village_id)
    return bool(mr and mr.status == "running")


def start_macro(village_id: int, goal: str, model: str,
                village_name: str, tribe: str) -> dict:
    """Démarre une macro sur `village_id`. Lève ValueError si une macro tourne déjà."""
    if is_running(village_id):
        raise ValueError("Une macro tourne déjà sur ce village.")
    if model not in MODELS:
        model = DEFAULT_MODEL
    mr = MacroRun(village_id=village_id, goal=goal.strip(), model=model)
    mr.deadline = mr.started_at + DEADLINE_SECONDS
    MACROS[village_id] = mr
    mr.add("info", text=f"Démarrage de la macro (modèle {model}).")
    mr.task = asyncio.create_task(_run(mr, village_name, tribe))
    return mr.view()


async def stop_macro(village_id: int) -> dict:
    mr = MACROS.get(village_id)
    if mr is None or mr.status != "running":
        return status_for(village_id)
    mr.stop_requested = True
    mr.add("info", text="Arrêt demandé.")
    if mr.client is not None:
        try:
            await mr.client.interrupt()
        except Exception:
            pass
    return mr.view()


async def _run(mr: MacroRun, village_name: str, tribe: str) -> None:
    """Boucle de fond : connecte l'agent, stream ses messages dans le journal."""
    try:
        from claude_agent_sdk import (
            ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, UserMessage,
            ResultMessage, TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock,
            PermissionResultAllow, PermissionResultDeny,
        )
    except Exception as e:  # SDK absent
        _fail(mr, f"claude-agent-sdk indisponible : {e}")
        return

    async def can_use_tool(name: str, _inp: dict, _ctx: Any):
        # Gate dur « sans tricher » : seuls nos outils de jeu passent.
        if name.startswith(T.TOOL_PREFIX):
            return PermissionResultAllow()
        return PermissionResultDeny(message="Outil interdit : la macro ne peut utiliser "
                                    "que les actions de jeu mcp__travian__*.")

    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM_TEMPLATE.format(
            village_name=village_name, village_id=mr.village_id, tribe=tribe, goal=mr.goal),
        mcp_servers={T.SERVER_NAME: T.build_server()},
        allowed_tools=T.ALLOWED_TOOL_NAMES,
        # Blocage dur des outils intégrés qui pourraient tricher (toucher game.db,
        # exécuter du code, lire des fichiers, sortir sur le réseau, déléguer à un
        # sous-agent). ToolSearch/TodoWrite (méta, lecture seule) restent tolérés — le
        # CLI s'en sert pour découvrir nos outils mcp__travian__*.
        disallowed_tools=["Bash", "BashOutput", "KillShell", "Read", "Write", "Edit",
                          "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch",
                          "Task"],
        can_use_tool=can_use_tool,
        permission_mode="default",
        setting_sources=None,        # n'hérite d'aucun CLAUDE.md / settings du dépôt
        max_turns=MAX_TURNS,
        model=mr.model,
    )

    watchdog = asyncio.create_task(_deadline_watchdog(mr))
    try:
        async with ClaudeSDKClient(options=options) as client:
            mr.client = client
            await client.query(
                f"Poursuis l'objectif sur le village {mr.village_id}. "
                "Commence par observer l'état, puis agis. Termine par finish().")
            async for msg in client.receive_response():
                if mr.stop_requested:
                    break
                if isinstance(msg, AssistantMessage):
                    mr.turns += 1
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            if block.text.strip():
                                mr.add("assistant", text=block.text.strip())
                        elif isinstance(block, ThinkingBlock):
                            pass  # réflexion interne, non journalisée
                        elif isinstance(block, ToolUseBlock):
                            short = block.name.split("__")[-1]
                            mr.add("tool_use", tool=short, input=block.input)
                            if short == "finish":
                                mr.summary = str(block.input.get("summary", ""))
                elif isinstance(msg, UserMessage):
                    for block in getattr(msg, "content", []) or []:
                        if isinstance(block, ToolResultBlock):
                            mr.add("tool_result",
                                   text=_preview(block.content),
                                   error=bool(block.is_error))
                elif isinstance(msg, ResultMessage):
                    if msg.total_cost_usd:
                        mr.cost_usd = msg.total_cost_usd
                    mr.turns = msg.num_turns or mr.turns
                    if msg.is_error:
                        _fail(mr, msg.result or "Erreur de l'agent.")
                        return
                    if not mr.summary and msg.result:
                        mr.summary = str(msg.result)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # CLINotFoundError, ProcessError, etc.
        _fail(mr, _diagnose(e))
        return
    finally:
        watchdog.cancel()
        mr.client = None

    if mr.stop_requested:
        mr.status = "stopped"
        mr.add("info", text="Macro arrêtée.")
    elif mr.status == "running":
        mr.status = "done"
        mr.add("info", text="Macro terminée.")
    mr.ended_at = time.time()


async def _deadline_watchdog(mr: MacroRun) -> None:
    """Interrompt la macro si elle dépasse la deadline wall-clock."""
    try:
        remaining = mr.deadline - time.time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        mr.stop_requested = True
        mr.add("info", text="Deadline atteinte — arrêt automatique.")
        if mr.client is not None:
            await mr.client.interrupt()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def _preview(content: Any) -> str:
    """Aplati/tronque un contenu de tool_result pour le journal."""
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c)))
            else:
                parts.append(str(getattr(c, "text", c)))
        text = "\n".join(parts)
    else:
        text = str(content)
    return text[:TOOL_RESULT_PREVIEW] + ("…" if len(text) > TOOL_RESULT_PREVIEW else "")


def _diagnose(e: Exception) -> str:
    name = type(e).__name__
    if "CLINotFound" in name:
        return ("CLI `claude` introuvable : installe/lance Claude Code (le SDK pilote "
                "le binaire `claude`).")
    return f"{name} : {e}"


def _fail(mr: MacroRun, msg: str) -> None:
    mr.status = "error"
    mr.error = msg
    mr.ended_at = time.time()
    mr.add("error", text=msg)
