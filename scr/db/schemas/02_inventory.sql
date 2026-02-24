PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS inventory (
  instance_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  item_name          TEXT NOT NULL,
  put_in_time_utc    TEXT NOT NULL,
  put_in_event_id    INTEGER NOT NULL,
  take_out_time_utc  TEXT,
  take_out_event_id  INTEGER,
  status             TEXT NOT NULL CHECK(status IN ('IN','OUT')),

  FOREIGN KEY(put_in_event_id) REFERENCES events(id),
  FOREIGN KEY(take_out_event_id) REFERENCES events(id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_status_name ON inventory(status, item_name);
CREATE INDEX IF NOT EXISTS idx_inventory_put_time ON inventory(put_in_time_utc);

