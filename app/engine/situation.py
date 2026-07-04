"""Digest compact + décision de réveil pour l'agent joueur (Phase 4, défenseur).

**But :** donner au LLM une observation MINUSCULE (quelques centaines de tokens pour tout
le compte) au lieu du `serialize()` verbeux rejoué en boucle. La couche Python agrège tout
ici ; le LLM n'est réveillé (`should_wake`) que pour un **événement** non couvert par le
plan d'ordres permanents (menace entrante, nouveau rapport, plan épuisé).

**Parité d'observation (« sans tricher ») :** les menaces entrantes n'exposent QUE ce qu'un
humain voit dans l'UI (`serialize()` : kind / sens / ETA / effectif total) — jamais la
composition ni l'identité de l'attaquant.

Fonctions **pures** (aucun LLM, aucun HTTP) ⇒ testables directement.
"""
from __future__ import annotations

import json
import time

from app import store
from app.data.units import UNITS
from app.engine import village as V

# Mouvements entrants qui comptent comme menace (l'espionnage est listé à part : il ne
# détruit rien mais révèle une hostilité).
THREAT_KINDS = ("attack", "raid")


def _threats_for(village_id: int, now: float) -> list[dict]:
    """Menaces entrantes vers ce village (aller uniquement) : kind / ETA / effectif —
    strictement ce que l'UI montre déjà (parité)."""
    out = []
    for m in store.movements_for(village_id):
        if m["phase"] != "outbound" or m["target_id"] != village_id:
            continue  # seulement l'aller ENTRANT vers ce village
        if m["kind"] not in THREAT_KINDS and m["kind"] != "scout":
            continue
        out.append({"kind": m["kind"],
                    "arrive_in": round(m["arrive_at"] - now),
                    "n": sum(json.loads(m["units"]))})
    out.sort(key=lambda t: t["arrive_in"])
    return out


def _village_line(v: V.Village, now: float) -> dict:
    """Résumé terse d'un village (défense). Tick pour des chiffres frais, sans persister
    (fonction de lecture pure ; l'état sera sauvé par le prochain vrai accès)."""
    V.tick(v, now)
    prod = V.net_production(v)
    wall = v.slots.get(V.WALL_SLOT)
    troops = [{"i": i, "name": UNITS[v.tribe][i].name, "n": c}
              for i, c in enumerate(v.troops) if c]
    building = None
    if v.queue:
        o = v.queue[0]
        building = {"slot": o.slot_index, "level": o.target_level,
                    "finish_in": round(o.finish_at - now)}
    return {
        "village_id": v.id, "name": v.name, "x": v.x, "y": v.y,
        "resources": [int(r) for r in v.resources],
        "production": [round(p) for p in prod],
        "wall_level": wall.level if wall else 0,
        "free_traps": V.free_traps(v),
        "loyalty": round(v.loyalty),
        "troops": troops,
        "building": building,
        "queue_busy": bool(v.queue),
    }


def build_digest(player_id: int, now: float | None = None,
                 report_cursor: float = 0.0) -> dict:
    """Digest compact du compte : villages (état défensif terse), menaces entrantes,
    rapports plus récents que `report_cursor`."""
    now = now or time.time()
    villages: list[dict] = []
    threats: list[dict] = []
    for vid in store.player_villages(player_id):
        v = store.load_village(vid)
        if v is None:
            continue
        line = _village_line(v, now)
        th = _threats_for(vid, now)
        if th:
            line["threats"] = th
            for t in th:
                threats.append({"village_id": vid, "name": v.name, **t})
        villages.append(line)
    new_reports = [{"created_at": r["created_at"], "title": r["title"]}
                   for r in store.reports_for(player_id)
                   if r["created_at"] > report_cursor]
    threats.sort(key=lambda t: t["arrive_in"])
    return {"player_id": player_id, "now": now, "villages": villages,
            "threats": threats, "new_reports": new_reports}


def latest_report_at(player_id: int) -> float:
    """Date du rapport le plus récent (curseur anti-doublon des réveils)."""
    reports = store.reports_for(player_id, limit=1)
    return reports[0]["created_at"] if reports else 0.0


def should_wake(digest: dict, *, plan_active: bool) -> str | None:
    """Faut-il réveiller le LLM ? Uniquement sur événement non couvert par le plan :
    menace entrante, nouveau rapport, ou plan épuisé/inactif (à (re)planifier)."""
    if digest["threats"]:
        n = len(digest["threats"])
        return f"{n} menace(s) entrante(s)"
    if digest["new_reports"]:
        return f"{len(digest['new_reports'])} nouveau(x) rapport(s)"
    if not plan_active:
        return "plan épuisé — replanifier"
    return None


def render_digest(digest: dict) -> str:
    """Rendu texte compact du digest, injecté dans le message user du LLM."""
    lines = [f"Compte joueur {digest['player_id']} — {len(digest['villages'])} village(s)."]
    if digest["threats"]:
        lines.append("⚔️ MENACES ENTRANTES (kind / dans X s / effectif — composition inconnue) :")
        for t in digest["threats"]:
            lines.append(f"  → {t['name']} ({t['village_id']}) : {t['kind']}, "
                         f"dans {t['arrive_in']}s, {t['n']} unités")
    else:
        lines.append("Aucune menace entrante.")
    if digest["new_reports"]:
        lines.append("📜 Nouveaux rapports :")
        for r in digest["new_reports"]:
            lines.append(f"  • {r['title']}")
    lines.append("Villages :")
    for v in digest["villages"]:
        res = "/".join(str(r) for r in v["resources"])
        prod = "/".join(str(p) for p in v["production"])
        troops = (", ".join(f"{t['name']}×{t['n']}" for t in v["troops"])
                  if v["troops"] else "aucune")
        busy = (f"construit slot {v['building']['slot']}→niv {v['building']['level']} "
                f"({v['building']['finish_in']}s)" if v["building"] else "file libre")
        lines.append(
            f"  [{v['village_id']}] {v['name']} ({v['x']},{v['y']}) — "
            f"res {res}, prod {prod}/h, mur niv {v['wall_level']}, "
            f"pièges libres {v['free_traps']}, loyauté {v['loyalty']}, {busy}. "
            f"Troupes : {troops}.")
    return "\n".join(lines)
