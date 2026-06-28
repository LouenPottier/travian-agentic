"""Verrouille les célébrations de l'hôtel de ville (cf. CLAUDE.md, Phase 3 item #2).

- Coûts fixes + durée ÷ vitesse serveur (TravianZ cel.php).
- Points de culture = production/jour plafonnée (village ≤ 500 ; compte ≤ 2000, vrai T4),
  figés au lancement puis crédités **à la fin** (récolte paresseuse).
- Grande fête : hôtel de ville niv 10+ requis ; pendant qu'elle est active, chaque
  administrateur retire +5 points de loyauté (bonus de conquête).
- Une seule fête à la fois par village.
"""
import random
import tempfile
import time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "celebration.db"

from app.engine import village as V
from app.engine import expansion as EXP
from app.engine import celebration as CEL
from app.engine import conquest as CQ
from app.data.buildings import B
from app.data.tribes import Tribe


def setup(townhall_level=10, speed=100):
    store.DB_PATH = Path(tempfile.mkdtemp()) / "celebration.db"
    store.init_db()
    pid = store.create_player("Toi", Tribe.ROMANS)
    v = V.new_village("Capitale", Tribe.ROMANS, server_speed=speed, x=0, y=0, player_id=pid)
    v.slots[20] = V.Slot(building_id=B.TOWNHALL, level=townhall_level)
    # Entrepôt + grenier niv 20 : capacité suffisante pour payer une grande fête.
    v.slots[21] = V.Slot(building_id=B.WAREHOUSE, level=20)
    v.slots[22] = V.Slot(building_id=B.GRANARY, level=20)
    v.resources = [100000.0] * 4
    v = store.insert_village(v)
    return pid, v.id


def test_small_celebration_cost_duration_cp():
    pid, vid = setup(townhall_level=1, speed=100)
    now = time.time()
    v = store.load_village(vid)
    V.tick(v, now)                 # plafonnement à la capacité avant de mesurer le coût
    store.save_village(v)
    before = list(v.resources)
    info = CEL.start_celebration(vid, pid, CEL.SMALL, now)
    # Durée = table niv 1 (86400) ÷ vitesse serveur (100).
    assert info["ends_in"] == round(86400 / 100), info
    v = store.load_village(vid)
    cost = CEL.COST[CEL.SMALL]
    for i in range(4):
        assert abs((before[i] - v.resources[i]) - cost[i]) < 1e-6, (i, v.resources)
    # CP figés, plafonnés à 500, > 0 (le village produit de la culture).
    assert 0 < v.celebration["cp"] <= 500
    assert v.celebration["type"] == CEL.SMALL
    print(f"✅ petite fête : coût déduit, durée {info['ends_in']}s, {info['cp']} CP figés")


def test_great_requires_townhall_10():
    pid, vid = setup(townhall_level=5)
    now = time.time()
    try:
        CEL.start_celebration(vid, pid, CEL.GREAT, now)
        assert False, "grande fête sous hôtel de ville niv 10 aurait dû échouer"
    except CEL.CelebrationError as e:
        print("refus attendu (grande fête niv<10) :", e)


def test_busy_blocks_second():
    pid, vid = setup(townhall_level=10)
    now = time.time()
    CEL.start_celebration(vid, pid, CEL.SMALL, now)
    try:
        CEL.start_celebration(vid, pid, CEL.GREAT, now + 1)
        assert False, "deux fêtes simultanées auraient dû échouer"
    except CEL.CelebrationError as e:
        print("refus attendu (fête déjà en cours) :", e)


def test_harvest_credits_culture_at_end():
    pid, vid = setup(townhall_level=10, speed=100)
    now = time.time()
    EXP.accumulate_culture(pid, now)              # amorce l'horloge culture
    base, _ = store.get_culture(pid)
    info = CEL.start_celebration(vid, pid, CEL.GREAT, now)
    cp = info["cp"]
    assert 0 < cp <= 2000
    # Avant la fin : pas encore crédité, fête toujours active.
    mid = now + info["ends_in"] / 2
    assert CEL.is_active(store.load_village(vid), mid)
    # Après la fin : récolte → CP crédités, champ libéré.
    end = now + info["ends_in"] + 1
    total = EXP.accumulate_culture(pid, end)
    v = store.load_village(vid)
    assert v.celebration is None, "la fête terminée doit être récoltée"
    # Le total inclut le lump de la fête (+ l'accumulation horaire normale).
    assert total >= base + cp - 1, (total, base, cp)
    assert not CEL.is_active(v, end)
    print(f"✅ grande fête récoltée : +{cp} CP crédités au compte à la fin")


def test_great_celebration_loyalty_bonus():
    # Le bonus de grande fête ajoute +5 points de loyauté retirés par administrateur.
    n = 2
    base = CQ.loyalty_drop(Tribe.ROMANS, n, great_celebration=False,
                           rng=random.Random(42))
    boosted = CQ.loyalty_drop(Tribe.ROMANS, n, great_celebration=True,
                              rng=random.Random(42))
    assert boosted == base + CQ.GREAT_CELEBRATION_BONUS * n, (base, boosted)
    print(f"✅ bonus grande fête : {base} → {boosted} (+5/admin × {n})")


def test_great_active_flag():
    pid, vid = setup(townhall_level=10, speed=100)
    now = time.time()
    info = CEL.start_celebration(vid, pid, CEL.GREAT, now)
    v = store.load_village(vid)
    assert CEL.great_celebration_active(v, now + 1)
    assert not CEL.great_celebration_active(v, now + info["ends_in"] + 1)
    # Une petite fête n'active pas le bonus de conquête.
    pid2, vid2 = setup(townhall_level=10, speed=100)
    CEL.start_celebration(vid2, pid2, CEL.SMALL, now)
    assert not CEL.great_celebration_active(store.load_village(vid2), now + 1)
    print("✅ great_celebration_active : vrai pendant la grande fête uniquement")


def main():
    test_small_celebration_cost_duration_cp()
    test_great_requires_townhall_10()
    test_busy_blocks_second()
    test_harvest_credits_culture_at_end()
    test_great_celebration_loyalty_bonus()
    test_great_active_flag()
    print("\n✅ Célébrations (hôtel de ville) validées")


if __name__ == "__main__":
    main()
