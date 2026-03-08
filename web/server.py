from flask import Flask, jsonify, send_from_directory, request
import sqlite3
from pathlib import Path
from datetime import datetime
import json
import uuid

# Project root directory
REPO_ROOT = Path(__file__).resolve().parent

# database path (same folder as server.py)
DB_PATH = REPO_ROOT / "fridge.db"

app = Flask(__name__, static_folder=str(REPO_ROOT), static_url_path="")

def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def query_db(sql: str, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def exec_db(sql: str, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def safe_json_loads(s, default):
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default

@app.route("/")
def home():
    return send_from_directory(str(REPO_ROOT), "index.html")

@app.route("/app.js")
def serve_js():
    return send_from_directory(str(REPO_ROOT), "app.js")

@app.route("/style.css")
def serve_css():
    return send_from_directory(str(REPO_ROOT), "style.css")

# ---------------------------
# Inventory APIs
# ---------------------------

# New: return instance-level inventory (latest status per track_id)
@app.route("/api/inventory")
def inventory_instances():
    sql = """
    WITH ranked AS (
      SELECT
        id, session_id, event_time_utc, item_name, status, confidence, track_id,
        ROW_NUMBER() OVER (
          PARTITION BY track_id
          ORDER BY event_time_utc DESC, id DESC
        ) AS rn
      FROM events
    )
    SELECT
      id, item_name, status, event_time_utc, confidence, track_id
    FROM ranked
    WHERE rn = 1 AND status = 'IN_FRIDGE'
    ORDER BY event_time_utc DESC, id DESC;
    """
    return jsonify(query_db(sql))

# Keep your old summary endpoint (do not break existing UI)
@app.route("/api/inventory/summary")
def inventory_summary():
    sql = """
    WITH ranked AS (
      SELECT
        id,
        item_name,
        status,
        event_time_utc,
        track_id,
        ROW_NUMBER() OVER (
          PARTITION BY track_id
          ORDER BY event_time_utc DESC, id DESC
        ) AS rn
      FROM events
    )
    SELECT
      lower(trim(item_name)) AS item_name,
      COUNT(*) AS quantity,
      MIN(event_time_utc) AS earliest_put_in_time,
      MAX(event_time_utc) AS latest_put_in_time
    FROM ranked
    WHERE rn = 1 AND status = 'IN_FRIDGE'
    GROUP BY lower(trim(item_name))
    ORDER BY item_name ASC;
    """
    return jsonify(query_db(sql))

@app.route("/api/events")
def all_events():
    sql = """
    SELECT id, session_id, event_time_utc, item_name, status, confidence, track_id
    FROM events
    ORDER BY event_time_utc ASC, id ASC;
    """
    return jsonify(query_db(sql))

# ---------------------------
# Collision (new integration)
# ---------------------------

# GET /api/collisions/open?include_items=1
@app.route("/api/collisions/open")
def collisions_open():
    include_items = request.args.get("include_items", "0") == "1"

    cases = query_db("""
      SELECT
        case_id, item_name, pending_type, status,
        pending_item_ids_json, default_remove_ids_json, summary,
        version, created_at_utc, updated_at_utc
      FROM collision_cases
      WHERE status = 'OPEN'
      ORDER BY updated_at_utc DESC, created_at_utc DESC;
    """)

    if not include_items:
        return jsonify(cases)

    # Attach item details for each case
    out = []
    for c in cases:
        pending_ids = safe_json_loads(c.get("pending_item_ids_json"), [])
        # normalize to int list if possible
        norm_ids = []
        for x in pending_ids:
            try:
                norm_ids.append(int(x))
            except Exception:
                pass

        items = []
        if norm_ids:
            placeholders = ",".join(["?"] * len(norm_ids))
            items = query_db(
                f"""
                SELECT id, item_name, status, event_time_utc, confidence, track_id
                FROM events
                WHERE id IN ({placeholders})
                ORDER BY event_time_utc DESC, id DESC;
                """,
                tuple(norm_ids),
            )

        c2 = dict(c)
        c2["pending_item_ids"] = pending_ids
        c2["default_remove_ids"] = safe_json_loads(c.get("default_remove_ids_json"), [])
        c2["items"] = items
        out.append(c2)

    return jsonify(out)

# POST /api/collisions/<case_id>/actions
@app.route("/api/collisions/<case_id>/actions", methods=["POST"])
def collisions_action(case_id):
    data = request.get_json(silent=True) or {}
    remove_item_ids = data.get("remove_item_ids", [])
    if not isinstance(remove_item_ids, list):
        return jsonify({"ok": False, "error": "remove_item_ids must be a list"}), 400

    # action_id optional; generate if missing
    action_id = (data.get("action_id") or "").strip() or str(uuid.uuid4())

    # Ensure case exists and is OPEN (optional but safer)
    rows = query_db("SELECT status FROM collision_cases WHERE case_id = ?;", (case_id,))
    if not rows:
        return jsonify({"ok": False, "error": f"case_id not found: {case_id}"}), 404
    if rows[0]["status"] != "OPEN":
        return jsonify({"ok": False, "error": f"case is not OPEN (status={rows[0]['status']})"}), 409

    exec_db(
        """
        INSERT INTO collision_actions (action_id, case_id, remove_item_ids_json, status, created_at_utc)
        VALUES (?, ?, ?, 'NEW', ?);
        """,
        (action_id, case_id, json.dumps(remove_item_ids), utc_now_iso()),
    )

    return jsonify({"ok": True, "action_id": action_id})

# ---------------------------
# Manual controls (keep)
# ---------------------------

@app.route("/api/manual/add", methods=["POST"])
def manual_add():
    data = request.get_json(silent=True) or {}
    item_name = (data.get("item_name") or "").strip()
    if not item_name:
        return jsonify({"ok": False, "error": "item_name is required"}), 400

    confidence = 1.00
    row = query_db("SELECT COALESCE(MAX(track_id), 0) AS mx FROM events;")
    next_track = int(row[0]["mx"]) + 1 if row else 1

    session_id = "manual_add"
    event_time_utc = utc_now_iso()

    exec_db(
        """
        INSERT INTO events (session_id, event_time_utc, item_name, status, confidence, track_id)
        VALUES (?, ?, ?, 'IN_FRIDGE', ?, ?);
        """,
        (session_id, event_time_utc, item_name, confidence, next_track),
    )
    return jsonify({"ok": True, "track_id": next_track, "event_time_utc": event_time_utc})

@app.route("/api/manual/remove", methods=["POST"])
def manual_remove():
    data = request.get_json(silent=True) or {}
    item_name = (data.get("item_name") or "").strip()
    if not item_name:
        return jsonify({"ok": False, "error": "item_name is required"}), 400

    confidence = 1.00

    sql_find = """
    WITH ranked AS (
      SELECT
        id, item_name, status, event_time_utc, track_id,
        ROW_NUMBER() OVER (PARTITION BY track_id ORDER BY event_time_utc DESC, id DESC) AS rn
      FROM events
    )
    SELECT track_id, event_time_utc
    FROM ranked
    WHERE rn = 1 AND status = 'IN_FRIDGE' AND lower(trim(item_name)) = lower(trim(?))
    ORDER BY event_time_utc DESC
    LIMIT 1;
    """
    rows = query_db(sql_find, (item_name,))
    if not rows:
        return jsonify({"ok": False, "error": f'No IN_FRIDGE item found for "{item_name}"'}), 404

    track_id = int(rows[0]["track_id"])
    session_id = "manual_remove"
    event_time_utc = utc_now_iso()

    exec_db(
        """
        INSERT INTO events (session_id, event_time_utc, item_name, status, confidence, track_id)
        VALUES (?, ?, ?, 'REMOVED', ?, ?);
        """,
        (session_id, event_time_utc, item_name, confidence, track_id),
    )

    return jsonify({"ok": True, "track_id": track_id, "event_time_utc": event_time_utc})

if __name__ == "__main__":
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    print(f"Using DB: {DB_PATH}")
    app.run(host="0.0.0.0", port=5000, debug=True)


