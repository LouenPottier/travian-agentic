"""Recoupe quelques valeurs T4.6 avec les calculateurs de travian.kirilloid.ru.

À lancer : ./venv/bin/python -m scripts.validate_data
Valeurs de référence connues (T4, vitesse 1) en commentaire.
"""
from app.data import buildings as BLD
from app.data.buildings import B
from app.data import formulas as F
from app.data.tribes import Tribe
from app.data.units import UNITS


def section(title):
    print(f"\n=== {title} ===")


section("Bûcheron (coût & temps de base, niv 1-5)")
wc = BLD.get(B.WOODCUTTER)
for lvl in range(1, 6):
    print(f"  niv {lvl}: coût {wc.cost_at(lvl)}  temps {wc.time_at(lvl):.0f}s")
# Réf Kirilloid : niv1 coût [40,100,50,60]

section("Production des champs en T4 (prod *1.4)")
print("  niveaux 0..10 :", [F.prod4(l) for l in range(11)])
# Réf : 3, 7, 13, 21, 31, 46, 70, 98, 140, 203, 280

section("Capacité entrepôt (niv 0,1,10,20)")
wh = BLD.get(B.WAREHOUSE)
for lvl in (0, 1, 10, 20):
    print(f"  niv {lvl}: {wh.benefit(lvl)}")
# Réf : 800, 1200, ... , 80000

section("Bâtiment principal — facteur de réduction de temps")
mb = BLD.get(B.MAIN_BUILDING)
for lvl in (1, 5, 10, 20):
    print(f"  niv {lvl}: x{mb.benefit(lvl):.4f}")

section("Cachette (capacité niv 1,10)")
cr = BLD.get(B.CRANNY)
print("  niv 1:", cr.benefit(1), " niv 10:", cr.benefit(10))

section("Coût total bâtiments chargés")
print(f"  {len(BLD.BUILDINGS)} bâtiments")

section("Unités romaines (nom / att / déf / coût / temps)")
for u in UNITS[Tribe.ROMANS]:
    print(f"  {u.name:<22} a={u.attack:<4} di={u.def_inf:<4} dc={u.def_cav:<4} "
          f"coût={u.cost} t={u.train_time}s")

print("\nOK — données chargées sans erreur.")
