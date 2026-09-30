const utf8 = new TextEncoder();
const decode = new TextDecoder('utf-8', {fatal:true});
export class ConnectionError extends Error {
  constructor(code = 'connection', status = 400) { super(code); this.code = code; this.status = status; }
}
export class RetryableConnectionError extends ConnectionError {
  constructor() { super('oauth_unavailable', 503); this.retryable = true; }
}
const PROVIDER_OAUTH_ERRORS = Object.freeze(['invalid_request','invalid_client','invalid_grant',
  'unauthorized_client','unsupported_grant_type','invalid_scope','access_denied','test_env_error',
  'missing_version','validation_error','restricted_resource']);
const EXCHANGE_DIAGNOSTICS = Object.freeze([...PROVIDER_OAUTH_ERRORS,'transport','http_error',
  'rate_limited','server_error','invalid_response','invalid_access_token','invalid_refresh_token']);
function exchangeFailure(diagnostic, retryable = false) {
  const error = retryable ? new RetryableConnectionError() : new ConnectionError('reconnect');
  error.exchangeCode = diagnostic;
  return error;
}
export function exchangeDiagnostic(error) {
  return error instanceof ConnectionError && EXCHANGE_DIAGNOSTICS.includes(error.exchangeCode)
    ? 'token_exchange_'+error.exchangeCode : null;
}
export function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
export function unbase64url(value) {
  if (typeof value !== 'string' || !/^[\w-]+={0,2}$/.test(value) || value.length > 30000) throw new ConnectionError();
  try { return Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')), c => c.charCodeAt(0)); }
  catch { throw new ConnectionError(); }
}
export const nonce = () => base64url(crypto.getRandomValues(new Uint8Array(24)));
export function origin(value) {
  try {
    if (typeof value !== 'string' || /[\s\\?#]/.test(value)) throw new Error();
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || value !== url.origin ||
        !url.hostname.includes('.') || /(?:^|\.)(?:localhost|local|internal)$/.test(url.hostname) ||
        /^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) || url.hostname.includes(':') || url.port) throw new Error();
    return url.origin;
  } catch { throw new ConnectionError('configuration'); }
}
export class TokenBox {
  constructor(key, now = () => Date.now()) {
    const raw = unbase64url(key);
    if (raw.length !== 32) throw new ConnectionError('configuration');
    this.key = crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt','decrypt']);
    this.now = now;
  }
  async seal(purpose, payload) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = utf8.encode(JSON.stringify({iat:Math.floor(this.now()/1000), payload}));
    if (data.length > 12000) throw new ConnectionError();
    const encrypted = await crypto.subtle.encrypt({name:'AES-GCM', iv, additionalData:utf8.encode('teacher-planner:v1:'+purpose)}, await this.key, data);
    return 'v1.' + base64url(iv) + '.' + base64url(new Uint8Array(encrypted));
  }
  async open(purpose, value, ttl = null) {
    try {
      if (typeof value !== 'string' || value.length > 20000) throw new Error();
      const [version, iv, body, extra] = value.split('.');
      if (version !== 'v1' || extra !== undefined) throw new Error();
      const plain = await crypto.subtle.decrypt({name:'AES-GCM', iv:unbase64url(iv), additionalData:utf8.encode('teacher-planner:v1:'+purpose)}, await this.key, unbase64url(body));
      const result = JSON.parse(decode.decode(plain));
      const age = Math.floor(this.now()/1000) - result.iat;
      if (!Number.isInteger(result.iat) || age < -30 || (ttl !== null && age > ttl) || !result.payload || typeof result.payload !== 'object') throw new Error();
      return result.payload;
    } catch { throw new ConnectionError('session'); }
  }
}
export async function readJSON(response, maximum = 65536) {
  const reader = response.body?.getReader();
  if (!reader) throw new ConnectionError();
  const chunks = []; let length = 0;
  try {
    while (true) {
      const {done,value} = await reader.read(); if (done) break;
      length += value.length;
      if (length > maximum) throw new ConnectionError('size');
      chunks.push(value);
    }
    const data = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) {data.set(chunk,offset); offset += chunk.length;}
    return JSON.parse(decode.decode(data));
  } finally { await reader.cancel().catch(() => {}); }
}
export async function exchange(clientId, secret, body, fetchFn = fetch) {
  if (!clientId || !secret) throw new ConnectionError('configuration');
  let response;
  try {
    response = await fetchFn('https://api.notion.com/v1/oauth/token', {
      method:'POST', redirect:'error', signal:AbortSignal.timeout(15000),
      headers:{'Authorization':'Basic '+btoa(clientId+':'+secret),'Content-Type':'application/json','Notion-Version':'2026-03-11'},
      body:JSON.stringify(body)
    });
  } catch { throw exchangeFailure('transport'); }
  if (response.status === 429 || response.status >= 500) {
    try { await response.body?.cancel(); } catch { /* The definite HTTP status remains known. */ }
    throw exchangeFailure(response.status === 429 ? 'rate_limited' : 'server_error',true);
  }
  if (!response.ok) {
    let diagnostic = 'http_error';
    try {
      const failure = await readJSON(response,8192);
      // OAuth uses error; newer Notion responses use code. Never retain descriptions.
      const code = [failure?.error,failure?.code].find(value=>PROVIDER_OAUTH_ERRORS.includes(value));
      if (code) diagnostic = code;
    } catch { /* Empty, malformed or oversized responses retain the fixed fallback. */ }
    throw exchangeFailure(diagnostic);
  }
  let value;
  try { value = await readJSON(response); } catch { throw exchangeFailure('invalid_response'); }
  const token = input => typeof input === 'string' && input.length > 0 && input.length <= 4096;
  if (!value || Array.isArray(value) || !token(value.access_token)) throw exchangeFailure('invalid_access_token');
  // The current official response schema permits a null refresh_token. Older
  // code exchanges can omit it. A refresh grant must return a fresh valid pair.
  // https://developers.notion.com/reference/create-a-token
  if (!(body?.grant_type === 'authorization_code' && value.refresh_token == null) && !token(value.refresh_token)) throw exchangeFailure('invalid_refresh_token');
  return value;
}
export function validateManifest(input) {
  const ids = ['root_page_id','agenda_data_source_id','meals_block_id','status_block_id'];
  const keys = ['version','office_code','school_code','school_name','academic_year',...ids];
  if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).length !== keys.length || keys.some(k => !Object.hasOwn(input,k))) throw new ConnectionError();
  if (input.version !== 1 || typeof input.office_code !== 'string' || !/^[A-Z]\d{2}$/.test(input.office_code) || typeof input.school_code !== 'string' || !/^\d{7}$/.test(input.school_code) || !Number.isInteger(input.academic_year) || input.academic_year < 1900 || input.academic_year > 9998) throw new ConnectionError();
  const name = input.school_name;
  if (typeof name !== 'string' || !name.trim() || [...name].length > 200 || /[\p{C}<>]/u.test(name) || /:\/\/|bearer\s|(?:ntn|nrt|secret|sk)[_-]|(?:api[_-]?key|access[_-]?token|password)\s*[:=]/i.test(name)) throw new ConnectionError();
  const result = {...input, school_name:name.trim()};
  for (const key of ids) {
    const value = input[key];
    if (typeof value !== 'string' || !/^(?:[a-f\d]{32}|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})$/i.test(value)) throw new ConnectionError();
    const raw = value.replaceAll('-','').toLowerCase();
    if (/^0+$/.test(raw)) throw new ConnectionError();
    result[key] = raw.replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/,'$1-$2-$3-$4-$5');
  }
  if (new Set(ids.map(k => result[k])).size !== ids.length || utf8.encode(JSON.stringify(result)).length > 4096) throw new ConnectionError();
  return result;
}
