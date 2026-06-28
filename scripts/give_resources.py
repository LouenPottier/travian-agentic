"""Donne des ressources à tes villages (humains) — utilitaire de test.

À lancer : ./venv/bin/python -m scripts.give_resources           (50000 par ressource)
           ./venv/bin/python -m scripts.give_resources 5000       (5000 par ressource)

Les ressources sont plafonnées par l'entrepôt (bois/argile/fer) et le grenier (blé).
Le script monte donc d'abord un entrepôt et un grenier au niveau nécessaire pour
contenir le montant demandé (max 80000), puis remplit. Ne touche qu'aux villages
des joueurs non-NPC.
"""
from __future__ import annotations

import sys
import time

from app import store
from app.engine import village as V
from app.data.buildings import B, get as get_building

DEFAULT_AMOUNT = 50_000


def main(amount: int = DEFAULT_AMOUNT) -> None:
    store.init_db()
    npc = set(_npc_player_ids())
    touched = 0
    for meta in store.list_villages():
        if meta["player_id"] in npc:
            continue
        v = store.load_village(meta["id"])
        V.tick(v, time.time())  # applique production/événements avant d'écraser
        _ensure_storage(v, B.WAREHOUSE, amount)
        _ensure_storage(v, B.GRANARY, amount)
        caps = V.capacities(v)
        for i in range(4):
            v.resources[i] = float(min(amount, caps[i]))
        store.save_village(v)
        touched += 1
        print(f"  {v.name} ({v.x}|{v.y}) → {[round(r) for r in v.resources]} "
              f"(stockage {caps})")
    print(f"OK — {touched} village(s) approvisionné(s) à {amount}/ressource.")


def _ensure_storage(v: V.Village, building_id: int, amount: int) -> None:
    """Garantit un entrepôt/grenier au niveau requis pour contenir `amount`."""
    b = get_building(building_id)
    level = next((l for l in range(1, b.max_level + 1) if b.benefit(l) >= amount),
                 b.max_level)
    # Slot existant de ce type avec la plus grande capacité, sinon un slot vide.
    slot = next((s for s in v.slots.values() if s.building_id == building_id), None)
    if slot is None:
        idx = next(i for i in V.CENTER_SLOTS if i not in v.slots)
        slot = V.Slot(building_id=building_id, level=0)
        v.slots[idx] = slot
    slot.level = max(slot.level, level)


def _npc_player_ids() -> list[int]:
    with store.connect() as c:
        rows = c.execute("SELECT id FROM players WHERE is_npc=1").fetchall()
    return [r["id"] for r in rows]


if __name__ == "__main__":
    arg = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_AMOUNT
    main(arg)
