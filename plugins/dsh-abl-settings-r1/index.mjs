// dsh-abl-settings-r1 — server half (Cordis plugin, object form).
//
// The ONE writer of ABL settings. Odysseus, the autoresponder, the morning briefing and
// the desktop (via its pull) only READ settings.json (src/abl_settings.py on the Python side).
//
// Routes (exact, under /api so they ride dsh sign-in through the shared authenticated
// handler, same as dsh-abl-console-r1's /api/abl-console/read):
//   GET  /api/abl-settings/read          → {ok, version, updated_at, values, effective, source, schema, sections}
//   POST /api/abl-settings/write         ← {version, set:{key:value}, unset:[key]}
//                                        → 200 new doc | 409 version_conflict | 422 invalid
//   POST /api/abl-settings/undo          ← {version}  restores settings.json.prev (a new version, audited)
//   GET  /api/abl-settings/audit?limit=N → last N audit records (newest first)
//
// File discipline: write tmp → fsync → rename (atomic on POSIX); the previous file is kept
// as settings.json.prev; every change appends one JSONL line to settings_audit.jsonl.
// Writes are serialised in-process, and optimistic concurrency (version) protects two tabs.

import { appendFile, mkdir, open, readFile, rename, copyFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { SECTIONS, TIERS, TIER_FLOORS, buildSchema, validateField } from './schema.mjs'

export const name = 'dsh-abl-settings-r1'
export const inject = ['connection']

const ROUTE = '/api/abl-settings'
const DEFAULT_PATH = join(homedir(), 'abl', 'odysseus', 'data', 'abl', 'settings.json')

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  })
}

export class SettingsStore {
  constructor(path, schemaOpts = {}) {
    this.path = path
    this.prevPath = `${path}.prev`
    this.auditPath = join(dirname(path), 'settings_audit.jsonl')
    this.schema = buildSchema(schemaOpts)
    this.fields = new Map(this.schema.map(f => [f.key, f]))
    this._chain = Promise.resolve()          // serialises writes
  }

  async load() {
    try {
      const doc = JSON.parse(await readFile(this.path, 'utf8'))
      if (!doc || typeof doc !== 'object' || typeof doc.values !== 'object') throw new Error('bad shape')
      return { version: Number(doc.version) || 0, updated_at: doc.updated_at ?? null, updated_by: doc.updated_by ?? null, values: doc.values }
    } catch (e) {
      if (e.code === 'ENOENT') return { version: 0, updated_at: null, updated_by: null, values: {} }
      // Corrupt file: never silently reset. Report it; reads fall back to defaults.
      return { version: -1, updated_at: null, updated_by: null, values: {}, error: `settings.json unreadable: ${e.message}` }
    }
  }

  /** Resolved value per key: file value (if valid) else default; floors re-applied. */
  effective(values) {
    const out = {}, source = {}
    for (const f of this.schema) {
      const v = values[f.key]
      if (v !== undefined && validateField(f, v) === null) { out[f.key] = v; source[f.key] = 'settings' }
      else { out[f.key] = f.default; source[f.key] = v === undefined ? 'default' : 'default (stored value invalid)' }
    }
    for (const [cat, floor] of Object.entries(TIER_FLOORS)) {     // belt and braces vs hand edits
      const k = `alerts.tiers.${cat}`
      if (TIERS.indexOf(out[k]) < TIERS.indexOf(floor)) { out[k] = floor; source[k] = 'floor' }
    }
    return { effective: out, source }
  }

  async read() {
    const doc = await this.load()
    const { effective, source } = this.effective(doc.values)
    return { ok: !doc.error, error: doc.error, path: this.path, ...doc, effective, source, schema: this.schema, sections: SECTIONS }
  }

  validate(set = {}, unset = []) {
    const errors = {}
    if (typeof set !== 'object' || set === null || Array.isArray(set)) return { _: 'set must be an object' }
    if (!Array.isArray(unset)) return { _: 'unset must be a list' }
    for (const [k, v] of Object.entries(set)) {
      const f = this.fields.get(k)
      if (!f) { errors[k] = 'unknown setting'; continue }
      const err = validateField(f, v)
      if (err) errors[k] = err
    }
    for (const k of unset) if (!this.fields.has(k)) errors[k] = 'unknown setting'
    // Cross-field rule: live mode needs at least one channel to be meaningful.
    const mode = set['autoresponder.mode']
    if (mode === 'live' && Array.isArray(set['autoresponder.autosend_channels']) && set['autoresponder.autosend_channels'].length === 0) {
      errors['autoresponder.autosend_channels'] = 'live mode needs at least one channel'
    }
    return errors
  }

  write(body, who = 'owner') {
    const run = async () => {
      const cur = await this.load()
      if (cur.error) return { status: 503, body: { ok: false, error: cur.error, hint: 'fix or move settings.json; writes are refused rather than overwrite it' } }
      if (body?.version !== cur.version) return { status: 409, body: { ok: false, error: 'version_conflict', current: cur.version } }
      const set = body.set ?? {}, unset = body.unset ?? []
      const errors = this.validate(set, unset)
      if (Object.keys(errors).length) return { status: 422, body: { ok: false, error: 'invalid', fields: errors } }
      const values = { ...cur.values }
      const changes = []
      for (const [k, v] of Object.entries(set)) {
        if (JSON.stringify(values[k]) !== JSON.stringify(v)) changes.push({ key: k, old: values[k] ?? null, new: v })
        values[k] = v
      }
      for (const k of unset) {
        if (k in values) { changes.push({ key: k, old: values[k], new: null }); delete values[k] }
      }
      if (!changes.length) return { status: 200, body: { ...(await this.read()), changed: [] } }
      const doc = { version: cur.version + 1, updated_at: new Date().toISOString(), updated_by: who, values }
      await this._persist(doc, cur.version > 0)
      await this._audit(changes.map(c => ({ ...c, version: doc.version, by: who })))
      return { status: 200, body: { ...(await this.read()), changed: changes.map(c => c.key) } }
    }
    const p = this._chain.then(run, run)
    this._chain = p.catch(() => {})
    return p
  }

  undo(body, who = 'owner') {
    const run = async () => {
      const cur = await this.load()
      if (body?.version !== cur.version) return { status: 409, body: { ok: false, error: 'version_conflict', current: cur.version } }
      let prev
      try { prev = JSON.parse(await readFile(this.prevPath, 'utf8')) } catch { return { status: 404, body: { ok: false, error: 'nothing to undo' } } }
      const values = prev.values ?? {}
      const keys = new Set([...Object.keys(values), ...Object.keys(cur.values)])
      const changes = [...keys].filter(k => JSON.stringify(values[k]) !== JSON.stringify(cur.values[k]))
        .map(k => ({ key: k, old: cur.values[k] ?? null, new: values[k] ?? null }))
      const doc = { version: cur.version + 1, updated_at: new Date().toISOString(), updated_by: `${who} (undo)`, values }
      await this._persist(doc, true)
      await this._audit(changes.map(c => ({ ...c, version: doc.version, by: `${who} (undo)` })))
      return { status: 200, body: { ...(await this.read()), changed: changes.map(c => c.key) } }
    }
    const p = this._chain.then(run, run)
    this._chain = p.catch(() => {})
    return p
  }

  async _persist(doc, keepPrev) {
    await mkdir(dirname(this.path), { recursive: true })
    if (keepPrev) { try { await copyFile(this.path, this.prevPath) } catch { /* first write */ } }
    const tmp = `${this.path}.tmp-${process.pid}`
    const fh = await open(tmp, 'w', 0o600)
    try { await fh.writeFile(JSON.stringify(doc, null, 2) + '\n'); await fh.sync() } finally { await fh.close() }
    await rename(tmp, this.path)
  }

  async _audit(records) {
    const ts = new Date().toISOString()
    await appendFile(this.auditPath, records.map(r => JSON.stringify({ ts, ...r }) + '\n').join(''))
  }

  async audit(limit = 20) {
    try {
      const lines = (await readFile(this.auditPath, 'utf8')).trim().split('\n').filter(Boolean)
      return lines.slice(-limit).reverse().map(l => { try { return JSON.parse(l) } catch { return { raw: l } } })
    } catch { return [] }
  }
}

async function readBody(request) {
  try { return await request.json() } catch { return null }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{settingsPath?: string, autopilotSystems?: string[], channels?: string[], modelOptions?: string[], lightOptions?: string[], voices?: string[]}} config
 */
export function apply(ctx, config = {}) {
  const store = new SettingsStore(config.settingsPath || process.env.ABL_SETTINGS_PATH || DEFAULT_PATH, config)
  const reg = (path, methods, handler) => ctx.connection.fetch.register({ path: `${ROUTE}/${path}`, methods, requestBody: 'buffered', fetch: handler })

  const guard = fn => async (request) => {
    try { return await fn(request) } catch (e) {
      ctx.logger?.('abl-settings')?.warn?.(e)
      return json(500, { ok: false, error: String(e?.message ?? e) })
    }
  }
  // Disposers are returned to Cordis via ctx.effect so routes vanish on unload/HMR.
  const routes = [
    reg('read', ['GET'], guard(async () => json(200, await store.read()))),
    reg('write', ['POST'], guard(async (req) => {
      const body = await readBody(req)
      if (!body) return json(400, { ok: false, error: 'JSON body required: {version, set, unset}' })
      const r = await store.write(body)
      return json(r.status, r.body)
    })),
    reg('undo', ['POST'], guard(async (req) => {
      const r = await store.undo((await readBody(req)) ?? {})
      return json(r.status, r.body)
    })),
    reg('audit', ['GET'], guard(async (req) => {
      const n = Math.max(1, Math.min(200, Number(new URL(req.url).searchParams.get('limit')) || 20))
      return json(200, { ok: true, records: await store.audit(n) })
    })),
  ]
  if (typeof ctx.effect === 'function') ctx.effect(() => () => routes.forEach(d => { try { d() } catch {} }))
  return store
}

export default { name, inject, apply }
