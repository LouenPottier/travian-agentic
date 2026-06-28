"""Test de bout en bout du commerce (place de marché) :
envoi de ressources → marchands mobilisés → livraison plafonnée → retour des marchands.

Verrouille la mécanique (cf. CLAUDE.md) : nombre de marchands = niveau du marché,
marchands requis = plafond(total / capacité), surplus perdu au-delà du stockage cible,
marchands rendus disponibles seulement après leur retour à vide.
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "trade.db"

from app.engine import village as V
from app.engine import movement as M
from app.data.buildings import B
from app.data.tribes import Tribe, MERCHANT_CAPACITY


def setup():
    store.init_db()
    a = store.create_player("A", Tribe.GAULS)
    o = V.new_village("Origine", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=a)
    o.slots[20] = V.Slot(building_id=B.MARKETPLACE, level=3)   # 3 marchands
    o.slots[21] = V.Slot(building_id=B.WAREHOUSE, level=10)    # cap 11800 > 5000
    o.slots[22] = V.Slot(building_id=B.GRANARY, level=10)
    o.resources = [5000.0] * 4
    o = store.insert_village(o)
    t = V.new_village("Cible", Tribe.GAULS, server_speed=100, x=3, y=4, player_id=a)
    t.resources = [100.0] * 4                                  # stockage de base = 800
    t = store.insert_village(t)
    return a, o.id, t.id


def main():
    pa, oid, tid = setup()
    now = time.time()
    cap = MERCHANT_CAPACITY[Tribe.GAULS]          # 750
    assert cap == 750

    o = store.load_village(oid)
    assert M.merchants_total(o) == 3, M.merchants_total(o)
    assert M.merchant_capacity(o) == 750          # pas de comptoir → base, pas ×vitesse
    assert M.merchants_available(o) == 3

    # Envoi : 1000 bois + 500 argile = 1500 → plafond(1500/750) = 2 marchands.
    info = M.send_resources(oid, tid, pa, [1000, 500, 0, 0], now)
    print("envoi : arrive dans", info["arrive_in"], "s | marchands =", info["merchants"])
    assert info["merchants"] == 2, info["merchants"]

    o = store.load_village(oid)
    print("origine après envoi : ressources =", [round(r) for r in o.resources],
          "| marchands libres =", M.merchants_available(o))
    assert round(o.resources[0]) == 4000 and round(o.resources[1]) == 4500  # débitées tout de suite
    assert M.merchants_available(o) == 1                         # 2 partis sur 3

    # Un 2ᵉ envoi de 2 marchands doit échouer (il n'en reste qu'1 libre).
    try:
        M.send_resources(oid, tid, pa, [1000, 0, 0, 0], now)
        assert False, "aurait dû manquer de marchands"
    except M.MoveError as e:
        print("refus attendu :", e)

    # Arrivée → livraison plafonnée par le stockage de la cible (800).
    M.process_due(now + info["arrive_in"] + 1)
    t = store.load_village(tid)
    print("cible après livraison : ressources =", [round(r) for r in t.resources])
    # Tolérance : la cible produit aussi un peu pendant le trajet.
    assert t.resources[0] == 800, t.resources[0]                 # bois plafonné pile au stockage
    assert 600 <= t.resources[1] < 800, t.resources[1]           # argile livrée, sous le plafond

    reps = store.reports_for(pa)
    rep = next(r for r in reps if r["body"].get("type") == "trade")
    print("rapport :", rep["title"], "| perdu =", rep["body"]["perdu"])
    assert 290 <= rep["body"]["perdu"][0] <= 320                 # ~1100 reçus - 800 plafond
    assert rep["body"]["perdu"][1] == 0                          # argile : rien de perdu

    # Les marchands sont en retour (toujours indisponibles).
    o = store.load_village(oid)
    assert M.merchants_available(o) == 1, M.merchants_available(o)

    # Retour à vide → marchands de nouveau libres.
    back = [m for m in store.movements_for(oid) if m["phase"] == "back"][0]
    M.process_due(back["arrive_at"] + 1)
    o = store.load_village(oid)
    print("après retour : marchands libres =", M.merchants_available(o))
    assert M.merchants_available(o) == 3, M.merchants_available(o)

    print("\n✅ Commerce complet (envoi → marchands → livraison plafonnée → retour) validé")


if __name__ == "__main__":
    main()
