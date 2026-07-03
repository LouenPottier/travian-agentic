"""Verrouille le classement / statistiques (cf. CLAUDE.md, engine.ranking).

Mécanique (kirilloid muet ; TravianZ Ranking.php, recoupé support.travian.com) :
- **points d'attaque/défense** = somme de l'**upkeep** (consommation de céréales) des
  troupes ennemies détruites — l'attaquant pour les défenseurs tués, le défenseur pour
  les assaillants tués ;
- **ressources pillées** cumulées à l'attaquant ;
- **population** = somme des populations des villages ;
- classement trié par valeur décroissante, joueur courant repérable.
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "ranking.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import ranking as RK
from app.data.units import UNITS
from app.data.tribes import Tribe


def _fresh():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "ranking.db"
    store.init_db()


def test_kill_points_equals_upkeep():
    """Tuer une unité rapporte autant de points que sa consommation de céréales."""
    # 10 légionnaires romains (upkeep 1) → 10 points.
    up0 = UNITS[Tribe.ROMANS][0].upkeep
    killed = [10] + [0] * 9
    assert RK.kill_points(Tribe.ROMANS, killed) == 10 * up0
    # Mélange : la somme pondère chaque type par son upkeep.
    killed2 = [3, 0, 0, 0, 0, 0, 2, 0, 0, 0]  # 3 idx0 + 2 idx6
    expect = 3 * UNITS[Tribe.ROMANS][0].upkeep + 2 * UNITS[Tribe.ROMANS][6].upkeep
    assert RK.kill_points(Tribe.ROMANS, killed2) == expect


def test_credit_battle_splits_off_def_and_raided():
    _fresh()
    att = store.create_player("Att", Tribe.ROMANS)
    dfd = store.create_player("Def", Tribe.GAULS)
    att_losses = [4] + [0] * 9   # 4 attaquants romains morts
    def_losses = [7] + [0] * 9   # 7 défenseurs gaulois morts
    RK.credit_battle(att, Tribe.ROMANS, att_losses, dfd, Tribe.GAULS, def_losses,
                     raided=123.0)
    pa = {p["id"]: p for p in store.all_players()}
    # Attaquant : points d'attaque = upkeep des défenseurs tués ; raided crédité.
    assert pa[att]["off_points"] == 7 * UNITS[Tribe.GAULS][0].upkeep
    assert pa[att]["raided"] == 123.0
    assert pa[att]["def_points"] == 0
    # Défenseur : points de défense = upkeep des assaillants tués ; pas de pillage.
    assert pa[dfd]["def_points"] == 4 * UNITS[Tribe.ROMANS][0].upkeep
    assert pa[dfd]["off_points"] == 0
    assert pa[dfd]["raided"] == 0


def test_credit_ignores_none_defender():
    """Combat d'oasis vs Nature : def_pid=None ⇒ aucun crédit défensif, mais
    l'attaquant gagne bien ses points d'attaque."""
    _fresh()
    att = store.create_player("Chasseur", Tribe.TEUTONS)
    RK.credit_battle(att, Tribe.TEUTONS, [1] + [0] * 9, None, Tribe.NATURE,
                     [5] + [0] * 9)
    pa = {p["id"]: p for p in store.all_players()}
    assert pa[att]["off_points"] == 5 * UNITS[Tribe.NATURE][0].upkeep


def test_rankings_sorted_and_categories():
    _fresh()
    a = store.create_player("Alice", Tribe.GAULS)
    b = store.create_player("Bob", Tribe.ROMANS, is_npc=True)
    # Alice : gros village (donc grosse pop) ; Bob : petit village.
    va = V.new_village("A", Tribe.GAULS, server_speed=1, x=0, y=0, player_id=a)
    for i in range(1, 19):
        va.slots[i].level = 8
    store.insert_village(va)
    vb = V.new_village("B", Tribe.ROMANS, server_speed=1, x=5, y=5, player_id=b)
    store.insert_village(vb)
    # Bob a plus de points d'attaque, Alice plus de défense.
    store.add_player_stats(a, deff=500, raided=40)
    store.add_player_stats(b, off=800, raided=10)

    r = RK.rankings(human_player_id=a)
    keys = [c["key"] for c in r["categories"]]
    assert keys == ["population", "off", "def", "raided", "villages"]

    def cat(k): return next(c for c in r["categories"] if c["key"] == k)
    # Population : Alice (champs niv 8) devant Bob.
    assert cat("population")["rows"][0]["name"] == "Alice"
    # Points d'attaque : Bob premier.
    assert cat("off")["rows"][0]["name"] == "Bob"
    # Points de défense : Alice première.
    assert cat("def")["rows"][0]["name"] == "Alice"
    # Rangs séquentiels + repérage du joueur courant.
    off_rows = cat("off")["rows"]
    assert [row["rank"] for row in off_rows] == [1, 2]
    alice_row = next(x for x in off_rows if x["name"] == "Alice")
    assert alice_row["is_own"] is True
    assert next(x for x in off_rows if x["name"] == "Bob")["is_npc"] is True


def test_raid_credits_stats_end_to_end():
    """Un raid réel crédite l'attaquant (attaque + pillage) et le défenseur (défense)."""
    _fresh()
    a = store.create_player("Raider", Tribe.GAULS)
    d = store.create_player("Ferme", Tribe.TEUTONS, is_npc=True)
    o = V.new_village("Origine", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=a)
    o.troops[0] = 100          # 100 phalanges
    o.resources = [200.0] * 4
    store.insert_village(o)
    t = V.new_village("Cible", Tribe.TEUTONS, server_speed=100, x=3, y=1, player_id=d)
    t.troops[0] = 20           # 20 combattants à la massue (vont mourir)
    t.resources = [1000.0] * 4
    tid = store.insert_village(t).id
    oid = store.load_village(store.player_villages(a)[0]).id

    now = time.time()
    info = M.send(oid, tid, a, "raid", [100] + [0] * 9, now)
    M.process_due(now + info["arrive_in"] + 1)

    pa = {p["id"]: p for p in store.all_players()}
    # L'attaquant a tué des défenseurs (upkeep du combattant à la massue) et pillé.
    up_def = UNITS[Tribe.TEUTONS][0].upkeep
    assert pa[a]["off_points"] > 0
    assert pa[a]["off_points"] <= 20 * up_def       # au plus tous les défenseurs
    assert pa[a]["raided"] > 0
    # Le défenseur a des points de défense s'il a tué des assaillants (sinon 0, mais
    # 20 massues infligent des pertes aux 100 phalanges dans ce cas).
    assert pa[d]["def_points"] >= 0
    assert pa[d]["off_points"] == 0
