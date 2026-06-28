"""Validation du moteur de combat contre les vecteurs de Kirilloid (t4/combat.spec.ts)."""
from app.engine import combat as C
from app.data.units import UNITS
from app.data.tribes import Tribe


def n(*pairs):
    """Construit un tableau de 10 effectifs : n((index, nombre), ...)."""
    arr = [0] * 10
    for i, v in pairs:
        arr[i] = v
    return arr


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol * max(1, abs(b))


def test_upgrade_t4():
    leg = UNITS[Tribe.ROMANS][0]  # u = 1
    assert approx(C.upgrade(40, 0, leg.upkeep), 40.0021, 1e-6), C.upgrade(40, 0, leg.upkeep)
    assert approx(C.upgrade(40, 20, leg.upkeep), 52.3623, 1e-3), C.upgrade(40, 20, leg.upkeep)
    print("✓ upgrade T4 :", C.upgrade(40, 0, 1), C.upgrade(40, 20, 1))


def test_e2e_minor_change():
    place = C.Place()  # défauts : pop 100, mur 0, def 0
    deff = C.Defender(units=UNITS[Tribe.TEUTONS], numbers=n((0, 999999)))
    off = C.Off(units=UNITS[Tribe.ROMANS], numbers=n((0, 499999)), pop=100)
    r = C.combat(place, off, [deff])
    assert r.off_losses == 1, r.off_losses
    dead = round(r.def_losses * 999999)
    assert dead == 999931, dead
    print("✓ e2e : off_losses =", r.off_losses, "| def morts =", dead)


def test_catapults_threshold():
    place = C.Place(pop=1000)
    deff = C.Defender(units=UNITS[Tribe.ROMANS], numbers=n((1, 999999)))
    off1 = C.Off(units=UNITS[Tribe.GAULS], numbers=n((1, 670175), (7, 20)),
                 pop=2000, targets=[5])
    off2 = C.Off(units=UNITS[Tribe.GAULS], numbers=n((1, 670174), (7, 20)),
                 pop=2000, targets=[5])
    b1 = C.combat(place, off1, [C.Defender(units=UNITS[Tribe.ROMANS], numbers=n((1, 999999)))]).buildings[0]
    b2 = C.combat(place, off2, [C.Defender(units=UNITS[Tribe.ROMANS], numbers=n((1, 999999)))]).buildings[0]
    assert b1 == 0, b1
    assert b2 == 1, b2
    print("✓ catapultes : 670175 →", b1, "| 670174 →", b2)


if __name__ == "__main__":
    test_upgrade_t4()
    test_e2e_minor_change()
    test_catapults_threshold()
    print("\nTous les vecteurs de combat Kirilloid sont reproduits ✅")
