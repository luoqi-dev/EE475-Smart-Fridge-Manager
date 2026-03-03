PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collision_cases (
  case_id                 TEXT PRIMARY KEY,
  item_name               TEXT NOT NULL,
  pending_type            TEXT NOT NULL,
  status                  TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED','CANCELLED','EXPIRED')),
  created_at_utc          TEXT NOT NULL,
  updated_at_utc          TEXT NOT NULL,
  pending_item_ids_json   TEXT NOT NULL,
  default_remove_ids_json TEXT NOT NULL,
  summary                 TEXT NOT NULL,
  session_id              TEXT NULL,
  version                 INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_collision_cases_open_unique
ON collision_cases(item_name, pending_type)
WHERE status = 'OPEN';

CREATE INDEX IF NOT EXISTS idx_collision_cases_status_updated
ON collision_cases(status, updated_at_utc);

CREATE TABLE IF NOT EXISTS collision_actions (
  action_id             TEXT PRIMARY KEY,
  case_id               TEXT NOT NULL,
  created_at_utc        TEXT NOT NULL,
  remove_item_ids_json  TEXT NOT NULL,
  note                  TEXT NULL,
  processed_at_utc      TEXT NULL,
  status                TEXT NOT NULL CHECK(status IN ('NEW','PROCESSED','REJECTED')) DEFAULT 'NEW'
);

CREATE INDEX IF NOT EXISTS idx_collision_actions_status_created
ON collision_actions(status, created_at_utc);
