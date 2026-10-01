// D1-only state: every remote mutation must await a fenced saveState checkpoint.
export const LEASE_MS = 240_000;
const DAY_MS = 86_400_000;
const encoder = new TextEncoder();

export class StoreError extends Error {
  constructor(code = 'storage_invalid', status = 400) { super(code); this.code = code; this.status = status; }
}
export class BusyError extends StoreError { constructor() { super('busy', 409); } }
export class LeaseError extends StoreError { constructor() { super('lease_expired', 409); } }

export function koreaDay(now = Date.now()) { return new Date(now + 9 * 3_600_000).toISOString().slice(0, 10); }
export function morning(day) { return Date.parse(day + 'T07:00:00+09:00'); }
// Completed/failed daily runs defer to the next calendar day, even when an
// explicitly requested first run took place before today's 07:00 schedule.
export function nextMorning(now = Date.now()) { return morning(koreaDay(now)) + DAY_MS; }

function limit(value, fallback, maximum) {
  const number = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(number) || number < 1 || number > maximum) throw new StoreError('invalid_limit');
  return number;
}
function json(value, maximum) {
  let result;
  try { result = JSON.stringify(value); } catch { throw new StoreError('invalid_state'); }
  if (typeof result !== 'string' || encoder.encode(result).length > maximum) throw new StoreError('state_too_large');
  return result;
}
function canonical(manifest) { return json(Object.fromEntries(Object.keys(manifest).sort().map(key => [key, manifest[key]])), 4096); }
function record(row) {
  if (!row) return null;
  const { manifest_json, state_json, ...rest } = row;
  return { ...rest, manifest: JSON.parse(manifest_json), state: JSON.parse(state_json), enabled: !!row.enabled,
    rotation_pending: !!row.rotation_pending };
}
export async function installationId(workspace, root) {
  const bytes = await crypto.subtle.digest('SHA-256', encoder.encode(workspace + ':' + root));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function token() { return crypto.randomUUID(); }
const UPDATE_ID = 'student-history-v1';
const UPDATE_STATUSES = new Set(['pending', 'schema_applied', 'assistance_required', 'complete']);
function updateObject(value, maximum) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new StoreError('invalid_update');
  return json(Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])), maximum);
}

export class Store {
  constructor(db, { maxInstallations, maxDailySteps, now = Date.now } = {}) {
    this.db = db; this.now = now;
    this.maxInstallations = limit(maxInstallations, 50, 5000);
    this.maxDailySteps = limit(maxDailySteps, 1000, 10_000);
  }
  stmt(sql, ...args) { return this.db.prepare(sql).bind(...args); }
  async get(id) { return record(await this.stmt('SELECT * FROM planner_installations WHERE id = ?', id).first()); }
  async getUpdates(id) {
    const response = await this.stmt(`SELECT update_id, targets_json, journal_json, status, updated_at
      FROM planner_template_updates WHERE installation_id = ? ORDER BY update_id`, id).all();
    return response.results.map(({ targets_json, journal_json, ...row }) => ({
      ...row, targets: JSON.parse(targets_json), journal: JSON.parse(journal_json),
    }));
  }
  // Updates share the installation's lease with daily sync and reauthorization,
  // while retaining the daily schedule, sync journal and paused state verbatim.
  async claimUpdate(id, owner) {
    const now = this.now(), lease = { id, owner, token: token(), until: now + LEASE_MS };
    const row = await this.stmt(`UPDATE planner_installations SET lease_token = ?, lease_until = ?
      WHERE id = ? AND owner_id = ? AND lease_until <= ? AND rotation_pending = 0
      RETURNING *`, lease.token, lease.until, id, owner, now).first();
    if (!row) {
      const previous = await this.get(id);
      if (!previous || previous.owner_id !== owner) throw new StoreError('installation_not_found', 404);
      if (previous.lease_until > now) throw new BusyError();
      throw new StoreError('update_reconnect', 409);
    }
    return { lease, doc: record(row) };
  }
  async assertUpdateLease(lease) {
    const row = await this.stmt(`SELECT id FROM planner_installations
      WHERE id = ? AND owner_id = ? AND lease_token = ? AND lease_until > ? AND rotation_pending = 0`,
    lease.id, lease.owner, lease.token, this.now()).first();
    if (!row) throw new LeaseError();
  }
  async saveUpdate(lease, updateId, targets, journal, status) {
    if (updateId !== UPDATE_ID || !UPDATE_STATUSES.has(status)) throw new StoreError('invalid_update');
    const serializedTargets = updateObject(targets, 4096), serializedJournal = updateObject(journal, 12000), now = this.now();
    const row = await this.stmt(`INSERT INTO planner_template_updates
      (installation_id, update_id, targets_json, journal_json, status, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS
        (SELECT 1 FROM planner_installations WHERE id = ? AND owner_id = ? AND lease_token = ? AND lease_until > ? AND rotation_pending = 0)
      ON CONFLICT(installation_id, update_id) DO UPDATE SET journal_json = excluded.journal_json,
        status = excluded.status, updated_at = excluded.updated_at
      WHERE planner_template_updates.targets_json = excluded.targets_json
      RETURNING update_id`, lease.id, updateId, serializedTargets, serializedJournal, status, now, now,
    lease.id, lease.owner, lease.token, now).first();
    if (!row) {
      // Distinguish stale leases from changed targets without allowing a retry
      // to redirect a journal to another database or property.
      await this.assertUpdateLease(lease);
      throw new StoreError('update_targets_conflict', 409);
    }
  }
  async releaseUpdate(lease) {
    await this.stmt(`UPDATE planner_installations SET lease_token = NULL, lease_until = 0
      WHERE id = ? AND lease_token = ?`, lease.id, lease.token).run();
  }
  async register(manifest, identity, encryptedTokens) {
    for (const key of ['owner_id', 'workspace_id', 'bot_id']) {
      if (typeof identity?.[key] !== 'string' || !identity[key] || identity[key].length > 128) throw new StoreError('invalid_identity');
    }
    if (!manifest?.root_page_id || typeof encryptedTokens !== 'string' || !encryptedTokens || encryptedTokens.length > 32_000) throw new StoreError('invalid_registration');
    const id = await installationId(identity.workspace_id, manifest.root_page_id);
    const now = this.now(), day = koreaDay(now), serialized = canonical(manifest);
    const row = await this.stmt(`INSERT INTO planner_installations
      (id, owner_id, workspace_id, bot_id, manifest_json, credentials, next_due, created_at, updated_at)
      SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
      WHERE (SELECT count(*) FROM planner_installations) < ? OR EXISTS (SELECT 1 FROM planner_installations WHERE id = ?)
      ON CONFLICT(id) DO UPDATE SET bot_id = excluded.bot_id, credentials = excluded.credentials,
        enabled = 1, status = 'waiting', immediate = 1, rotation_pending = 0, failures = 0,
        next_due = CASE WHEN planner_installations.last_success_day = ? THEN ? ELSE ? END,
        updated_at = excluded.updated_at
      WHERE planner_installations.owner_id = excluded.owner_id AND planner_installations.manifest_json = excluded.manifest_json
        AND planner_installations.lease_until <= ?
      RETURNING id`, id, identity.owner_id, identity.workspace_id, identity.bot_id, serialized,
    encryptedTokens, now, now, now, this.maxInstallations, id, day, nextMorning(now), now, now).first();
    if (!row) {
      const previous = await this.get(id);
      if (previous?.lease_until > now) throw new BusyError();
      throw new StoreError(previous ? 'registration_conflict' : 'installation_limit', 409);
    }
    return id;
  }
  async setEnabled(id, owner, enabled) {
    if (typeof enabled !== 'boolean') throw new StoreError('invalid_enabled');
    const now = this.now();
    const result = await this.stmt(`UPDATE planner_installations SET enabled = ?, status = ?, immediate = ?, failures = 0,
      next_due = CASE WHEN last_success_day = ? THEN ? ELSE ? END, updated_at = ?
      WHERE id = ? AND owner_id = ? AND lease_until <= ? RETURNING id`, enabled ? 1 : 0, enabled ? 'waiting' : 'paused',
    enabled ? 1 : 0, koreaDay(now), nextMorning(now), now, now, id, owner, now).first();
    if (!result) {
      const previous = await this.get(id);
      if (previous?.owner_id === owner && previous.lease_until > now) throw new BusyError();
      throw new StoreError('installation_not_found', 404);
    }
  }
  async claimGlobal() {
    const now = this.now(), day = koreaDay(now), lease = token();
    const row = await this.stmt(`UPDATE planner_scheduler SET lease_token = ?, lease_until = ?, budget_day = ?,
      steps = CASE WHEN budget_day = ? THEN steps ELSE 0 END
      WHERE singleton = 1 AND lease_until <= ? RETURNING *`, lease, now + LEASE_MS, day, day, now).first();
    return row ? { token: lease, day, until: now + LEASE_MS, steps: row.steps } : null;
  }
  async releaseGlobal(global) {
    await this.stmt('UPDATE planner_scheduler SET lease_token = NULL, lease_until = 0 WHERE singleton = 1 AND lease_token = ?', global.token).run();
  }
  async claimDue(global) {
    const now = this.now(), day = koreaDay(now);
    if (global.day !== day) throw new LeaseError();
    const candidate = await this.stmt(`SELECT id FROM planner_installations INDEXED BY planner_due
      WHERE enabled = 1 AND next_due <= ? AND lease_until <= ? AND (last_success_day IS NULL OR last_success_day != ?)
        AND (immediate = 1 OR run_day = ? OR ? >= ?)
      ORDER BY next_due, id LIMIT 1`, now, now, day, day, now, morning(day)).first();
    if (!candidate) return null;
    const lease = { token: token(), global: global.token, day, until: now + LEASE_MS, id: candidate.id };
    const results = await this.db.batch([
      this.stmt(`UPDATE planner_installations SET lease_token = ?, lease_until = ?, run_day = ?, immediate = 0,
        failures = CASE WHEN run_day = ? THEN failures ELSE 0 END,
        status = 'syncing', updated_at = ? WHERE id = ? AND enabled = 1 AND next_due <= ? AND lease_until <= ?
        AND (last_success_day IS NULL OR last_success_day != ?) AND EXISTS
        (SELECT 1 FROM planner_scheduler WHERE singleton = 1 AND lease_token = ? AND lease_until > ? AND budget_day = ? AND steps < ?)
        RETURNING *`, lease.token, lease.until, day, day, now, lease.id, now, now, day, global.token, now, day, this.maxDailySteps),
      this.stmt(`UPDATE planner_scheduler SET steps = steps + 1 WHERE singleton = 1 AND lease_token = ? AND lease_until > ?
        AND budget_day = ? AND steps < ? AND EXISTS
        (SELECT 1 FROM planner_installations WHERE id = ? AND lease_token = ?)`, global.token, now, day, this.maxDailySteps, lease.id, lease.token),
    ]);
    const row = results[0].results?.[0];
    if (row) return { lease, doc: record(row) };
    // Budget exhaustion changes eligibility, never last_success_day or pending state.
    await this.stmt(`UPDATE planner_installations SET next_due = ?, status = 'quota_wait', updated_at = ?
      WHERE id = ? AND enabled = 1 AND lease_until <= ? AND EXISTS
      (SELECT 1 FROM planner_scheduler WHERE singleton = 1 AND lease_token = ? AND lease_until > ? AND steps >= ?)`,
    morning(day) + DAY_MS, now, lease.id, now, global.token, now, this.maxDailySteps).run();
    return null;
  }
  async fenced(lease, fields) {
    const allowed = new Set(['state_json', 'credentials', 'rotation_pending', 'enabled', 'status', 'next_due',
      'last_success_day', 'last_success_at', 'failures', 'lease_token', 'lease_until']);
    if (!Object.keys(fields).length || Object.keys(fields).some(key => !allowed.has(key))) throw new StoreError('invalid_update');
    const now = this.now();
    if (koreaDay(now) !== lease.day) throw new LeaseError();
    const entries = Object.entries(fields);
    const row = await this.stmt(`UPDATE planner_installations SET ${entries.map(([key]) => key + ' = ?').join(', ')}, updated_at = ?
      WHERE id = ? AND enabled = 1 AND lease_token = ? AND lease_until > ? AND run_day = ? AND EXISTS
      (SELECT 1 FROM planner_scheduler WHERE singleton = 1 AND lease_token = ? AND lease_until > ?) RETURNING id`,
    ...entries.map(([, value]) => value), now, lease.id, lease.token, now, lease.day, lease.global, now).first();
    if (!row) throw new LeaseError();
  }
  async saveState(lease, state) { await this.fenced(lease, { state_json: json(state, 600_000) }); }
  async beginRotation(lease) { await this.fenced(lease, { rotation_pending: 1 }); }
  async saveCredentials(lease, credentials) {
    if (typeof credentials !== 'string' || !credentials || credentials.length > 32_000) throw new StoreError('invalid_credentials');
    await this.fenced(lease, { credentials, rotation_pending: 0 });
  }
  async finish(lease, { done = false, status = done ? 'active' : 'waiting', delay = 60_000, failures = 0, disable = false } = {}) {
    const now = this.now();
    await this.fenced(lease, { status, failures, enabled: disable ? 0 : 1,
      next_due: done ? nextMorning(now) : now + delay, lease_token: null, lease_until: 0,
      ...(done ? { last_success_day: lease.day, last_success_at: now } : {}) });
  }
  async cacheGet(key) {
    const row = await this.stmt('SELECT snapshot_json FROM planner_school_cache WHERE cache_key = ? AND expires_at > ?', key, this.now()).first();
    return row ? JSON.parse(row.snapshot_json) : null;
  }
  async cachePut(key, snapshot) {
    await this.stmt(`INSERT INTO planner_school_cache(cache_key, snapshot_json, expires_at) VALUES (?, ?, ?)
      ON CONFLICT(cache_key) DO UPDATE SET snapshot_json = excluded.snapshot_json, expires_at = excluded.expires_at`,
    key, json(snapshot, 1_200_000), this.now() + DAY_MS * 2).run();
  }
  async cleanup(limitCount = 4) {
    await this.stmt(`DELETE FROM planner_school_cache WHERE cache_key IN
      (SELECT cache_key FROM planner_school_cache WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)`,
    this.now(), limit(limitCount, 4, 20)).run();
  }
}
export function createStore(db, options) { return new Store(db, options); }
