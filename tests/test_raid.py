"""Test de bout en bout d'un raid : envoi → trajet → combat → butin → retour."""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "raid.db"

from app.engine import village as V
from app.engine import movement as M
from app.data.tribes import Tribe


def setup():
    store.init_db()
    a = store.create_player("A", Tribe.GAULS)
    b = store.create_player("B", Tribe.TEUTONS, is_npc=True)
    o = V.new_village("Origine", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=a)
    o.troops[0] = 50           # 50 phalanges
    o.resources = [200.0] * 4
    o = store.insert_village(o)
    t = V.new_village("Cible", Tribe.TEUTONS, server_speed=100, x=3, y=1, player_id=b)
    t.troops[0] = 10           # 10 combattants à la massue
    t.resources = [1000.0] * 4
    t = store.insert_village(t)
    return a, o.id, t.id


def main():
    pa, oid, tid = setup()
    now = time.time()
    units = [50] + [0] * 9
    info = M.send(oid, tid, pa, "raid", units, now)
    print("envoi : arrive dans", info["arrive_in"], "s")

    o = store.load_village(oid)
    print("origine après envoi : troupes phalanges =", o.troops[0], "(parties)")
    assert o.troops[0] == 0

    # Arrivée → combat
    M.process_due(now + info["arrive_in"] + 1)
    t = store.load_village(tid)
    print("cible après raid : défenseurs =", t.troops[0], "| ressources =",
          [round(r) for r in t.resources])

    reps = store.reports_for(pa)
    print("rapport attaquant :", reps[0]["title"], "| butin =", reps[0]["body"]["butin"],
          "| survivants =", reps[0]["body"]["survivantes"][0])
    loot = reps[0]["body"]["butin"]
    assert sum(loot) > 0, "le raid aurait dû rapporter du butin"

    # Retour des survivants + butin
    mv = store.movements_for(oid)
    back = [m for m in mv if m["phase"] == "back"][0]
    M.process_due(back["arrive_at"] + 1)
    o = store.load_village(oid)
    print("origine au retour : phalanges =", o.troops[0], "| ressources =",
          [round(r) for r in o.resources])
    assert o.troops[0] > 0, "les survivants auraient dû rentrer"
    print("\n✅ Raid complet (envoi → combat → butin → retour) validé")


if __name__ == "__main__":
    main()
