from flask import Flask, jsonify, send_from_directory, request
import sqlite3
from pathlib import Path
from datetime import datetime

# ====== 项目根目录 ======
REPO_ROOT = Path(__file__).resolve().parent

# ====== 固定你的 DB 绝对路径 ======
DB_PATH = Path(r"./data/db/fridge.db") 

# ====== Flask 初始化 ======
app = Flask(__name__, static_folder=str(REPO_ROOT), static_url_path="")


def utc_now_iso() -> str:
    # e.g. "2026-02-25T20:00:10Z"
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


# =========================
# 首页 + 静态文件
# =========================
@app.route("/")
def home():
    return send_from_directory(str(REPO_ROOT), "index.html")


@app.route("/app.js")
def serve_js():
    return send_from_directory(str(REPO_ROOT), "app.js")


@app.route("/style.css")
def serve_css():
    return send_from_directory(str(REPO_ROOT), "style.css")


# =========================
# API：库存汇总（按 track_id 最新状态）
# =========================
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


# =========================
# API：查看所有 events（调试用）
# =========================
@app.route("/api/events")
def all_events():
    sql = """
    SELECT id, session_id, event_time_utc, item_name, status, confidence, track_id
    FROM events
    ORDER BY event_time_utc ASC, id ASC;
    """
    return jsonify(query_db(sql))


# =========================
# 手动添加：IN_FRIDGE（confidence 固定 1.00）
# body: {"item_name":"apple"}
# =========================
@app.route("/api/manual/add", methods=["POST"])
def manual_add():
    data = request.get_json(silent=True) or {}
    item_name = (data.get("item_name") or "").strip()

    if not item_name:
        return jsonify({"ok": False, "error": "item_name is required"}), 400

    confidence = 1.00  # ✅ 固定置信度

    # 生成新的 track_id = max(track_id)+1
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


# =========================
# 手动删除：REMOVED（confidence 固定 1.00）
# body: {"item_name":"apple"}
# 逻辑：找到“当前在库”的该物品对应 track_id（最新在库），写入 REMOVED
# =========================
@app.route("/api/manual/remove", methods=["POST"])
def manual_remove():
    data = request.get_json(silent=True) or {}
    item_name = (data.get("item_name") or "").strip()

    if not item_name:
        return jsonify({"ok": False, "error": "item_name is required"}), 400

    confidence = 1.00  # ✅ 固定置信度

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
    app.run(host="127.0.0.1", port=5000, debug=True)