"""Persistance SQLite — joueurs et villages.

Approche pragmatique pour un jeu local : chaque village est stocké comme une ligne
avec ses données dynamiques (ressources, slots, file, troupes) en JSON. Le moteur
continue d'opérer sur les dataclasses `Village` ; on (dé)sérialise au chargement et
à la sauvegarde. Suffisant pour le multijoueur local ; migrable vers un schéma
relationnel complet plus tard si besoin.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.data.tribes import Tribe
from app.engine.village import (Village, Slot, BuildOrder, TrainOrder,
                                ResearchOrder, UpgradeOrder, TrapOrder)

DB_PATH = Path(__file__).resolve().parent.parent / "game.db"


# --- (dé)sérialisation d'un village -----------------------------------------
def village_to_dict(v: Village) -> dict:
    return {
        "resources": v.resources,
        "updated_at": v.updated_at,
        "server_speed": v.server_speed,
        "max_queue": v.max_queue,
        "troops": v.troops,
        "away": v.away,
        "slots": {str(i): [s.building_id, s.level] for i, s in v.slots.items()},
        "queue": [[o.slot_index, o.target_level, o.finish_at] for o in v.queue],
        "training": [[t.building_id, t.unit_index, t.remaining, t.per_unit, t.next_finish]
                     for t in v.training],
        "research": v.research,
        "research_queue": [[r.unit_index, r.finish_at] for r in v.research_queue],
        "upgrades": v.upgrades,
        "upgrade_queue": [[u.unit_index, u.target_level, u.finish_at]
                          for u in v.upgrade_queue],
        "traps": v.traps,
        "trap_queue": [[t.remaining, t.per_unit, t.next_finish] for t in v.trap_queue],
    }


def village_from_row(row: sqlite3.Row) -> Village:
    d = json.loads(row["data"])
    slots = {int(i): Slot(building_id=b, level=l) for i, (b, l) in d["slots"].items()}
    queue = [BuildOrder(slot_index=s, target_level=t, finish_at=f) for s, t, f in d["queue"]]
    training = [TrainOrder(building_id=b, unit_index=u, remaining=r, per_unit=p, next_finish=f)
                for b, u, r, p, f in d.get("training", [])]
    research_queue = [ResearchOrder(unit_index=u, finish_at=f)
                      for u, f in d.get("research_queue", [])]
    upgrade_queue = [UpgradeOrder(unit_index=u, target_level=t, finish_at=f)
                     for u, t, f in d.get("upgrade_queue", [])]
    trap_queue = [TrapOrder(remaining=r, per_unit=p, next_finish=f)
                  for r, p, f in d.get("trap_queue", [])]
    return Village(
        id=row["id"], player_id=row["player_id"], name=row["name"],
        tribe=Tribe(row["tribe"]), x=row["x"], y=row["y"],
        is_capital=bool(row["is_capital"]),
        slots=slots, resources=d["resources"], updated_at=d["updated_at"],
        queue=queue, server_speed=d["server_speed"], max_queue=d["max_queue"],
        troops=d["troops"], away=d.get("away", [0] * 10), training=training,
        research=d.get("research", [0] * 10), research_queue=research_queue,
        upgrades=d.get("upgrades", [0] * 10), upgrade_queue=upgrade_queue,
        traps=d.get("traps", 0), trap_queue=trap_queue,
    )


# --- accès base --------------------------------------------------------------
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tribe INTEGER NOT NULL,
            is_npc INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS villages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            tribe INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            is_capital INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            UNIQUE(x, y)
        );
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_id INTEGER NOT NULL,
            target_id INTEGER,           -- village ciblé ; NULL si la cible est une oasis
            owner_id INTEGER NOT NULL,
            kind TEXT NOT NULL,          -- attack | raid | reinforce | trade
            phase TEXT NOT NULL,         -- outbound | back
            units TEXT NOT NULL,         -- json [10]
            loot TEXT NOT NULL DEFAULT '[0,0,0,0]',  -- butin (combat) ou cargaison (trade)
            arrive_at REAL NOT NULL,
            target_x INTEGER,            -- coordonnées de la cible (village ou oasis)
            target_y INTEGER,
            merchants INTEGER NOT NULL DEFAULT 0     -- trade : marchands mobilisés
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,          -- json
            seen INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tiles (
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            kind TEXT NOT NULL,          -- valley | oasis
            layout TEXT NOT NULL,        -- vallée: "4-4-4-6" ; oasis: code de bonus
            animals TEXT,                -- oasis: json[10] (garnison Nature) ; vallée: null
            PRIMARY KEY (x, y)
        );
        -- Héros : un par joueur, état (santé, niveau, attributs, inventaire) en JSON.
        CREATE TABLE IF NOT EXISTS heroes (
            player_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL
        );
        -- Aventures disponibles d'un joueur (le héros y est envoyé puis revient).
        CREATE TABLE IF NOT EXISTS adventures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            difficulty TEXT NOT NULL,    -- normal | hard
            created_at REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'available'  -- available | done
        );
        """)
        # Migration douce des bases antérieures : colonnes ajoutées au fil des features
        # (oasis : target_x/y avec target_id NULL ; commerce : merchants ; héros au
        # combat : drapeau hero ; expansion : points de culture cumulés du joueur).
        for col in ("target_x INTEGER", "target_y INTEGER",
                    "merchants INTEGER NOT NULL DEFAULT 0",
                    "hero INTEGER NOT NULL DEFAULT 0"):
            try:
                c.execute(f"ALTER TABLE movements ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # colonne déjà présente
        for col in ("culture REAL NOT NULL DEFAULT 0",
                    "culture_at REAL NOT NULL DEFAULT 0"):
            try:
                c.execute(f"ALTER TABLE players ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass


# --- Cases du monde (carte) --------------------------------------------------
def world_is_empty() -> bool:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM tiles").fetchone()["n"] == 0


def insert_tiles(tiles: list[dict]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT OR IGNORE INTO tiles(x, y, kind, layout, animals) VALUES (?,?,?,?,?)",
            [(t["x"], t["y"], t["kind"], t["layout"],
              json.dumps(t["animals"]) if t["animals"] is not None else None)
             for t in tiles])


def _tile_from_row(row: sqlite3.Row) -> dict:
    return {"x": row["x"], "y": row["y"], "kind": row["kind"], "layout": row["layout"],
            "animals": json.loads(row["animals"]) if row["animals"] else None}


def get_tile(x: int, y: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM tiles WHERE x=? AND y=?", (x, y)).fetchone()
    return _tile_from_row(row) if row else None


def tiles_in_box(x0: int, x1: int, y0: int, y1: int) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM tiles WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
            (x0, x1, y0, y1)).fetchall()
    return [_tile_from_row(r) for r in rows]


def update_tile_animals(x: int, y: int, animals: list[int]) -> None:
    with connect() as c:
        c.execute("UPDATE tiles SET animals=? WHERE x=? AND y=?",
                  (json.dumps(animals), x, y))


def insert_movement(origin_id, target_id, owner_id, kind, phase, units, arrive_at,
                    loot=(0, 0, 0, 0), target_x=None, target_y=None, merchants=0,
                    hero=0) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO movements(origin_id,target_id,owner_id,kind,phase,units,loot,"
            "arrive_at,target_x,target_y,merchants,hero) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (origin_id, target_id, owner_id, kind, phase, json.dumps(units),
             json.dumps(list(loot)), arrive_at, target_x, target_y, merchants, hero))
        return cur.lastrowid


def merchants_out(village_id: int) -> int:
    """Marchands actuellement mobilisés par les routes commerciales d'un village
    (aller comme retour : un marchand reste indisponible jusqu'à son retour)."""
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(merchants), 0) AS n FROM movements "
            "WHERE origin_id=? AND kind='trade'", (village_id,)).fetchone()
    return row["n"]


def due_movements(now: float) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM movements WHERE arrive_at<=? ORDER BY arrive_at",
                         (now,)).fetchall()
    return [dict(r) for r in rows]


def delete_movement(mid: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM movements WHERE id=?", (mid,))


def movements_for(village_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM movements WHERE origin_id=? OR target_id=? ORDER BY arrive_at",
            (village_id, village_id)).fetchall()
    return [dict(r) for r in rows]


def add_report(player_id: int, created_at: float, title: str, body: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO reports(player_id,created_at,title,body) VALUES (?,?,?,?)",
                  (player_id, created_at, title, json.dumps(body)))


def reports_for(player_id: int, limit: int = 30) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM reports WHERE player_id=? ORDER BY created_at DESC LIMIT ?",
            (player_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["body"] = json.loads(d["body"])
        out.append(d)
    return out


def create_player(name: str, tribe: Tribe, is_npc: bool = False) -> int:
    with connect() as c:
        cur = c.execute("INSERT INTO players(name, tribe, is_npc) VALUES (?,?,?)",
                        (name, int(tribe), int(is_npc)))
        return cur.lastrowid


def get_player(player_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
    return dict(row) if row else None


# --- Points de culture (expansion) ------------------------------------------
def get_culture(player_id: int) -> tuple[float, float]:
    """(points cumulés, instant de dernière mise à jour) du joueur."""
    with connect() as c:
        row = c.execute("SELECT culture, culture_at FROM players WHERE id=?",
                        (player_id,)).fetchone()
    return (row["culture"], row["culture_at"]) if row else (0.0, 0.0)


def set_culture(player_id: int, culture: float, culture_at: float) -> None:
    with connect() as c:
        c.execute("UPDATE players SET culture=?, culture_at=? WHERE id=?",
                  (culture, culture_at, player_id))


# --- Héros -------------------------------------------------------------------
def get_hero_row(player_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT data FROM heroes WHERE player_id=?",
                        (player_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def save_hero_row(player_id: int, data: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO heroes(player_id, data) VALUES (?,?) "
                  "ON CONFLICT(player_id) DO UPDATE SET data=excluded.data",
                  (player_id, json.dumps(data)))


# --- Aventures ---------------------------------------------------------------
def insert_adventure(player_id: int, x: int, y: int, difficulty: str,
                     created_at: float) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO adventures(player_id,x,y,difficulty,created_at,state) "
            "VALUES (?,?,?,?,?,'available')",
            (player_id, x, y, difficulty, created_at))
        return cur.lastrowid


def adventures_for(player_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM adventures WHERE player_id=? AND state='available' "
            "ORDER BY created_at", (player_id,)).fetchall()
    return [dict(r) for r in rows]


def get_adventure(adventure_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM adventures WHERE id=?",
                        (adventure_id,)).fetchone()
    return dict(row) if row else None


def count_adventures(player_id: int) -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM adventures "
                         "WHERE player_id=? AND state='available'",
                         (player_id,)).fetchone()["n"]


def mark_adventure_done(adventure_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE adventures SET state='done' WHERE id=?", (adventure_id,))


def insert_village(v: Village) -> Village:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO villages(player_id, name, tribe, x, y, is_capital, data) "
            "VALUES (?,?,?,?,?,?,?)",
            (v.player_id, v.name, int(v.tribe), v.x, v.y, int(v.is_capital),
             json.dumps(village_to_dict(v))))
        v.id = cur.lastrowid
    return v


def save_village(v: Village) -> None:
    with connect() as c:
        c.execute("UPDATE villages SET name=?, data=? WHERE id=?",
                  (v.name, json.dumps(village_to_dict(v)), v.id))


def load_village(village_id: int) -> Village | None:
    with connect() as c:
        row = c.execute("SELECT * FROM villages WHERE id=?", (village_id,)).fetchone()
    return village_from_row(row) if row else None


def list_villages() -> list[dict]:
    """Métadonnées de tous les villages (pour la carte / la liste)."""
    with connect() as c:
        rows = c.execute(
            "SELECT v.id, v.name, v.x, v.y, v.is_capital, v.player_id, p.name AS player "
            "FROM villages v JOIN players p ON p.id = v.player_id ORDER BY v.id").fetchall()
    return [dict(r) for r in rows]


def player_villages(player_id: int) -> list[int]:
    with connect() as c:
        rows = c.execute("SELECT id FROM villages WHERE player_id=? ORDER BY id",
                         (player_id,)).fetchall()
    return [r["id"] for r in rows]


def is_empty() -> bool:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM villages").fetchone()["n"] == 0
