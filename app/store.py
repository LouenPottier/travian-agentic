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
from app.engine.village import (Village, Slot, BuildOrder, PlannedBuild, DemolishOrder,
                                TrainOrder, ResearchOrder, UpgradeOrder, TrapOrder)

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
        "build_plan": [[p.slot_index, p.building_id, p.target_level]
                       for p in v.build_plan],
        "demolition": ([v.demolition.slot_index, v.demolition.target_level,
                        v.demolition.finish_at] if v.demolition else None),
        "training": [[t.building_id, t.unit_index, t.remaining, t.per_unit, t.next_finish]
                     for t in v.training],
        "research": v.research,
        "research_queue": [[r.unit_index, r.finish_at] for r in v.research_queue],
        "upgrades": v.upgrades,
        "upgrade_queue": [[u.unit_index, u.target_level, u.finish_at]
                          for u in v.upgrade_queue],
        "traps": v.traps,
        "trap_queue": [[t.remaining, t.per_unit, t.next_finish] for t in v.trap_queue],
        "oases": v.oases,
        "prisoners": v.prisoners,
        "loyalty": v.loyalty,
        "celebration": v.celebration,
        "brewery_festival": v.brewery_festival,
    }


def village_from_row(row: sqlite3.Row) -> Village:
    d = json.loads(row["data"])
    slots = {int(i): Slot(building_id=b, level=l) for i, (b, l) in d["slots"].items()}
    queue = [BuildOrder(slot_index=s, target_level=t, finish_at=f) for s, t, f in d["queue"]]
    build_plan = [PlannedBuild(slot_index=s, building_id=b, target_level=t)
                  for s, b, t in d.get("build_plan", [])]
    dem = d.get("demolition")
    demolition = DemolishOrder(slot_index=dem[0], target_level=dem[1], finish_at=dem[2]) if dem else None
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
        queue=queue, build_plan=build_plan, demolition=demolition,
        server_speed=d["server_speed"], max_queue=d["max_queue"],
        troops=d["troops"], away=d.get("away", [0] * 10), training=training,
        research=d.get("research", [0] * 10), research_queue=research_queue,
        upgrades=d.get("upgrades", [0] * 10), upgrade_queue=upgrade_queue,
        traps=d.get("traps", 0), trap_queue=trap_queue,
        oases=d.get("oases", []),
        prisoners=d.get("prisoners", []),
        loyalty=d.get("loyalty", 100.0),
        celebration=d.get("celebration"),
        brewery_festival=d.get("brewery_festival"),
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
            merchants INTEGER NOT NULL DEFAULT 0,    -- trade : marchands mobilisés
            hero INTEGER NOT NULL DEFAULT 0,         -- attaque/razzia : héros embarqué
            targets TEXT NOT NULL DEFAULT '[]'       -- siège : ids de bâtiments visés par les catapultes
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
        -- Routes commerciales récurrentes : envoi périodique de ressources d'un
        -- village vers un autre (déclenché au passage de `next_run`, cf. movement).
        CREATE TABLE IF NOT EXISTS trade_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            amounts TEXT NOT NULL,          -- json [4]
            interval_hours REAL NOT NULL,   -- cadence en heures (temps de base, ÷vitesse serveur)
            next_run REAL NOT NULL          -- date absolue du prochain envoi
        );
        -- Liste de fermes (farm list T4) : cibles de razzia récurrentes d'un village,
        -- chacune avec un modèle de troupes ; « razzia groupée » = envoi d'un raid sur
        -- chaque cible (cf. engine.farmlist).
        CREATE TABLE IF NOT EXISTS farm_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_id INTEGER NOT NULL,     -- village qui razzie
            owner_id INTEGER NOT NULL,
            target_id INTEGER,              -- village ciblé ; NULL si oasis
            target_x INTEGER NOT NULL,
            target_y INTEGER NOT NULL,
            units TEXT NOT NULL,            -- json [10] (modèle de troupes)
            label TEXT NOT NULL DEFAULT ''
        );
        -- Artefacts (endgame Natars) : chaque artefact est soit détenu par un village
        -- Natar (`holder='natar'`, `natar_village_id` renseigné), soit capturé et stocké
        -- dans la trésorerie d'un village du joueur (`holder='player'`, `owner_id` +
        -- `village_id`). Cf. engine.artifacts / data.artifacts.
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind INTEGER NOT NULL,           -- 1..8 (type, cf. data.artifacts.TYPES)
            size TEXT NOT NULL,              -- small | large | unique
            holder TEXT NOT NULL,            -- natar | player
            natar_village_id INTEGER,        -- village Natar détenteur (si holder=natar)
            owner_id INTEGER,                -- joueur détenteur (si holder=player)
            village_id INTEGER               -- trésorerie de stockage (si holder=player)
        );
        -- Clé/valeur global du monde. Sert notamment au **battement de cœur** de la
        -- détection d'arrêt serveur (cf. engine.downtime) : `last_alive` = dernier
        -- instant (temps mural) où le serveur tournait ; un grand trou ⇒ arrêt/veille
        -- pendant lequel famine et routes commerciales sont mises en pause.
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        # Migration douce des bases antérieures : colonnes ajoutées au fil des features
        # (oasis : target_x/y avec target_id NULL ; commerce : merchants ; héros au
        # combat : drapeau hero ; expansion : points de culture cumulés du joueur).
        for col in ("target_x INTEGER", "target_y INTEGER",
                    "merchants INTEGER NOT NULL DEFAULT 0",
                    "hero INTEGER NOT NULL DEFAULT 0",
                    "targets TEXT NOT NULL DEFAULT '[]'"):
            try:
                c.execute(f"ALTER TABLE movements ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # colonne déjà présente
        for col in ("culture REAL NOT NULL DEFAULT 0",
                    "culture_at REAL NOT NULL DEFAULT 0",
                    # Classement / statistiques (cf. engine.ranking) : compteurs
                    # cumulés au fil des combats. Points = upkeep des troupes tuées.
                    "off_points REAL NOT NULL DEFAULT 0",
                    "def_points REAL NOT NULL DEFAULT 0",
                    "raided REAL NOT NULL DEFAULT 0",
                    # Phase 4 : joueur (un vrai compte, pas un PNJ passif) piloté par un
                    # agent LLM. Distinct de `is_npc` (Natars/Nature/Voisin, jamais joués).
                    "agent INTEGER NOT NULL DEFAULT 0"):
            try:
                c.execute(f"ALTER TABLE players ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        # Occupation d'oasis : le village (NULL = libre) qui annexe la case oasis.
        try:
            c.execute("ALTER TABLE tiles ADD COLUMN owner_id INTEGER")
        except sqlite3.OperationalError:
            pass


# --- Métadonnées globales (clé/valeur) --------------------------------------
def get_meta(key: str) -> str | None:
    with connect() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value) -> None:
    with connect() as c:
        c.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


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
    keys = row.keys()
    return {"x": row["x"], "y": row["y"], "kind": row["kind"], "layout": row["layout"],
            "animals": json.loads(row["animals"]) if row["animals"] else None,
            "owner_id": row["owner_id"] if "owner_id" in keys else None}


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


def set_tile_owner(x: int, y: int, owner_id: int | None) -> None:
    """Rattache (ou détache, owner_id=None) une oasis à un village."""
    with connect() as c:
        c.execute("UPDATE tiles SET owner_id=? WHERE x=? AND y=?", (owner_id, x, y))


def set_tile_layout(x: int, y: int, layout: str) -> None:
    """Force la distribution de champs d'une vallée (seeding : capitale rivale posée
    sur un 15-cropper quand la carte n'en offre pas un à portée de l'ancrage)."""
    with connect() as c:
        c.execute("UPDATE tiles SET layout=? WHERE x=? AND y=? AND kind='valley'",
                  (layout, x, y))


def insert_movement(origin_id, target_id, owner_id, kind, phase, units, arrive_at,
                    loot=(0, 0, 0, 0), target_x=None, target_y=None, merchants=0,
                    hero=0, targets=()) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO movements(origin_id,target_id,owner_id,kind,phase,units,loot,"
            "arrive_at,target_x,target_y,merchants,hero,targets) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (origin_id, target_id, owner_id, kind, phase, json.dumps(units),
             json.dumps(list(loot)), arrive_at, target_x, target_y, merchants, hero,
             json.dumps(list(targets))))
        return cur.lastrowid


def merchants_out(village_id: int) -> int:
    """Marchands actuellement mobilisés par les routes commerciales d'un village
    (aller comme retour : un marchand reste indisponible jusqu'à son retour)."""
    with connect() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(merchants), 0) AS n FROM movements "
            "WHERE origin_id=? AND kind='trade'", (village_id,)).fetchone()
    return row["n"]


def pending_settlements(player_id: int) -> int:
    """Nombre de trains de colons d'un joueur **en route** vers une fondation
    (mouvements `settle` en phase aller). Sert à réserver l'emplacement
    d'expansion et le seuil de culture tant que la fondation n'a pas eu lieu :
    sans ça, on pourrait dépasser son quota en lançant plusieurs colons en
    parallèle (le slot/la culture n'étant consommés qu'à l'arrivée)."""
    with connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM movements "
            "WHERE owner_id=? AND kind='settle' AND phase='outbound'",
            (player_id,)).fetchone()
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


# --- Routes commerciales récurrentes ----------------------------------------
def insert_trade_route(origin_id, target_id, owner_id, amounts,
                       interval_hours, next_run) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO trade_routes(origin_id,target_id,owner_id,amounts,"
            "interval_hours,next_run) VALUES (?,?,?,?,?,?)",
            (origin_id, target_id, owner_id, json.dumps(list(amounts)),
             interval_hours, next_run))
        return cur.lastrowid


def trade_routes_for(origin_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM trade_routes WHERE origin_id=? ORDER BY id",
                         (origin_id,)).fetchall()
    return [dict(r) for r in rows]


def due_trade_routes(now: float) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM trade_routes WHERE next_run<=? ORDER BY next_run",
                         (now,)).fetchall()
    return [dict(r) for r in rows]


def update_trade_route_next_run(route_id: int, next_run: float) -> None:
    with connect() as c:
        c.execute("UPDATE trade_routes SET next_run=? WHERE id=?", (next_run, route_id))


def delete_trade_route(route_id: int, origin_id: int) -> int:
    with connect() as c:
        cur = c.execute("DELETE FROM trade_routes WHERE id=? AND origin_id=?",
                        (route_id, origin_id))
        return cur.rowcount


def delete_trade_routes_by_origin(origin_id: int) -> int:
    """Supprime toutes les routes commerciales partant d'un village (utilisé à la
    conquête : sinon elles deviennent des zombies retentées en boucle pour l'ancien
    propriétaire, cf. movement._process_trade_routes_locked)."""
    with connect() as c:
        return c.execute("DELETE FROM trade_routes WHERE origin_id=?",
                         (origin_id,)).rowcount


# --- Liste de fermes (farm list) --------------------------------------------
def insert_farm_target(origin_id, owner_id, target_id, target_x, target_y,
                       units, label="") -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO farm_targets(origin_id,owner_id,target_id,target_x,"
            "target_y,units,label) VALUES (?,?,?,?,?,?,?)",
            (origin_id, owner_id, target_id, target_x, target_y,
             json.dumps(list(units)), label))
        return cur.lastrowid


def farm_targets_for(origin_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM farm_targets WHERE origin_id=? ORDER BY id",
                         (origin_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_farm_target(target_id: int, origin_id: int) -> int:
    with connect() as c:
        cur = c.execute("DELETE FROM farm_targets WHERE id=? AND origin_id=?",
                        (target_id, origin_id))
        return cur.rowcount


def delete_farm_targets_by_origin(origin_id: int) -> int:
    """Supprime toute la liste de fermes d'un village (utilisé à la conquête :
    le nouveau propriétaire ne doit pas hériter des cibles de l'ancien)."""
    with connect() as c:
        return c.execute("DELETE FROM farm_targets WHERE origin_id=?",
                         (origin_id,)).rowcount


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


def create_player(name: str, tribe: Tribe, is_npc: bool = False,
                  agent: bool = False) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO players(name, tribe, is_npc, agent) VALUES (?,?,?,?)",
            (name, int(tribe), int(is_npc), int(agent)))
        return cur.lastrowid


def agent_players() -> list[dict]:
    """Joueurs pilotés par un agent LLM (Phase 4 : `players.agent=1`)."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, name, tribe FROM players WHERE agent=1 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_player(player_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
    return dict(row) if row else None


def find_player_by_name(name: str) -> int | None:
    """Id du premier joueur portant ce nom (sert au seeding idempotent des Natars)."""
    with connect() as c:
        row = c.execute("SELECT id FROM players WHERE name=? LIMIT 1", (name,)).fetchone()
    return row["id"] if row else None


def all_players() -> list[dict]:
    """Tous les joueurs avec leurs compteurs de classement (cf. engine.ranking)."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, name, tribe, is_npc, off_points, def_points, raided "
            "FROM players ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# --- Statistiques / classement ----------------------------------------------
def add_player_stats(player_id: int, off: float = 0.0, deff: float = 0.0,
                     raided: float = 0.0) -> None:
    """Incrémente les compteurs de classement d'un joueur (points d'attaque/défense,
    ressources pillées). Cumulatif ; appelé à la résolution des combats."""
    if player_id is None or (off == 0 and deff == 0 and raided == 0):
        return
    with connect() as c:
        c.execute("UPDATE players SET off_points=off_points+?, "
                  "def_points=def_points+?, raided=raided+? WHERE id=?",
                  (off, deff, raided, player_id))


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


# --- Artefacts (endgame Natars) ----------------------------------------------
def artifacts_exist() -> bool:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] > 0


def insert_artifact(kind: int, size: str, natar_village_id: int) -> int:
    """Crée un artefact détenu par un village Natar (non encore capturé)."""
    with connect() as c:
        cur = c.execute(
            "INSERT INTO artifacts(kind,size,holder,natar_village_id) "
            "VALUES (?,?,'natar',?)", (kind, size, natar_village_id))
    _invalidate_artifacts_cache()
    return cur.lastrowid


def artifact_held_by_natar(natar_village_id: int) -> dict | None:
    """Artefact (non capturé) détenu par ce village Natar, le cas échéant."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM artifacts WHERE holder='natar' AND natar_village_id=? LIMIT 1",
            (natar_village_id,)).fetchone()
    return dict(row) if row else None


def artifact_in_village(village_id: int) -> dict | None:
    """Artefact actuellement stocké dans la trésorerie de ce village (sinon None)."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM artifacts WHERE holder='player' AND village_id=? LIMIT 1",
            (village_id,)).fetchone()
    return dict(row) if row else None


def capture_artifact(artifact_id: int, owner_id: int, village_id: int) -> None:
    """Transfère un artefact à un joueur (stocké dans la trésorerie `village_id`)."""
    with connect() as c:
        c.execute(
            "UPDATE artifacts SET holder='player', owner_id=?, village_id=?, "
            "natar_village_id=NULL WHERE id=?", (owner_id, village_id, artifact_id))
    _invalidate_artifacts_cache()


# Cache mémoire de `artifacts_owned_by` : cette lecture est sur le **chemin chaud**
# (`village.troop_upkeep` → `artifacts.crop_multiplier`, appelée à chaque itération de
# `village.tick`). Sans cache, chaque appel rouvre une connexion SQLite ⇒ des milliers de
# requêtes quand on ticke un village (surtout au `downtime._freeze` qui rejoue tout le monde)
# ⇒ **gel**. Les artefacts d'un joueur ne changent qu'à la capture/conquête ⇒ on invalide
# tout le cache sur ces (rares) écritures. La majorité des joueurs n'en possèdent aucun :
# on cache aussi la liste **vide** (le cas courant), ce qui supprime la requête pour eux.
_artifacts_owned_cache: dict[int, list[dict]] = {}


def _invalidate_artifacts_cache() -> None:
    _artifacts_owned_cache.clear()


def artifacts_owned_by(owner_id: int) -> list[dict]:
    """Artefacts capturés (actifs) d'un joueur (caché, cf. `_artifacts_owned_cache`)."""
    cached = _artifacts_owned_cache.get(owner_id)
    if cached is not None:
        return cached
    with connect() as c:
        rows = c.execute("SELECT * FROM artifacts WHERE holder='player' AND owner_id=? "
                         "ORDER BY id", (owner_id,)).fetchall()
    result = [dict(r) for r in rows]
    _artifacts_owned_cache[owner_id] = result
    return result


def uncaptured_artifacts() -> list[dict]:
    """Artefacts encore détenus par des villages Natars (avec leurs coordonnées)."""
    with connect() as c:
        rows = c.execute(
            "SELECT a.*, v.x AS x, v.y AS y, v.name AS village_name "
            "FROM artifacts a JOIN villages v ON v.id = a.natar_village_id "
            "WHERE a.holder='natar' ORDER BY a.id").fetchall()
    return [dict(r) for r in rows]


def release_artifacts_of_village(village_id: int) -> None:
    """Détache tous les artefacts stockés dans un village (à la conquête de ce village)
    : ils ne disparaissent pas mais cessent d'être stockés (sans trésorerie)."""
    with connect() as c:
        c.execute("UPDATE artifacts SET village_id=NULL WHERE holder='player' "
                  "AND village_id=?", (village_id,))
    _invalidate_artifacts_cache()


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
    """Sauvegarde l'état dynamique. `name` et les colonnes identitaires
    (player_id, tribe, is_capital) sont aussi écrites : la conquête les modifie."""
    with connect() as c:
        c.execute("UPDATE villages SET name=?, player_id=?, tribe=?, is_capital=?, data=? "
                  "WHERE id=?",
                  (v.name, v.player_id, int(v.tribe), int(v.is_capital),
                   json.dumps(village_to_dict(v)), v.id))


def delete_movements_by_origin(origin_id: int) -> int:
    """Supprime tous les mouvements partant d'un village (utilisé à la conquête :
    les armées du village conquis sont perdues, y compris celles en déplacement)."""
    with connect() as c:
        cur = c.execute("DELETE FROM movements WHERE origin_id=?", (origin_id,))
        return cur.rowcount


def move_village(village_id: int, x: int, y: int) -> None:
    """Déplace un village sur la carte (change ses coordonnées). Utilisé au seeding
    pour relocaliser la capitale humaine hors de la zone Natar (centre)."""
    with connect() as c:
        c.execute("UPDATE villages SET x=?, y=? WHERE id=?", (x, y, village_id))


def load_village(village_id: int) -> Village | None:
    with connect() as c:
        row = c.execute("SELECT * FROM villages WHERE id=?", (village_id,)).fetchone()
    return village_from_row(row) if row else None


def list_villages() -> list[dict]:
    """Métadonnées de tous les villages (pour la carte / la liste)."""
    with connect() as c:
        rows = c.execute(
            "SELECT v.id, v.name, v.x, v.y, v.is_capital, v.tribe, v.player_id, "
            "p.name AS player "
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
