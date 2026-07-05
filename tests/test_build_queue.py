"""Verrouille la **file de construction planifiée** (cf. CLAUDE.md, Phase 4).

Choix de dev (écart documenté avec le vrai Travian, qui limite la file et paie à la
mise en file) :
- file `Village.build_plan` **arbitrairement longue** ;
- les ordres se lancent **dans l'ordre** dès qu'un créneau (`max_queue`) est libre **et**
  que les ressources sont disponibles ;
- ressources **payées au démarrage** (promotion `PlannedBuild → BuildOrder`), pas à la
  mise en file ;
- ordre **annulable** tant qu'il n'a pas démarré (`cancel_plan`) ;
- promotion **paresseuse et indépendante du moment de lecture** (un gros tick == plein de
  petits ticks).
"""
import tempfile
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "buildqueue.db"

from app.engine import village as V
from app.data.buildings import B
from app.data.tribes import Tribe


def _village(server_speed: int = 1) -> V.Village:
    v = V.new_village("Q", Tribe.GAULS, server_speed=server_speed, x=0, y=0)
    # Gros stockage pour que le coût des montées reste finançable (pas plafonné).
    v.slots[20] = V.Slot(building_id=B.WAREHOUSE, level=20)
    v.slots[21] = V.Slot(building_id=B.GRANARY, level=20)
    return v


def test_overflow_waits_and_pays_on_start():
    """Deux ordres, un seul créneau : le 1ᵉʳ démarre (payé), le 2ᵉ attend (non payé)."""
    v = _village()
    t0 = v.updated_at
    cost1 = V.BLD.get(B.WOODCUTTER).cost_at(1)
    # Assez pour financer exactement UNE montée, pas deux.
    v.resources = [c * 1.5 for c in cost1]

    o1 = V.enqueue_build(v, 1, now=t0)   # démarre tout de suite
    o2 = V.enqueue_build(v, 2, now=t0)   # créneau plein ⇒ reste en file

    assert isinstance(o1, V.BuildOrder) and len(v.queue) == 1
    assert isinstance(o2, V.PlannedBuild) and len(v.build_plan) == 1
    # Seul le 1ᵉʳ a été débité (pay-on-start), le 2ᵉ n'a rien coûté.
    for i in range(4):
        assert abs(v.resources[i] - (cost1[i] * 1.5 - cost1[i])) < 1e-6
    print("✅ débordement : 1ᵉʳ démarré/payé, 2ᵉ en attente non payé")


def test_cancel_before_start():
    """Un ordre en attente est annulable ; les ressources restent intactes."""
    v = _village()
    t0 = v.updated_at
    cost1 = V.BLD.get(B.WOODCUTTER).cost_at(1)
    v.resources = [c * 1.5 for c in cost1]
    V.enqueue_build(v, 1, now=t0)                    # démarre
    V.enqueue_build(v, 2, now=t0)                    # en attente
    before = list(v.resources)
    p = V.cancel_plan(v, 0, now=t0)
    assert p.slot_index == 2 and not v.build_plan
    assert v.resources == before                     # jamais payé ⇒ rien remboursé/perdu
    # L'ordre déjà démarré n'est pas annulable (index hors file de planification).
    try:
        V.cancel_plan(v, 0, now=t0)
        assert False, "aucun ordre en attente à annuler"
    except V.BuildError:
        pass
    print("✅ annulation d'un ordre en attente (démarré = non annulable)")


def test_ladder_same_slot_projects_levels():
    """Enfiler plusieurs fois le même emplacement monte le niveau **projeté**."""
    v = _village()
    t0 = v.updated_at
    v.resources = [1e9] * 4
    V.enqueue_build(v, 1, now=t0)   # champ → 1 (démarre)
    V.enqueue_build(v, 1, now=t0)   # champ → 2 (en file)
    V.enqueue_build(v, 1, now=t0)   # champ → 3 (en file)
    assert V._projected_slot_level(v, 1) == 3
    assert [p.target_level for p in v.build_plan] == [2, 3]
    print("✅ laddering d'un même emplacement (niveaux projetés)")


def test_lazy_promotion_is_read_independent():
    """Un gros tick == plein de petits ticks : promotions financées au fil de la
    production, indépendamment du moment de lecture."""
    def build_scenario():
        v = _village(server_speed=100)
        # Bois/argile/fer à 0 (gating par accumulation), stock de blé confortable mais
        # **sous la capacité** (le village Gaulois neuf a une prod nette de blé négative ;
        # partir au-dessus du plafond rendrait l'écrêtage dépendant du découpage temporel).
        v.resources = [0.0, 0.0, 0.0, 70_000.0]
        t0 = v.updated_at
        for _ in range(5):                          # 5 montées du même champ
            V.enqueue_build(v, 1, now=t0)
        return v, t0

    # Production nette de bois positive ⇒ la file finit par lancer des montées.
    assert V.net_production(_village(server_speed=100))[0] > 0

    v1, t0 = build_scenario()
    end = t0 + 5000.0
    V.tick(v1, end)                                  # un seul grand pas

    v2, _ = build_scenario()
    step = (end - t0) / 137.0
    t = t0
    while t < end:                                   # beaucoup de petits pas
        t = min(end, t + step)
        V.tick(v2, t)

    assert v1.slots[1].level == v2.slots[1].level
    assert v1.slots[1].level >= 1                    # au moins une montée réalisée
    assert len(v1.build_plan) == len(v2.build_plan)
    assert len(v1.queue) == len(v2.queue)
    for i in range(4):
        # Tolérance : bruit d'arrondi de la sommation sur ~140 segments vs 1 seul.
        assert abs(v1.resources[i] - v2.resources[i]) < 1e-2
    print(f"✅ promotion paresseuse read-independent "
          f"(champ niv {v1.slots[1].level}, {len(v1.build_plan)} en attente)")


def test_no_infinite_loop_on_subresolution_promo_delay():
    """Anti-régression (bug de gel observé en prod) : quand `plan[0]` est finançable à un
    délai **sous la résolution du float** — `cursor` est un timestamp Unix (~1,8e9) donc
    `cursor + delay == cursor` pour un `delay` sub-microseconde — la boucle d'événements de
    `tick` ne devait jamais avancer ni promouvoir ⇒ **boucle infinie**. Ici on reconstitue
    ce cas (manque de ressources infime + forte production) et on exige que le tick termine
    (garde-fou `SIGALRM`) en promouvant l'ordre."""
    import signal, time
    v = _village(server_speed=100)
    for i in range(1, 19):                 # champs niveau 15 ⇒ forte production (rate élevé)
        v.slots[i].level = 15
    # Bâtiment bon marché (coût < capacité de stockage, sinon _affordable_delay renvoie None).
    p = V.PlannedBuild(slot_index=22, building_id=B.CRANNY, target_level=1)
    cost = V._plan_cost(p)
    # Manque **infime** (1,2e-6) : `_affordable_delay` renvoie un délai ~1e-8 s, perdu dans la
    # précision du timestamp Unix (cursor+delay==cursor) ⇒ gelait la boucle avant le correctif.
    v.resources = [c - 1.2e-6 for c in cost]
    v.build_plan = [p]
    v.updated_at = time.time()             # timestamp Unix réel (grand ⇒ ULP ~2,4e-7)
    assert v.updated_at + V._affordable_delay(v, cost) == v.updated_at   # condition du gel

    signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(AssertionError(
        "tick n'a pas terminé : régression de la boucle infinie (délai sub-résolution)")))
    signal.alarm(10)
    try:
        V.tick(v, v.updated_at + 3600)     # gros rattrapage
    finally:
        signal.alarm(0)

    # L'ordre a démarré et s'est terminé : file vidée, bâtiment posé au niveau 1.
    assert not v.build_plan, "plan[0] aurait dû être promu (fin de boucle)"
    assert v.slots[22].level == 1
    print("✅ pas de boucle infinie sur délai de promotion sous-résolution")


if __name__ == "__main__":
    test_overflow_waits_and_pays_on_start()
    test_cancel_before_start()
    test_ladder_same_slot_projects_levels()
    test_lazy_promotion_is_read_independent()
    test_no_infinite_loop_on_subresolution_promo_delay()
