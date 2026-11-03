// node --test plugins/dsh-abl-settings-r1/tests/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, writeFile, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import plugin, { apply, name, inject } from '../index.mjs'
import { buildSchema } from '../schema.mjs'

async function mount(extra = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'abls-'))
  const routes = new Map()
  const disposers = []
  const ctx = {
    connection: { fetch: { register(r) {
      assert.match(r.path, /^\/api\//, 'exact Fetch routes must live under /api')
      assert.ok(r.methods.length > 0 && r.requestBody === 'buffered')
      assert.ok(!routes.has(r.path), 'duplicate route')
      routes.set(r.path, r)
      return () => routes.delete(r.path)
    } } },
    effect(fn) { disposers.push(fn()) },
  }
  const path = join(dir, 'settings.json')
  apply(ctx, { settingsPath: path, ...extra })
  const callRoute = async (method, p, body) => {
    const [pathname] = p.split('?')
    const r = routes.get(`/api/abl-settings/${pathname}`)
    assert.ok(r, `route ${pathname} registered`)
    assert.ok(r.methods.includes(method), `${method} allowed on ${pathname}`)
    const res = await r.fetch(new Request(`http://host/api/abl-settings/${p}`, {
      method, body: body === undefined ? undefined : JSON.stringify(body), headers: { 'content-type': 'application/json' },
    }))
    return { status: res.status, body: await res.json() }
  }
  return { dir, path, routes, disposers, call: callRoute }
}

test('plugin shape matches the Cordis object form used by dsh-abl-console-r1', () => {
  assert.equal(name, 'dsh-abl-settings-r1')
  assert.deepEqual(inject, ['connection'])
  assert.equal(plugin.apply, apply)
})

test('registers exactly the four routes and disposes them on unload', async () => {
  const m = await mount()
  assert.deepEqual([...m.routes.keys()].sort(),
    ['/api/abl-settings/audit', '/api/abl-settings/read', '/api/abl-settings/undo', '/api/abl-settings/write'])
  m.disposers.forEach(d => d())
  assert.equal(m.routes.size, 0)
})

test('fresh install: version 0, every value is the default, nothing on disk', async () => {
  const m = await mount()
  const r = await m.call('GET', 'read')
  assert.equal(r.status, 200)
  assert.equal(r.body.ok, true)
  assert.equal(r.body.version, 0)
  assert.equal(r.body.effective['quiet.start'], '23:00')
  assert.equal(r.body.effective['alerts.tiers.bills'], 'YELLOW')
  assert.ok(Object.values(r.body.source).every(s => s === 'default'))
  await assert.rejects(stat(m.path))
})

test('write → version bump, atomic file, audit line; second tab gets 409', async () => {
  const m = await mount()
  const w = await m.call('POST', 'write', { version: 0, set: { 'quiet.start': '22:00', 'speaker.volume': 55 } })
  assert.equal(w.status, 200)
  assert.equal(w.body.version, 1)
  assert.deepEqual(w.body.changed.sort(), ['quiet.start', 'speaker.volume'])
  assert.equal(w.body.source['quiet.start'], 'settings')
  const onDisk = JSON.parse(await readFile(m.path, 'utf8'))
  assert.deepEqual(onDisk.values, { 'quiet.start': '22:00', 'speaker.volume': 55 })
  assert.equal((await stat(m.path)).mode & 0o777, 0o600)
  const stale = await m.call('POST', 'write', { version: 0, set: { 'quiet.end': '08:00' } })
  assert.equal(stale.status, 409)
  assert.equal(stale.body.current, 1)
  const audit = await m.call('GET', 'audit?limit=5')
  assert.equal(audit.body.records.length, 2)
  assert.equal(audit.body.records[0].version, 1)
})

test('guardrails: fraud/security locked RED, money ≥ YELLOW, types, unknown keys → 422 and nothing written', async () => {
  const m = await mount()
  const bad = await m.call('POST', 'write', { version: 0, set: {
    'alerts.tiers.fraud': 'GREEN', 'alerts.tiers.security': 'YELLOW', 'alerts.tiers.money': 'GREEN',
    'quiet.start': '25:00', 'speaker.volume': 101, 'speaker.rate': 2, 'quiet.tz': 'not a zone!',
    'models.triage_chain': ['local', 'local'], 'autoresponder.autosend_channels': ['carrier-pigeon'], 'nope.key': 1,
  } })
  assert.equal(bad.status, 422)
  const f = bad.body.fields
  assert.match(f['alerts.tiers.fraud'], /locked at RED/)
  assert.match(f['alerts.tiers.security'], /locked at RED/)
  assert.match(f['alerts.tiers.money'], /lower than YELLOW/)
  for (const k of ['quiet.start', 'speaker.volume', 'speaker.rate', 'quiet.tz', 'models.triage_chain', 'autoresponder.autosend_channels', 'nope.key']) {
    assert.ok(f[k], `expected error for ${k}`)
  }
  await assert.rejects(stat(m.path))
  const ok = await m.call('POST', 'write', { version: 0, set: { 'alerts.tiers.money': 'YELLOW', 'alerts.tiers.bills': 'RED' } })
  assert.equal(ok.status, 200)
})

test('live mode with an explicitly empty channel list is rejected', async () => {
  const m = await mount()
  const r = await m.call('POST', 'write', { version: 0, set: { 'autoresponder.mode': 'live', 'autoresponder.autosend_channels': [] } })
  assert.equal(r.status, 422)
  assert.match(r.body.fields['autoresponder.autosend_channels'], /at least one channel/)
})

test('hand-edited file below a floor is re-floored on read', async () => {
  const m = await mount()
  await writeFile(m.path, JSON.stringify({ version: 3, values: { 'alerts.tiers.fraud': 'GREEN', 'alerts.tiers.money': 'GREEN', 'speaker.volume': 999 } }))
  const r = await m.call('GET', 'read')
  assert.equal(r.body.effective['alerts.tiers.fraud'], 'RED')
  assert.equal(r.body.effective['alerts.tiers.money'], 'RED')         // invalid stored value → default (RED)
  assert.equal(r.body.effective['speaker.volume'], 70)
  assert.match(r.body.source['speaker.volume'], /invalid/)
})

test('corrupt settings.json: read reports it, writes refused (503), file untouched', async () => {
  const m = await mount()
  await writeFile(m.path, '{ not json')
  const r = await m.call('GET', 'read')
  assert.equal(r.body.ok, false)
  assert.match(r.body.error, /unreadable/)
  const w = await m.call('POST', 'write', { version: -1, set: { 'speaker.volume': 10 } })
  assert.equal(w.status, 503)
  assert.equal(await readFile(m.path, 'utf8'), '{ not json')
})

test('unset returns a key to its default; no-op write does not bump the version', async () => {
  const m = await mount()
  await m.call('POST', 'write', { version: 0, set: { 'speaker.volume': 10 } })
  const same = await m.call('POST', 'write', { version: 1, set: { 'speaker.volume': 10 } })
  assert.equal(same.body.version, 1)
  const u = await m.call('POST', 'write', { version: 1, unset: ['speaker.volume'] })
  assert.equal(u.body.version, 2)
  assert.equal(u.body.effective['speaker.volume'], 70)
  assert.equal(u.body.source['speaker.volume'], 'default')
})

test('undo restores the previous file as a new audited version', async () => {
  const m = await mount()
  await m.call('POST', 'write', { version: 0, set: { 'quiet.end': '08:00' } })
  await m.call('POST', 'write', { version: 1, set: { 'quiet.end': '07:00' } })
  const u = await m.call('POST', 'undo', { version: 2 })
  assert.equal(u.status, 200)
  assert.equal(u.body.version, 3)
  assert.equal(u.body.effective['quiet.end'], '08:00')
  const a = await m.call('GET', 'audit')
  assert.match(a.body.records[0].by, /undo/)
  assert.equal((await m.call('POST', 'undo', { version: 1 })).status, 409)
})

test('concurrent writes from two tabs with the same version: exactly one wins', async () => {
  const m = await mount()
  const [a, b] = await Promise.all([
    m.call('POST', 'write', { version: 0, set: { 'speaker.volume': 11 } }),
    m.call('POST', 'write', { version: 0, set: { 'speaker.volume': 22 } }),
  ])
  assert.deepEqual([a.status, b.status].sort(), [200, 409])
})

test('bad request bodies are 400, not crashes', async () => {
  const m = await mount()
  const r = await m.routes.get('/api/abl-settings/write').fetch(new Request('http://h/api/abl-settings/write', { method: 'POST', body: 'nope' }))
  assert.equal(r.status, 400)
})

test('config extends autopilot systems and channels', async () => {
  const keys = buildSchema({ autopilotSystems: ['a', 'b'], channels: ['x'] }).map(f => f.key)
  assert.ok(keys.includes('autopilot.systems.a') && keys.includes('autopilot.systems.b'))
  assert.deepEqual(buildSchema({ channels: ['x'] }).find(f => f.key === 'autoresponder.autosend_channels').options, ['x'])
})
