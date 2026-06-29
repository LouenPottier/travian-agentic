"""Verrouille le câblage des bâtiments spéciaux jadis inertes (cf. CLAUDE.md #6) :

- **Place de tournoi** : +20 %/niveau de vitesse des troupes **au-delà des 20 premières
  cases** (les 20 premières restent à vitesse normale).
- **Tailleur de pierre** : +10 %/niveau de durabilité ⇒ les engins de siège (catapultes,
  béliers) détruisent moins (combat divise par la durabilité).
- **Grand entrepôt / grand grenier** : leur capacité s'**additionne** à l'entrepôt/grenier
  ordinaire (chacun = 3× la capacité normale).

Chiffres recoupés support.travian.com / unofficialtravian (kirilloid muet) ; cf.
commentaires dans buildings.py / movement.py / village.py.
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "special.db"

from app.engine import village as V
from app.engine import movement as M
from app.data import buildings as BLD
from app.data import formulas as F
from app.data.buildings import B
from app.data.tribes import Tribe


def test_tournament_square_speed():
    """Au-delà de 20 cases, la place de tournoi accélère le trajet ; en deçà, rien."""
    units = [10, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # phalanges gauloises
    speed = M.army_speed(Tribe.GAULS, units)
    # Trajet long (120 cases) : avec une place de tournoi niv 10 (×3 au-delà de 20).
    base = M.travel_seconds(0, 0, 120, 0, Tribe.GAULS, units, 1, arena=0)
    boosted = M.travel_seconds(0, 0, 120, 0, Tribe.GAULS, units, 1, arena=10)
    assert boosted < base, (base, boosted)
    # Formule attendue : 20 cases à vitesse normale + 100 cases ÷ (×3).
    mult = 1 + BLD.get(B.ARENA).benefit(10) / 100.0
    expected = (20 / speed + 100 / (speed * mult)) * 3600.0
    assert abs(boosted - expected) < 1e-6, (boosted, expected)
    # Trajet court (≤ 20 cases) : aucun effet (les 20 premières cases sont normales).
    short = M.travel_seconds(0, 0, 15, 0, Tribe.GAULS, units, 1, arena=10)
    assert short == M.travel_seconds(0, 0, 15, 0, Tribe.GAULS, units, 1, arena=0)
    print(f"✅ place de tournoi : 120 cases {base:.0f}s → {boosted:.0f}s (niv 10) ; "
          f"≤20 cases inchangé")


def test_great_warehouse_granary_capacity():
    """Grand entrepôt / grand grenier s'ajoutent au stockage ordinaire (3× chacun)."""
    v = V.new_village("T", Tribe.GAULS, server_speed=100, player_id=1)
    # Entrepôt/grenier ordinaires (niv 10) + leur version « grande » (niv 5).
    idx = max(v.slots) + 1
    v.slots[idx] = V.Slot(building_id=B.WAREHOUSE, level=10)
    v.slots[idx + 1] = V.Slot(building_id=B.GREAT_WAREHOUSE, level=5)
    v.slots[idx + 2] = V.Slot(building_id=B.GRANARY, level=10)
    v.slots[idx + 3] = V.Slot(building_id=B.GREAT_GRANARY, level=5)
    assert V.warehouse_capacity(v) == F.capacity(10) + F.great_capacity(5), V.warehouse_capacity(v)
    assert V.granary_capacity(v) == F.capacity(10) + F.great_capacity(5), V.granary_capacity(v)
    print(f"✅ grand entrepôt/grenier : +{F.great_capacity(5)} en plus de l'ordinaire "
          f"({F.capacity(10)})")


def test_stonemason_reduces_siege():
    """Le tailleur de pierre réduit la démolition par catapultes (durabilité ↑)."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / "stonemason.db"
    store.init_db()
    pid = store.create_player("Att", Tribe.ROMANS)
    did = store.create_player("Def", Tribe.GAULS)
    now = time.time()

    att = V.new_village("Att", Tribe.ROMANS, server_speed=100, x=0, y=0, player_id=pid)
    att = store.insert_village(att)

    def make_def(x, stonemason):
        d = V.new_village(f"Def{x}", Tribe.GAULS, server_speed=100, x=x, y=0, player_id=did)
        d.troops[0] = 5                                          # défense minime
        d.slots[20] = V.Slot(building_id=B.MARKETPLACE, level=20)  # cible niv 20
        if stonemason:
            d.slots[21] = V.Slot(building_id=B.STONEMASON, level=20)  # +200 % durabilité
        return store.insert_village(d)

    def attack(target):
        a = store.load_village(att.id)
        a.troops = [300, 0, 0, 0, 0, 0, 0, 30, 0, 0]            # légionnaires + 30 catas
        a.away = [0] * 10
        store.save_village(a)
        info = M.send(att.id, target.id, pid, "attack",
                      [300, 0, 0, 0, 0, 0, 0, 30, 0, 0], now, targets=[B.MARKETPLACE])
        M.process_due(now + info["arrive_in"] + 1)
        return store.load_village(target.id).slots[20].level

    plain = make_def(1, stonemason=False)
    fortified = make_def(2, stonemason=True)
    lvl_plain = attack(plain)
    lvl_fort = attack(fortified)
    assert lvl_fort > lvl_plain, ("le tailleur de pierre doit mieux protéger",
                                  lvl_plain, lvl_fort)
    print(f"✅ tailleur de pierre : marché 20 → {lvl_plain} (sans) vs {lvl_fort} (niv 20)")


def test_horse_pool_upkeep_and_training():
    """Abreuvoir romain : −1 céréale/cavalier aux paliers (Imperatoris niv 15) et
    −1 %/niveau sur le temps d'entraînement de la cavalerie."""
    now = time.time()
    v = V.new_village("Rome", Tribe.ROMANS, server_speed=1, player_id=1)
    v.troops[4] = 10                          # 10 Equites Imperatoris (entretien 3)
    assert V.unit_upkeep(v, 4) == 3
    base_upkeep = V.troop_upkeep(v)
    # Abreuvoir niv 15 ⇒ Imperatoris à 2 céréales (−1 par cavalier).
    v.slots[max(v.slots) + 1] = V.Slot(building_id=B.HORSE_POOL, level=15)
    assert V.unit_upkeep(v, 4) == 2
    assert V.troop_upkeep(v) == base_upkeep - 10, (base_upkeep, V.troop_upkeep(v))
    # Le palier Caesaris (niv 20) n'est pas atteint à niv 15.
    assert V.unit_upkeep(v, 5) == V.UNITS[v.tribe][5].upkeep

    # Temps d'entraînement de la cavalerie réduit de −1 %/niveau (abreuvoir niv 20).
    assert abs(V.horse_pool_train_factor(v) - (1 - 0.15)) < 1e-9
    v.slots[max(v.slots) + 1] = V.Slot(building_id=B.STABLES, level=1)
    v.resources = [1e9] * 4
    v.research[4] = 1                         # Imperatoris recherché
    for s in v.slots.values():                # monte l'abreuvoir à niv 20 (×0,80)
        if s.building_id == B.HORSE_POOL:
            s.level = 20
    order = V.enqueue_training(v, B.STABLES, 4, 1, now)
    expected = V.UNITS[v.tribe][4].train_time * 1.0 / v.server_speed * 0.80
    assert abs(order.per_unit - expected) < 1e-6, (order.per_unit, expected)
    print("✅ abreuvoir : entretien cavalerie −1/palier, entraînement −1 %/niveau")


def test_brewery_attack_bonus():
    """Brasserie teutonne : pendant la fête de la bière, +1 %/niveau d'attaque pour
    tout le compte ⇒ une attaque identique tue davantage de défenseurs."""
    from app.engine import brewery as BR
    store.DB_PATH = Path(tempfile.mkdtemp()) / "brewery.db"
    store.init_db()
    pid = store.create_player("Att", Tribe.TEUTONS)
    did = store.create_player("Def", Tribe.GAULS)
    now = time.time()

    att = V.new_village("Att", Tribe.TEUTONS, server_speed=100, x=0, y=0, player_id=pid)
    att.slots[20] = V.Slot(building_id=B.BREWERY, level=10)   # +10 % à pleine fête
    att.slots[21] = V.Slot(building_id=B.WAREHOUSE, level=15)  # stockage suffisant
    att.slots[22] = V.Slot(building_id=B.GRANARY, level=15)
    att.resources = [50000.0] * 4                             # de quoi payer la fête
    att = store.insert_village(att)

    def make_def(x):
        d = V.new_village(f"Def{x}", Tribe.GAULS, server_speed=100, x=x, y=0, player_id=did)
        d.troops[0] = 200                       # 200 phalanges en défense
        return store.insert_village(d)

    def attack(target):
        a = store.load_village(att.id)
        a.troops = [100, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # 100 combattants à la massue
        a.away = [0] * 10
        store.save_village(a)
        info = M.send(att.id, target.id, pid, "attack",
                      [100, 0, 0, 0, 0, 0, 0, 0, 0, 0], now)
        M.process_due(now + info["arrive_in"] + 1)
        return store.load_village(target.id).troops[0]    # phalanges survivantes

    # Sans fête : aucun bonus.
    assert BR.attack_bonus(pid, now) == 0.0
    survivors_plain = 200 - attack(make_def(1))

    # Avec fête de la bière active : +10 % d'attaque.
    res = BR.start_festival(att.id, pid, now)
    assert abs(res["bonus"] - 0.10) < 1e-9, res
    assert abs(BR.attack_bonus(pid, now) - 0.10) < 1e-9
    survivors_festival = 200 - attack(make_def(2))

    assert survivors_festival > survivors_plain, (survivors_plain, survivors_festival)
    print(f"✅ brasserie : {survivors_plain} tués sans fête → {survivors_festival} avec "
          f"fête (+10 % attaque)")
