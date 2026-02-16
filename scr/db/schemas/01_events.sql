PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT,
  event_time_utc TEXT NOT NULL,
  item_name      TEXT NOT NULL,
  action         TEXT NOT NULL CHECK(action IN ('PUT_IN','TAKE_OUT')),
  confidence     REAL,
  track_id       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time_utc);
CREATE INDEX IF NOT EXISTS idx_events_item_time ON events(item_name, event_time_utc);

