-- Opt-in template updates are independent from the daily-sync manifest/journal.
-- Only target identifiers and bounded recovery metadata belong in this table.
CREATE TABLE IF NOT EXISTS planner_template_updates (
  installation_id TEXT NOT NULL REFERENCES planner_installations(id),
  update_id TEXT NOT NULL CHECK (update_id = 'student-history-v1'),
  targets_json TEXT NOT NULL CHECK (length(CAST(targets_json AS BLOB)) <= 4096),
  journal_json TEXT NOT NULL CHECK (length(CAST(journal_json AS BLOB)) <= 12000),
  status TEXT NOT NULL CHECK (status IN ('pending', 'schema_applied', 'assistance_required', 'complete')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (installation_id, update_id)
);
