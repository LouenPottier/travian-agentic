"""Verrouille l'expansion (cf. CLAUDE.md) :

- Points de culture : accumulés au fil du temps (production paresseuse, ×vitesse serveur).
- Emplacements d'expansion : résidence niv 10 → 1 slot.
- Fondation : 3 colons envoyés sur une vallée libre → nouveau village à l'arrivée,
  colons consommés. Refus si points/slots insuffisants ou case occupée/non-vallée.

Approximations documentées : seuils de points de culture (CULTURE_NEEDED).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "expansion.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import expansion as EXP
from app.data.buildings import B
from app.data.tribes import Tribe


def setup():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "expansion.db"
    store.init_db()
    store.insert_tiles([
        {"x": 0, "y": 0, "kind": "valley", "layout": "4-4-4-6", "animals": None},
        {"x": 3, "y": 0, "kind": "valley", "layout": "3-3-3-9", "animals": None},
        {"x": 1, "y": 0, "kind": "oasis", "layout": "wood25", "animals": [1] + [0]*9},
    ])
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Capitale", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    # Résidence niv 10 (1 slot d'expansion) + ressources.
    v.slots[20] = V.Slot(building_id=B.RESIDENCE, level=10)
    v.resources = [50000.0] * 4
    v = store.insert_village(v)
    return pid, v.id


def test_culture_accumulation():
    pid, vid = setup()
    now = time.time()
    EXP.accumulate_culture(pid, now)          # amorce l'horloge
    per_day = EXP.player_culture_per_day(pid)
    assert per_day > 0, "la capitale doit produire des points de culture"
    # Après 1 h réelle (×100 serveur ⇒ ~100 h de jeu).
    later = now + 3600
    culture = EXP.accumulate_culture(pid, later)
    expected = per_day * (later - now) / 86400.0 * 100
    assert abs(culture - expected) < 1e-3, (culture, expected)
    print(f"✅ culture : {per_day}/j → {round(culture)} pts après 1 h (×100)")


def test_settle_flow():
    pid, vid = setup()
    now = time.time()
    v = store.load_village(vid)
    # Donne assez de points de culture pour le village 2 (seuil 200).
    store.set_culture(pid, 500.0, now)
    st = EXP.expansion_status(pid, now)
    assert st["slots_free"] == 1 and st["can_settle"], st

    # Pas de colons → refus.
    try:
        EXP.send_settlers(vid, 3, 0, pid, now)
        assert False, "fonder sans colons aurait dû échouer"
    except EXP.ExpansionError as e:
        print("refus attendu (pas de colons) :", e)

    # Donne 3 colons et envoie.
    idx = EXP.settler_index(Tribe.GAULS)
    v = store.load_village(vid)
    v.troops[idx] = 3
    store.save_village(v)
    info = EXP.send_settlers(vid, 3, 0, pid, now)
    assert len(store.player_villages(pid)) == 1, "village pas encore fondé (en route)"

    # À l'arrivée, le village est fondé et les colons consommés.
    M.process_due(now + info["arrive_in"] + 1)
    vids = store.player_villages(pid)
    assert len(vids) == 2, vids
    new = store.load_village([x for x in vids if x != vid][0])
    assert (new.x, new.y) == (3, 0) and not new.is_capital
    origin = store.load_village(vid)
    assert origin.troops[idx] == 0 and origin.away[idx] == 0, "colons non consommés"
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "settle")
    assert rep["body"]["ok"]
    print(f"✅ village fondé en (3|0), 3 colons consommés ({new.name})")


def test_settle_refused_on_oasis():
    pid, vid = setup()
    now = time.time()
    store.set_culture(pid, 500.0, now)
    v = store.load_village(vid)
    v.troops[EXP.settler_index(Tribe.GAULS)] = 3
    store.save_village(v)
    try:
        EXP.send_settlers(vid, 1, 0, pid, now)  # (1|0) est une oasis
        assert False, "fonder sur une oasis aurait dû échouer"
    except EXP.ExpansionError as e:
        print("refus attendu (oasis) :", e)


def main():
    test_culture_accumulation()
    test_settle_flow()
    test_settle_refused_on_oasis()
    print("\n✅ Expansion (culture / colons / fondation) validée")


if __name__ == "__main__":
    main()
