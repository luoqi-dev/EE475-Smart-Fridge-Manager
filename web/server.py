from flask import Flask, jsonify, send_from_directory, request
import sqlite3
from pathlib import Path
from datetime import datetime
import json
import uuid

# Project root directory
REPO_ROOT = Path(__file__).resolve().parent

# database path (same folder as server.py)
DB_PATH = Path("data/db/fridge.db")

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

# Return instance-level inventory directly from current event rows.
@app.route("/api/inventory")
def inventory_instances():
    sql = """
    SELECT
      id, item_name, status, event_time_utc, confidence, track_id
    FROM events
    WHERE status = 'IN_FRIDGE'
    ORDER BY event_time_utc DESC, id DESC;
    """
    return jsonify(query_db(sql))

# Keep the summary endpoint shape used by the current UI.
@app.route("/api/inventory/summary")
def inventory_summary():
    sql = """
    SELECT
      lower(trim(item_name)) AS item_name,
      COUNT(*) AS quantity,
      MIN(event_time_utc) AS earliest_put_in_time,
      MAX(event_time_utc) AS latest_put_in_time
    FROM events
    WHERE status = 'IN_FRIDGE'
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
# Environment API
# ---------------------------

@app.route("/api/environment/latest")
def environment_latest():
    sql = """
    SELECT id, temperature, humidity
    FROM environment
    ORDER BY id DESC
    LIMIT 1;
    """
    rows = query_db(sql)
    if not rows:
        return jsonify({"temperature": None, "humidity": None})
    return jsonify(rows[0])

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

    year = int(data.get("year") or 0)
    month = int(data.get("month") or 0)
    day = int(data.get("day") or 1)
    hour = int(data.get("hour") or 12)

    if year <= 0 or not 1 <= month <= 12:
        return jsonify({"ok": False, "error": "year and month are required"}), 400
    if not 1 <= day <= 31:
        return jsonify({"ok": False, "error": "day must be between 1 and 31"}), 400
    if not 0 <= hour <= 23:
        return jsonify({"ok": False, "error": "hour must be between 0 and 23"}), 400

    session_id = "session_00000000_000000"
    event_time_utc = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00Z"
    confidence = 1.0
    track_id = -1

    exec_db(
        """
        INSERT INTO events (session_id, event_time_utc, item_name, status, confidence, track_id)
        VALUES (?, ?, ?, 'IN_FRIDGE', ?, ?);
        """,
        (session_id, event_time_utc, item_name, confidence, track_id),
    )
    return jsonify({"ok": True, "event_time_utc": event_time_utc})

@app.route("/api/manual/remove/candidates", methods=["POST"])
def manual_remove_candidates():
    data = request.get_json(silent=True) or {}
    item_name = (data.get("item_name") or "").strip()
    if not item_name:
        return jsonify({"ok": False, "error": "item_name is required"}), 400

    sql_find = """
    SELECT id, item_name, event_time_utc
    FROM events
    WHERE status = 'IN_FRIDGE'
      AND lower(trim(item_name)) = lower(trim(?))
    ORDER BY event_time_utc ASC, id ASC;
    """
    rows = query_db(sql_find, (item_name,))
    return jsonify({"ok": True, "items": rows})

@app.route("/api/manual/remove", methods=["POST"])
def manual_remove():
    data = request.get_json(silent=True) or {}
    remove_item_ids = data.get("remove_item_ids", [])
    if not isinstance(remove_item_ids, list) or not remove_item_ids:
        return jsonify({"ok": False, "error": "remove_item_ids must be a non-empty list"}), 400

    norm_ids = []
    for item_id in remove_item_ids:
        try:
            norm_ids.append(int(item_id))
        except Exception:
            return jsonify({"ok": False, "error": f"invalid event id: {item_id}"}), 400

    event_time_utc = utc_now_iso()
    placeholders = ",".join(["?"] * len(norm_ids))

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            f"""
            UPDATE events
            SET status = 'REMOVED',
                updated_at_utc = ?
            WHERE id IN ({placeholders})
              AND status = 'IN_FRIDGE';
            """,
            (event_time_utc, *norm_ids),
        )
        conn.commit()
    finally:
        conn.close()

    rows = query_db(
        f"""
        SELECT id
        FROM events
        WHERE id IN ({placeholders})
          AND status = 'REMOVED'
        ORDER BY id ASC;
        """,
        tuple(norm_ids),
    )
    return jsonify({"ok": True, "removed_item_ids": [int(row["id"]) for row in rows], "event_time_utc": event_time_utc})

if __name__ == "__main__":
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    print(f"Using DB: {DB_PATH}")
    app.run(host="0.0.0.0", port=0, debug=True)


