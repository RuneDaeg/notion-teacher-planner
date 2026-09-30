CREATE TABLE IF NOT EXISTS planner_installations (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  bot_id TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  credentials TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'waiting',
  state_json TEXT NOT NULL DEFAULT '{}',
  next_due INTEGER NOT NULL,
  immediate INTEGER NOT NULL DEFAULT 1 CHECK (immediate IN (0, 1)),
  run_day TEXT,
  last_success_day TEXT,
  last_success_at INTEGER,
  failures INTEGER NOT NULL DEFAULT 0,
  rotation_pending INTEGER NOT NULL DEFAULT 0 CHECK (rotation_pending IN (0, 1)),
  lease_token TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS planner_due ON planner_installations(enabled, next_due, id);

CREATE TABLE IF NOT EXISTS planner_scheduler (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  lease_token TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  budget_day TEXT NOT NULL DEFAULT '',
  steps INTEGER NOT NULL DEFAULT 0 CHECK (steps >= 0)
);
INSERT OR IGNORE INTO planner_scheduler(singleton) VALUES (1);

CREATE TABLE IF NOT EXISTS planner_school_cache (
  cache_key TEXT PRIMARY KEY,
  snapshot_json TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS planner_cache_expiry ON planner_school_cache(expires_at);
