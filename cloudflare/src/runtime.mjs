import { createStore, LeaseError, morning, nextMorning } from './store.mjs';
import { base64url, origin } from './security.mjs';
import { createNotionClient, fetchSchoolSnapshot, syncStep } from './sync.mjs';

const encoder = new TextEncoder();
const MAX_FAILURES = 3;
class ReconnectError extends Error { constructor() { super('reconnect'); this.code = 'reconnect'; } }
function retryable(error) { return error?.retryable === true || error?.status === 429 || error?.status >= 500; }

export async function schoolCacheKey(manifest, day) {
  const source = JSON.stringify([manifest.office_code, manifest.school_code, manifest.academic_year, day]);
  return base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(source))));
}

async function updateStatus(notion, manifest, day, now, publicUrl, checkpoint) {
  const link = origin(publicUrl) + '/connect#' + base64url(encoder.encode(JSON.stringify(manifest)));
  await checkpoint();
  await notion.request('PATCH', '/blocks/' + manifest.status_block_id, { callout: { rich_text: [
    { type: 'text', text: { content: `자동 갱신 · 매일 오전 7시부터 순차 실행 (한국 시간)\n${day} 급식·학사일정 반영 완료\n확인: ${new Date(now + 9 * 3_600_000).toISOString().slice(0, 16).replace('T', ' ')}\n` } },
    { type: 'text', text: { content: '자동 갱신 연결·관리', link: { url: link } } },
  ] } });
}

// A Cron delivery handles at most one installation step. No paid Queue or DO.
export async function runCron(env, dependencies = {}) {
  const now = dependencies.now || Date.now;
  const store = dependencies.store || createStore(env.DB, {
    maxInstallations: env.MAX_INSTALLATIONS ?? 50, maxDailySteps: env.MAX_DAILY_STEPS ?? 1000, now,
  });
  const clientFactory = dependencies.createNotionClient || createNotionClient;
  const fetchSnapshot = dependencies.fetchSchoolSnapshot || fetchSchoolSnapshot;
  const step = dependencies.syncStep || syncStep;
  const { encrypt, decrypt, exchangeTokens } = dependencies;
  if (![encrypt, decrypt, exchangeTokens].every(value => typeof value === 'function')) throw new Error('runtime_configuration');
  let global, work, tokens, rotated = false, state, rotateTokens;
  try {
    global = await store.claimGlobal();
    if (!global) return { status: 'busy' };
    await store.cleanup();
    work = await store.claimDue(global);
    if (!work) return { status: 'idle_or_budget' };
    const { lease, doc } = work;
    const day = lease.day;
    if (day >= `${doc.manifest.academic_year + 1}-03-01`) {
      await store.finish(lease, { status: 'academic_year_ended', disable: true });
      return { status: 'academic_year_ended' };
    }
    if (day < `${doc.manifest.academic_year}-03-01`) {
      await store.finish(lease, { status: 'waiting', delay: morning(`${doc.manifest.academic_year}-03-01`) - now() });
      return { status: 'future_academic_year' };
    }
    if (doc.rotation_pending) throw new ReconnectError();
    try { tokens = await decrypt(doc.credentials); } catch { throw new ReconnectError(); }
    if (typeof tokens?.access_token !== 'string' || !tokens.access_token) throw new ReconnectError();

    rotateTokens = async () => {
      if (typeof tokens?.refresh_token !== 'string' || !tokens.refresh_token) throw new ReconnectError();
      await store.beginRotation(lease);
      let replacement;
      try { replacement = await exchangeTokens(tokens.refresh_token); }
      catch (error) {
        // Only a definite throttled/server response proves no usable pair was returned.
        // An unknown response must retain rotation_pending and require reauthorization.
        if (error?.status === 429 || error?.status >= 500) {
          await store.fenced(lease, { rotation_pending: 0 });
          throw error;
        }
        throw new ReconnectError();
      }
      if (typeof replacement?.access_token !== 'string' || !replacement.access_token ||
          typeof replacement?.refresh_token !== 'string' || !replacement.refresh_token) throw new ReconnectError();
      const saved = { access_token: replacement.access_token, refresh_token: replacement.refresh_token };
      if (Number.isFinite(replacement.expires_in) && replacement.expires_in > 0) saved.expires_at = now() + replacement.expires_in * 1000;
      try { await store.saveCredentials(lease, await encrypt(saved)); }
      catch (error) { if (error instanceof LeaseError) throw error; throw new ReconnectError(); }
      tokens = saved; rotated = true;
    };
    // Retain unexpired/non-expiring access tokens; do not rotate every minute.
    if (Number.isFinite(tokens.expires_at) && tokens.expires_at <= now() + 60_000) await rotateTokens();
    const key = await schoolCacheKey(doc.manifest, day);
    let snapshot = await store.cacheGet(key);
    if (!snapshot) {
      snapshot = await fetchSnapshot(doc.manifest, day, env.NEIS_API_KEY);
      await store.cachePut(key, snapshot);
    }
    const notion = clientFactory(tokens.access_token);
    state = structuredClone(doc.state);
    const saveState = async next => {
      await store.saveState(lease, next);
      state = structuredClone(next);
    };
    const result = await step({ notion, manifest: doc.manifest, snapshot, state, saveState, day });
    if (typeof result?.done !== 'boolean') throw new Error('invalid_step_result');
    await saveState(result.state ?? state);
    if (result.done) await updateStatus(notion, doc.manifest, day, now(), env.PUBLIC_BASE_URL, () => store.saveState(lease, state));
    // Cron supplies the cadence. Adding 60 seconds after completion would skip
    // the next minute when this step finishes a few seconds after its trigger.
    await store.finish(lease, { done: result.done, delay: 0 });
    return { status: result.done ? 'complete' : 'continued' };
  } catch (error) {
    if (error instanceof LeaseError) return { status: 'lease_lost' };
    if (!work) return { status: 'storage_unavailable' };
    const { lease, doc } = work;
    try {
      if (error?.status === 401) {
        try {
          if (rotated || !rotateTokens || doc.failures >= MAX_FAILURES - 1) throw new ReconnectError();
          await rotateTokens();
          // Retry the durable pending operation in a later invocation, with a
          // fresh bounded Notion request budget.
          await store.finish(lease, { status: 'retrying', failures: doc.failures + 1 });
          return { status: 'token_refreshed' };
        } catch (rotationError) { error = rotationError; }
      }
      if (error?.code === 'reconnect') {
        await store.finish(lease, { status: 'reconnect', disable: true });
        return { status: 'reconnect' };
      }
      if (retryable(error)) {
        const failures = doc.failures + 1;
        await store.finish(lease, { status: failures >= MAX_FAILURES ? 'attention' : 'retrying', failures,
          delay: failures >= MAX_FAILURES ? nextMorning(now()) - now() : Math.min(900_000, 60_000 * 2 ** (failures - 1)) });
        return { status: failures >= MAX_FAILURES ? 'retry_tomorrow' : 'retrying' };
      }
      await store.finish(lease, { status: 'attention', disable: true });
      return { status: 'attention' };
    } catch { return { status: 'storage_unavailable' }; }
  } finally {
    if (global) {
      try { await store.releaseGlobal(global); } catch { /* A bounded lease expires without unsafe reuse. */ }
    }
  }
}
