PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS events (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id       TEXT,
  event_time_utc   TEXT NOT NULL,
  item_name        TEXT NOT NULL,
  status           TEXT NOT NULL CHECK(status IN ('IN_FRIDGE','REMOVED','PENDING')),
  confidence       REAL,
  track_id         INTEGER,
  pending_type     TEXT NULL,
  case_id          TEXT NULL,
  updated_at_utc   TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time_utc);
CREATE INDEX IF NOT EXISTS idx_events_item_time ON events(item_name, event_time_utc);
CREATE INDEX IF NOT EXISTS idx_events_case_status ON events(case_id, status, item_name);
