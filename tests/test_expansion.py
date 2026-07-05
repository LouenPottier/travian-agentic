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


def test_pending_settlement_reserves_slot():
    """Un train de colons en vol réserve l'emplacement d'expansion ET le palier
    de culture : on ne doit pas pouvoir dépasser son quota en lançant plusieurs
    colons en parallèle (fidélité Travian : slot/culture pas consommés qu'à
    l'arrivée, mais réservés dès le départ)."""
    pid, vid = setup()
    now = time.time()
    store.insert_tiles([
        {"x": 0, "y": 3, "kind": "valley", "layout": "4-4-4-6", "animals": None},
    ])
    # 1 seul slot (résidence niv 10) ; assez de culture pour le village 2 (seuil 200).
    store.set_culture(pid, 300.0, now)
    idx = EXP.settler_index(Tribe.GAULS)
    v = store.load_village(vid)
    v.troops[idx] = 6                            # de quoi tenter deux fondations
    store.save_village(v)

    # 1ʳᵉ vague : OK, part vers (3|0).
    EXP.send_settlers(vid, 3, 0, pid, now)
    st = EXP.expansion_status(pid, now)
    assert st["pending_settlements"] == 1, st
    assert st["slots_free"] == 0 and not st["can_settle"], st

    # 2ᵉ vague avec le slot déjà réservé → refus (pas d'over-fondation).
    try:
        EXP.send_settlers(vid, 0, 3, pid, now)
        assert False, "2ᵉ fondation aurait dû échouer (slot réservé en vol)"
    except EXP.ExpansionError as e:
        print("refus attendu (slot réservé par colons en vol) :", e)
    print("✅ fondation en vol réserve bien le slot/la culture")


def test_failed_settlement_frees_slot():
    """Une fondation qui échoue (case prise/non-vallée) libère le slot réservé :
    les colons reviennent (phase « back »), donc ne comptent plus comme pending."""
    pid, vid = setup()
    now = time.time()
    store.set_culture(pid, 300.0, now)
    idx = EXP.settler_index(Tribe.GAULS)
    v = store.load_village(vid)
    v.troops[idx] = 3
    store.save_village(v)
    info = EXP.send_settlers(vid, 3, 0, pid, now)
    assert EXP.expansion_status(pid, now)["pending_settlements"] == 1

    # On occupe (3|0) avant l'arrivée des colons (autre joueur fonde / village existe).
    other = V.new_village("Squat", Tribe.GAULS, server_speed=100, x=3, y=0,
                          player_id=store.create_player("Rival", Tribe.GAULS))
    store.insert_village(other)
    M.process_due(now + info["arrive_in"] + 1)

    # Colons en retour → plus de pending, slot de nouveau libre.
    st = EXP.expansion_status(pid, now + info["arrive_in"] + 1)
    assert st["pending_settlements"] == 0, st
    assert st["slots_free"] == 1, st
    rep = next(r for r in store.reports_for(pid)
               if r["body"].get("type") == "settle" and not r["body"]["ok"])
    assert rep["body"]["coords"] == [3, 0]
    print("✅ fondation échouée → slot libéré, colons en retour")


def test_settler_training_needs_slot():
    """Vrai Travian : un colon occupe un emplacement d'expansion **dès l'entraînement**
    (support.travian.com / wiki « Expansion slots »). Résidence niv 10 = 1 slot = 3
    colons formables ; au-delà (ou avec un emplacement déjà consommé), l'entraînement
    est refusé — c'était le bug : on pouvait former des colons sans emplacement libre."""
    pid, vid = setup()
    now = time.time()
    v = store.load_village(vid)
    sidx = EXP.settler_index(Tribe.GAULS)
    # 1 slot (résidence niv 10), aucun autre village → 3 colons formables.
    assert EXP.settler_training_allowance(pid, current=v) == 3
    # 2 colons déjà debout → il en reste 1 formable.
    v.troops[sidx] = 2
    store.save_village(v); v = store.load_village(vid)
    assert EXP.settler_training_allowance(pid, current=v) == 1
    # 3 colons → emplacement plein (le slot est occupé dès l'entraînement).
    v.troops[sidx] = 3
    store.save_village(v); v = store.load_village(vid)
    assert EXP.settler_training_allowance(pid, current=v) == 0
    # En former un de plus → refus (le garde-fou précède le débit du coût).
    try:
        V.enqueue_training(v, B.RESIDENCE, sidx, 1, now)
        assert False, "former un colon sans emplacement libre aurait dû échouer"
    except V.BuildError as e:
        assert "emplacement" in str(e).lower(), e
        print("✅ refus attendu (emplacement d'expansion plein) :", e)
    # Les colons en **file d'entraînement** comptent aussi (pas seulement debout).
    v.troops[sidx] = 0
    v.training.append(V.TrainOrder(building_id=B.RESIDENCE, unit_index=sidx,
                                   remaining=3, per_unit=1.0, next_finish=now + 1))
    store.save_village(v); v = store.load_village(vid)
    assert EXP.settler_training_allowance(pid, current=v) == 0
    print("✅ colons en file comptent dans l'occupation des emplacements")


def test_chief_training_needs_slot():
    """Vrai Travian (support.travian.com « Expansion Slots ») : un administrateur occupe
    un emplacement d'expansion dès l'entraînement — **1 emplacement = 3 colons OU 1
    administrateur** (vivier partagé). Et la **résidence** (pas seulement le palais) forme
    les administrateurs (correctif de fidélité T4.6). Résidence niv 10 = 1 emplacement."""
    pid, vid = setup()
    now = time.time()
    v = store.load_village(vid)
    cidx = next(i for i, u in enumerate(V.UNITS[Tribe.GAULS]) if u.is_chief)
    v.research[cidx] = 1                      # chef recherché (académie 20, cf. test dédié)
    # Entrepôt/grenier hauts : sinon le stockage plafonne les ressources sous le coût du chef.
    v.slots[21] = V.Slot(building_id=B.WAREHOUSE, level=20)
    v.slots[22] = V.Slot(building_id=B.GRANARY, level=20)
    v.resources = [80000.0] * 4
    store.save_village(v); v = store.load_village(vid)

    # La résidence forme bien l'administrateur (fidélité T4.6, pas seulement le palais).
    assert cidx in {i for i, _ in V.trainable_units(v, B.RESIDENCE)}
    # 1 emplacement libre → 1 administrateur OU 3 colons formables.
    assert EXP.chief_training_allowance(pid, current=v) == 1
    assert EXP.settler_training_allowance(pid, current=v) == 3

    # Former le chef → l'emplacement est occupé : plus ni chef ni colon (vivier partagé).
    V.enqueue_training(v, B.RESIDENCE, cidx, 1, now)
    store.save_village(v); v = store.load_village(vid)
    assert EXP.chief_training_allowance(pid, current=v) == 0
    assert EXP.settler_training_allowance(pid, current=v) == 0

    # En former un 2ᵉ → refus (garde-fou avant le débit du coût).
    try:
        V.enqueue_training(v, B.RESIDENCE, cidx, 1, now)
        assert False, "2ᵉ administrateur sans emplacement libre aurait dû échouer"
    except V.BuildError as e:
        assert "emplacement" in str(e).lower(), e
        print("✅ refus attendu (emplacement d'expansion occupé par l'administrateur) :", e)
    print("✅ chef : résidence 10 = 1 emplacement = 1 administrateur (partagé avec les colons)")


def main():
    test_culture_accumulation()
    test_settle_flow()
    test_settle_refused_on_oasis()
    test_pending_settlement_reserves_slot()
    test_failed_settlement_frees_slot()
    test_settler_training_needs_slot()
    test_chief_training_needs_slot()
    print("\n✅ Expansion (culture / colons / fondation) validée")


if __name__ == "__main__":
    main()
