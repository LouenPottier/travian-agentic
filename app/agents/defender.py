"""Agent défenseur **par village**, piloté à la main (Phase 4).

Modèle voulu par l'utilisateur : l'agent **ne se réveille jamais tout seul**. Sur demande
(« Réveiller »), il joue **un seul tour** — il observe, (re)pose sa **pile d'ordres
permanents** via `set_plan` — puis **dort**. Entre deux réveils, la pile est réalisée
**automatiquement** par l'exécuteur `playbook` (0 LLM). Trois commandes, indépendantes, par
village :

- **Réveiller** : un tour de LLM (observe → set_plan → finish note).
- **Débrancher le LLM** : interrompt un tour en cours **sans toucher la pile** ⇒ l'exécuteur
  continue de bâtir/entraîner/piéger selon le dernier plan, mais plus aucun tour de LLM.
- **Arrêter** : débranche le LLM **et vide la pile** (l'exécuteur s'arrête pour ce village).

Cerveau = Claude Code local (Claude Agent SDK, abonnement, pas d'API payante ; gate
`mcp__travian__*`, `disallowed_tools`, `interrupt()`). L'agent agit AU NOM du propriétaire du
village (en-tête `X-Acting-Player`). Registre **en mémoire** (clé = `village_id`).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.engine import situation as SIT
from app.agents import tools as T
from app.agents import playbook as PB
from app.agents.macro import _preview, _diagnose, LOG_MAX

TURN_MAX_TURNS = 24            # tours d'outils max d'UN réveil
TURN_DEADLINE = 5 * 60        # 5 min réelles max pour un tour (watchdog)

MODELS = ("sonnet", "opus", "haiku")
DEFAULT_MODEL = "sonnet"

_SYSTEM = """\
Tu es un agent qui DÉFEND le village « {village_name} » (id {village_id}, joueur {owner_id}, \
tribu {tribe}) d'une réimplémentation fidèle de Travian T4.6 (serveur ×100).

Tu joues UN SEUL TOUR puis tu t'arrêtes : tu observes, tu (re)poses ta PILE D'ORDRES \
PERMANENTS, tu termines. Entre deux réveils, un exécuteur réalise ta pile TOUT SEUL (sans \
LLM) ; tu ne seras rappelé que si l'utilisateur te réveille.

DOCTRINE (défense uniquement — jamais d'attaque ni de razzia) :
- Prépare la défense : entraîne de l'infanterie défensive, monte la muraille, pose des pièges \
(si Gaulois), garde une production positive et des entrepôts/greniers suffisants.
- Si une menace entre, renforce le village visé depuis TES autres villages (send_army \
'reinforce', vers tes villages seulement). Tu ne connais PAS la composition ennemie — \
dimensionne prudemment.

MÉTHODE DU TOUR :
1. `get_situation` (digest compact de ton compte) — utilise `get_state`/`get_reports` seulement \
si tu as besoin de détail.
2. Agis tout de suite si c'est urgent (renfort…).
3. `set_plan` : pose des ORDRES PERMANENTS pour ce village (build/construct/train/traps/ \
research) que l'exécuteur réalisera automatiquement. C'est le CŒUR de ton tour : investis-y \
une pile défensive solide et priorisée.
4. `finish(note)` : résume l'état et ta stratégie dans la note (elle te sera redonnée au \
prochain réveil). Sois bref, va droit aux outils.

RÈGLES : tu agis UNIQUEMENT via `mcp__travian__*` (aucun shell/fichier). Le jeu impose \
coûts/temps/files/portée/propriété ; une action impossible renvoie « ERREUR … » — adapte-toi.
"""


@dataclass
class DefenderRun:
    village_id: int
    owner_id: int
    model: str = DEFAULT_MODEL
    village_name: str = ""
    tribe: str = ""
    status: str = "idle"          # idle | thinking | asleep | unplugged | stopped | error
    log: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    last_wake: float = 0.0
    thinks: int = 0
    cost_usd: float = 0.0
    note: str = ""                # note persistante inter-réveils
    task: asyncio.Task | None = None
    client: Any | None = None

    def add(self, kind: str, **data: Any) -> None:
        self.log.append({"t": round(time.time() - self.started_at, 1), "kind": kind, **data})
        if len(self.log) > LOG_MAX:
            del self.log[: len(self.log) - LOG_MAX]

    def view(self) -> dict:
        return {
            "village_id": self.village_id, "owner_id": self.owner_id, "model": self.model,
            "village_name": self.village_name, "status": self.status, "thinks": self.thinks,
            "cost_usd": round(self.cost_usd, 4),
            "thinking": self.status == "thinking",
            "has_plan": bool(PB.get_plan(self.village_id)),
            "plan_size": len(PB.get_plan(self.village_id)),
            "note": self.note, "log": self.log,
        }


# Registre : un agent défenseur (au plus) par village.
AGENTS: dict[int, DefenderRun] = {}


def status_for(village_id: int) -> dict:
    dr = AGENTS.get(village_id)
    if dr:
        return dr.view()
    return {"village_id": village_id, "status": "none", "thinking": False,
            "has_plan": bool(PB.get_plan(village_id)),
            "plan_size": len(PB.get_plan(village_id)), "note": "", "log": []}


def is_thinking(village_id: int) -> bool:
    dr = AGENTS.get(village_id)
    return bool(dr and dr.status == "thinking")


def wake(village_id: int, owner_id: int, model: str,
         village_name: str, tribe: str) -> dict:
    """Fait jouer UN tour de LLM au défenseur de ce village (asynchrone : l'UI suit via GET)."""
    if is_thinking(village_id):
        return AGENTS[village_id].view()
    if model not in MODELS:
        model = DEFAULT_MODEL
    dr = AGENTS.get(village_id)
    if dr is None:
        dr = DefenderRun(village_id=village_id, owner_id=owner_id)
        AGENTS[village_id] = dr
    dr.model = model
    dr.owner_id = owner_id
    dr.village_name = village_name
    dr.tribe = tribe
    dr.status = "thinking"
    dr.last_wake = time.time()
    dr.add("wake", text="Réveil : un tour de jeu.")
    dr.task = asyncio.create_task(_turn(dr))
    return dr.view()


async def unplug(village_id: int) -> dict:
    """Débranche le LLM SANS toucher la pile : interrompt un tour en cours, garde le plan
    (l'exécuteur `playbook` continue de le réaliser). Réveil manuel requis pour reprendre."""
    dr = AGENTS.get(village_id)
    if dr is None:
        return status_for(village_id)
    await _interrupt(dr)
    dr.status = "unplugged"
    dr.add("info", text="LLM débranché — la pile d'ordres reste active (exécuteur).")
    return dr.view()


async def stop(village_id: int) -> dict:
    """Arrêt complet : débranche le LLM ET vide la pile (l'exécuteur s'arrête pour ce village)."""
    dr = AGENTS.get(village_id)
    if dr is None:
        PB.clear_village(village_id)
        return status_for(village_id)
    await _interrupt(dr)
    PB.clear_village(village_id)
    dr.status = "stopped"
    dr.add("info", text="Arrêt complet — pile d'ordres vidée.")
    return dr.view()


async def _interrupt(dr: DefenderRun) -> None:
    """Interrompt le tour LLM en cours (le cas échéant)."""
    if dr.client is not None:
        try:
            await dr.client.interrupt()
        except Exception:
            pass
    if dr.task is not None and not dr.task.done():
        dr.task.cancel()


async def _turn(dr: DefenderRun) -> None:
    """UN tour de LLM : observe → agit/pose la pile → finish. Puis retour au sommeil."""
    try:
        from claude_agent_sdk import (
            ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, UserMessage,
            ResultMessage, TextBlock, ToolUseBlock, ToolResultBlock,
            PermissionResultAllow, PermissionResultDeny,
        )
    except Exception as e:
        dr.status = "error"
        dr.add("error", text=f"claude-agent-sdk indisponible : {e}")
        return

    async def can_use_tool(name: str, _inp: dict, _ctx: Any):
        if name.startswith(T.TOOL_PREFIX):
            return PermissionResultAllow()
        return PermissionResultDeny(message="Outil interdit : défense via mcp__travian__* "
                                    "uniquement.")

    options = ClaudeAgentOptions(
        system_prompt=_SYSTEM.format(village_name=dr.village_name, village_id=dr.village_id,
                                     owner_id=dr.owner_id, tribe=dr.tribe),
        mcp_servers={T.SERVER_NAME: T.build_server()},
        allowed_tools=T.DEFENSIVE_TOOL_NAMES,
        disallowed_tools=["Bash", "BashOutput", "KillShell", "Read", "Write", "Edit",
                          "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Task"],
        can_use_tool=can_use_tool,
        permission_mode="default",
        setting_sources=None,
        max_turns=TURN_MAX_TURNS,
        model=dr.model,
    )

    digest = SIT.build_digest(dr.owner_id, time.time())
    prompt = (f"Ta note précédente :\n{dr.note or '(aucune)'}\n\n"
              f"Situation actuelle :\n{SIT.render_digest(digest)}\n\n"
              f"Défends surtout le village {dr.village_id}. Agis si urgent, (re)pose tes "
              "ORDRES PERMANENTS avec set_plan, puis finish(note).")

    watchdog = asyncio.create_task(_deadline(dr))
    T.set_acting_player(dr.owner_id)
    try:
        async with ClaudeSDKClient(options=options) as client:
            dr.client = client
            dr.thinks += 1
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            dr.add("assistant", text=block.text.strip())
                        elif isinstance(block, ToolUseBlock):
                            short = block.name.split("__")[-1]
                            dr.add("tool_use", tool=short, input=block.input)
                            if short == "finish":
                                dr.note = str(block.input.get("summary", "")) or dr.note
                elif isinstance(msg, UserMessage):
                    for block in getattr(msg, "content", []) or []:
                        if isinstance(block, ToolResultBlock):
                            dr.add("tool_result", text=_preview(block.content),
                                   error=bool(block.is_error))
                elif isinstance(msg, ResultMessage):
                    if msg.total_cost_usd:
                        dr.cost_usd += msg.total_cost_usd
                    if not dr.note and msg.result:
                        dr.note = str(msg.result)
    except asyncio.CancelledError:
        return  # débranché/arrêté : le statut a déjà été fixé par unplug/stop
    except Exception as e:
        dr.status = "error"
        dr.add("error", text=_diagnose(e))
        return
    finally:
        watchdog.cancel()
        T.set_acting_player(None)
        dr.client = None
    # Fin normale du tour : retour au sommeil (l'exécuteur continue la pile).
    if dr.status == "thinking":
        dr.status = "asleep"
        dr.add("info", text="Tour terminé — sommeil (l'exécuteur réalise la pile).")


async def _deadline(dr: DefenderRun) -> None:
    """Interrompt un tour trop long (garde-fou wall-clock)."""
    try:
        await asyncio.sleep(TURN_DEADLINE)
        dr.add("info", text="Tour trop long — interruption.")
        if dr.client is not None:
            await dr.client.interrupt()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
