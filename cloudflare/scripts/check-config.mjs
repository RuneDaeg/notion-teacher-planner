// The private deployment file is ordinary JSON (also accepted as JSONC by Wrangler).
import {readFileSync} from 'node:fs';
import {origin} from '../src/security.mjs';
try {
  const config = JSON.parse(readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8'));
  const base = origin(config.vars?.PUBLIC_BASE_URL);
  const realId = id => typeof id === 'string' && /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(id) && !/^0+$/.test(id.replaceAll('-',''));
  if (base.includes('.invalid') || !realId(config.vars?.NOTION_CLIENT_ID) ||
      !config.d1_databases?.some(d => d.binding === 'DB' && realId(d.database_id))) throw new Error();
  for (const key of ['NEIS_API_KEY','NOTION_CLIENT_SECRET','TOKEN_ENCRYPTION_KEY']) if (key in config.vars) throw new Error();
  for (const [key,max] of [['MAX_INSTALLATIONS',5000],['MAX_DAILY_STEPS',10000]]) if (!/^[1-9]\d{0,5}$/.test(String(config.vars[key])) || Number(config.vars[key])>max) throw new Error();
  console.log('Deployment identifiers and public settings are configured. Remote plan, secrets and OAuth still require verification.');
} catch {
  console.error('Prepare ignored cloudflare/wrangler.jsonc with the real D1 ID, HTTPS origin, Notion client ID and limits. Store secrets only with wrangler secret put.');
  process.exitCode = 1;
}
