"""Verrouille les nouvelles mécaniques de bâtiments (cf. CLAUDE.md) :

- Académie : l'entraînement d'une unité non-basique exige sa recherche préalable ;
  après le temps de recherche, l'unité devient entraînable.
- Forge : l'amélioration d'une unité augmente bien sa puissance au combat (le
  niveau d'amélioration est transmis au moteur de combat par movement.py).
- Trappeur : la construction de pièges respecte la capacité et débite les ressources.

Approximations documentées (kirilloid ne modélise pas ces coûts) : coût de
recherche = coût d'entraînement ; coût d'amélioration = coût × niveau ; coût/temps
des pièges = constantes (cf. village.TRAP_COST / TRAP_TIME).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "buildings.db"

from app.engine import village as V
from app.engine import movement as M
from app.data.buildings import B
from app.data.tribes import Tribe


def _gaul(resources=200000.0, **slots):
    v = V.new_village("T", Tribe.GAULS, server_speed=100, player_id=1)
    v.slots[19].level = 12
    idx = 20
    for bid, lvl in slots.items():
        v.slots[idx] = V.Slot(building_id=getattr(B, bid), level=lvl); idx += 1
    v.resources = [resources] * 4
    return v


def test_research_gating():
    now = time.time()
    v = _gaul(STABLES=5, ACADEMY=5)
    # Le cavalier Theutates (index 3) nécessite une recherche.
    assert V.needs_research(v, 3) and not v.research[3]
    try:
        V.enqueue_training(v, B.STABLES, 3, 1, now)
        assert False, "entraînement sans recherche aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (non recherché) :", e)

    order = V.enqueue_research(v, 3, now)
    V.tick(v, order.finish_at + 1)
    assert v.research[3] == 1, "recherche non appliquée"
    # Désormais entraînable.
    V.enqueue_training(v, B.STABLES, 3, 1, order.finish_at + 1)
    print("✅ cavalier Theutates entraînable après recherche")

    # L'unité de base de la caserne (index 0) ne demande aucune recherche.
    v2 = _gaul(BARRACKS=3)
    assert not V.needs_research(v2, 0)
    V.enqueue_training(v2, B.BARRACKS, 0, 5, now)
    print("✅ phalange (index 0) entraînable sans recherche")


def _raid_losses(upgrade_level):
    """Razzia identique d'un village vers un autre ; renvoie (% pertes off, def)."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / f"smithy_{upgrade_level}.db"
    store.init_db()
    pid = store.create_player("A", Tribe.GAULS)
    pid2 = store.create_player("B", Tribe.GAULS)
    now = time.time()
    att = V.new_village("Att", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    att.troops[1] = 200                 # 200 épéistes (index 1, attaque 65)
    att.upgrades[1] = upgrade_level     # amélioration en forge
    att = store.insert_village(att)
    deff = V.new_village("Def", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=pid2)
    deff.troops[0] = 100                # 100 phalanges en défense
    deff = store.insert_village(deff)

    info = M.send(att.id, deff.id, pid, "raid", [0, 200] + [0] * 8, now)
    M.process_due(now + info["arrive_in"] + 1)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    return rep["body"]["pertes_pct"], rep["body"].get("survivantes")


def test_smithy_combat():
    base_loss, base_surv = _raid_losses(0)
    upg_loss, upg_surv = _raid_losses(10)
    print(f"pertes attaquant — sans forge : {base_loss}% | forge niv 10 : {upg_loss}%")
    print(f"survivants — sans forge : {sum(base_surv)} | forge niv 10 : {sum(upg_surv)}")
    assert upg_loss < base_loss, "l'amélioration en forge devrait réduire les pertes"
    assert sum(upg_surv) > sum(base_surv)
    print("✅ l'amélioration en forge renforce bien l'attaque au combat")


def test_trapper():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "trapper.db"
    now = time.time()
    v = _gaul(resources=10000.0, TRAPPER=2)
    assert V.trap_capacity(v) == V.F.trapper_traps(2)
    cap = V.trap_capacity(v)
    res0 = list(v.resources)
    order = V.enqueue_traps(v, 10, now)
    assert V.traps_total(v) == 10
    # Coût débité = 10 × TRAP_COST.
    for i in range(4):
        assert res0[i] - v.resources[i] == 10 * V.TRAP_COST[i]
    # Dépasser la capacité est refusé.
    try:
        V.enqueue_traps(v, cap, now)
        assert False, "dépassement de capacité aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (capacité) :", e)
    # Après le temps de construction, les pièges sont posés.
    V.tick(v, order.next_finish + 10 * order.per_unit + 1)
    assert v.traps == 10, v.traps
    print(f"✅ trappeur : {v.traps}/{cap} pièges construits, coût débité")


def test_traps_in_combat():
    """Les pièges capturent les assaillants AVANT la bataille : ils deviennent
    prisonniers (non tués), le surplus combat. Capture totale ⇒ pas de bataille."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / "traps_combat.db"
    store.init_db()
    pid = store.create_player("Att", Tribe.GAULS)
    did = store.create_player("Def", Tribe.GAULS)
    now = time.time()
    att = V.new_village("Att", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    att.troops[1] = 50                  # 50 épéistes
    att = store.insert_village(att)
    deff = V.new_village("Def", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=did)
    deff.troops[0] = 100                # 100 phalanges en défense
    deff.traps = 20                     # 20 pièges posés
    deff = store.insert_village(deff)

    info = M.send(att.id, deff.id, pid, "raid", [0, 50] + [0] * 8, now)
    M.process_due(now + info["arrive_in"] + 1)

    d2 = store.load_village(deff.id)
    assert V.prisoners_count(d2) == 20, V.prisoners_count(d2)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    assert sum(rep["body"]["captures"]) == 20, rep["body"]["captures"]
    # Seuls 30 épéistes (50−20 piégés) ont combattu : survivants ≤ 30.
    assert sum(rep["body"]["survivantes"]) <= 30
    print(f"✅ pièges : {sum(rep['body']['captures'])} capturés, le surplus combat")

    # Capture totale : assez de pièges pour tout l'assaillant ⇒ aucune bataille.
    deff2 = V.new_village("Def2", Tribe.GAULS, server_speed=100, x=2, y=0, player_id=did)
    deff2.troops[0] = 100
    deff2.traps = 100
    deff2 = store.insert_village(deff2)
    a2 = store.load_village(att.id); a2.troops[1] = 40; store.save_village(a2)
    info2 = M.send(att.id, deff2.id, pid, "attack", [0, 40] + [0] * 8, now)
    M.process_due(now + info2["arrive_in"] + 1)
    d3 = store.load_village(deff2.id)
    assert V.prisoners_count(d3) == 40, V.prisoners_count(d3)
    assert d3.troops[0] == 100, "défense intacte (pas de bataille livrée)"

    # Libération : le groupe quitte les pièges (réintégration côté API).
    grp = V.release_prisoner(d3, 0)
    assert sum(grp["units"]) == 40 and V.prisoners_count(d3) == 0
    print("✅ capture totale ⇒ pas de bataille ; libération vide les pièges")


def main():
    test_research_gating()
    test_smithy_combat()
    test_trapper()
    test_traps_in_combat()
    print("\n✅ Mécaniques de bâtiments (académie / forge / trappeur / pièges) validées")


if __name__ == "__main__":
    main()
