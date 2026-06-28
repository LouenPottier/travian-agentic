"""Restaure (ou sauvegarde) le village humain développé — utilitaire de test.

Le village de départ est volontairement vide ; entre deux sessions on aime
retrouver un village déjà développé (champs niveau ~5, entrepôt/grenier, troupes)
sans tout reconstruire à la main. L'instantané vit, **durable et versionné**, dans
`scripts/saves/mon_village.json` (pas dans `game.db`, qui est gitignoré et volatil).

  ./venv/bin/python -m scripts.restore_village            # restaure depuis l'instantané
  ./venv/bin/python -m scripts.restore_village --save     # écrase l'instantané avec l'état courant

La restauration remet `updated_at` à maintenant pour éviter un rattrapage de
production géant (la simulation paresseuse comblerait sinon des heures × vitesse).
Ne touche qu'au **premier village** du premier joueur non-NPC.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app import store

SNAPSHOT = Path(__file__).resolve().parent / "saves" / "mon_village.json"


def _human_village_id() -> int:
    npc = {p["id"] for p in _players() if p["is_npc"]}
    for meta in store.list_villages():
        if meta["player_id"] not in npc:
            return meta["id"]
    raise SystemExit("Aucun village humain en base : lance d'abord le serveur pour semer le monde.")


def _players() -> list[dict]:
    with store.connect() as c:
        return [dict(r) for r in c.execute("SELECT id, is_npc FROM players")]


def save() -> None:
    store.init_db()
    vid = _human_village_id()
    with store.connect() as c:
        r = c.execute("SELECT name, tribe, x, y, is_capital, data FROM villages WHERE id=?",
                      (vid,)).fetchone()
    snap = {"name": r["name"], "tribe": r["tribe"], "x": r["x"], "y": r["y"],
            "is_capital": r["is_capital"], "data": json.loads(r["data"])}
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    print(f"Instantané sauvegardé depuis le village {vid} → {SNAPSHOT.name}")


def restore() -> None:
    store.init_db()
    if not SNAPSHOT.exists():
        raise SystemExit(f"Instantané introuvable : {SNAPSHOT}")
    snap = json.loads(SNAPSHOT.read_text())
    data = snap["data"]
    data["updated_at"] = time.time()  # pas de rattrapage de production
    vid = _human_village_id()
    with store.connect() as c:
        c.execute("UPDATE villages SET name=?, tribe=?, is_capital=?, data=? WHERE id=?",
                  (snap["name"], snap["tribe"], snap["is_capital"], json.dumps(data), vid))
        c.commit()
    fields = sorted((l for i, (b, l) in data["slots"].items() if int(i) <= 18), reverse=True)
    print(f"Village {vid} restauré : ressources {[round(x) for x in data['resources']]}, "
          f"champs {fields}, troupes {data['troops']}")


if __name__ == "__main__":
    (save if "--save" in sys.argv[1:] else restore)()
