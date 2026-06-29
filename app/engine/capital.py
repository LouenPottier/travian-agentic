"""Changement de capitale — déclaré depuis le **palais**.

⚠️ **Source du comportement** : support.travian.com (« Capital Village ») —
recoupé, car kirilloid ne modélise pas la capitale :
- pour déclarer une capitale il faut un **palais** (niv ≥ 1) dans le village et
  cliquer « Make this village your capital » dans le menu du palais ;
- un compte n'a **qu'une** capitale à la fois ;
- la capitale est le **seul** village dont les champs de ressources dépassent le
  niveau 10 ; **quand on change de capitale, tous les champs > 10 de l'ancienne
  capitale sont ramenés au niveau 10** (« all resource fields above level 10 in the
  old capital are reduced back to level 10 »).

On ne **rembourse pas** les niveaux de champs perdus (fidèle : c'est une pénalité
assumée du changement de capitale).
"""
from __future__ import annotations

import time as _time

from app import store
from app.data import buildings as BLD
from app.data.buildings import B
from app.engine import village as V


class CapitalError(Exception):
    pass


def make_capital(player_id: int, village_id: int,
                 now: float | None = None) -> dict:
    """Déclare `village_id` comme nouvelle capitale du joueur.

    Exige un palais (niv ≥ 1) dans le village. L'ancienne capitale perd son statut
    et ses champs de ressources au-delà du niveau 10 sont ramenés à 10.
    Renvoie `{"reduced": [(slot_index, ancien_niveau), ...]}` pour le récap UI.
    """
    now = now or _time.time()
    target = store.load_village(village_id)
    if target is None:
        raise CapitalError("Village introuvable.")
    if target.player_id != player_id:
        raise CapitalError("Ce village ne t'appartient pas.")
    if target.is_capital:
        raise CapitalError("Ce village est déjà ta capitale.")
    if V.building_levels(target).get(B.PALACE, 0) < 1:
        raise CapitalError("Un palais (niveau 1+) est requis pour déclarer la capitale.")

    # Rétrograde l'ancienne capitale (et ramène ses champs > 10 à 10).
    reduced: list[tuple[int, int]] = []
    for vid in store.player_villages(player_id):
        if vid == village_id:
            continue
        v = store.load_village(vid)
        if v is None or not v.is_capital:
            continue
        V.tick(v, now)
        reduced = _cap_resource_fields(v)
        v.is_capital = False
        store.save_village(v)

    V.tick(target, now)
    target.is_capital = True
    store.save_village(target)
    return {"reduced": reduced}


def _cap_resource_fields(v: V.Village) -> list[tuple[int, int]]:
    """Ramène à 10 les champs de ressources (slot « res ») de `v` au-delà de 10.

    Annule au passage les ordres de construction de ces champs visant un niveau > 10
    (sans remboursement). Renvoie la liste (emplacement, niveau d'origine)."""
    reduced: list[tuple[int, int]] = []
    res_slots = {si for si, s in v.slots.items()
                 if BLD.get(s.building_id).slot == "res"}
    for si, s in v.slots.items():
        if si in res_slots and s.level > V.FIELD_CAP_NON_CAPITAL:
            reduced.append((si, s.level))
            s.level = V.FIELD_CAP_NON_CAPITAL
    # Files : on retire les montées de champs au-delà de 10 (plus de capitale).
    v.queue = [o for o in v.queue
               if not (o.slot_index in res_slots
                       and o.target_level > V.FIELD_CAP_NON_CAPITAL)]
    return reduced
