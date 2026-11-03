// Loads client.js the way the dsh shell does (factory registration), renders with React 18.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const here = dirname(fileURLToPath(import.meta.url))
const nm = process.env.ABL_TEST_NODE_MODULES
let React, S
if (nm) { const r = createRequire(join(nm, 'x.js')); React = r('react'); S = r('react-dom/server') }
const skip = !React && 'set ABL_TEST_NODE_MODULES (react@18 + react-dom@18)'

function load() {
  const regs = [], styles = []
  vm.runInNewContext(readFileSync(join(here, '..', 'client.js'), 'utf8'), {
    window: { __ModuleLoader__: { load: r => regs.push(r) } },
    document: { getElementById: () => null, createElement: () => ({}), head: { appendChild: e => styles.push(e) } },
  })
  return { regs, styles }
}
const NOW = Date.parse('2026-11-03T21:00:00Z')
const SNAP = {
  generated_at: '2026-11-03T20:59:50Z', status: 'amber', alerts: ['abl-market-bridge.service restarted 212×: crash loop?'],
  services: [
    { name: 'odysseus', port: 7800, group: 'core', status: 'up', latency_ms: 3.1, detail: '' },
    { name: 'llama-27b', port: 8085, group: 'core', status: 'up', latency_ms: 2.0, detail: '' },
    { name: 'ollama', port: 11434, group: 'core', status: 'down', latency_ms: null, detail: 'ConnectionRefusedError' },
    { name: 'desk-futures', port: 8090, group: 'desks', status: 'up', latency_ms: 1.2, detail: '' },
    { name: 'desk-options', port: 8091, group: 'desks', status: 'down', latency_ms: null, detail: 'ConnectionRefusedError' },
  ],
  models: { llama: { status: 'ok', slots: 2, busy: 1 }, ollama: { status: 'unreachable', error: 'URLError' } },
  units: [{ unit: 'abl-market-bridge.service', active: 'activating', sub: 'auto-restart', restarts: 212 },
          { unit: 'abl-morning-briefing.timer', active: 'active', sub: 'waiting', restarts: 0 }],
  host: { gpu: { name: 'GTX 1070', mem_used_mb: 7000, mem_total_mb: 8192, util_pct: 60, temp_c: 66 }, ram: { used_pct: 71.5 }, swap: { used_pct: 3 } },
  router: { status: 'ok', by_tier: { PRIMARY: 4, LIGHTWEIGHT: 9 }, last_hour: { decisions: 13, fallbacks: 2 },
            recent: [{ tier: 'LIGHTWEIGHT', reason: 'short', fallback_from: 'cloud' }] },
  abl: { autoresponder: { mode: 'shadow', pending: 1 }, settings: { version: 4 }, briefing: { status: 'ok', updated: '2026-11-03T12:25:00Z' },
         finance: { status: 'never-run' }, research_bridge: { status: 'ok', updated: '2026-11-03T20:17:00Z' } },
}

test('registers only a factory; id = package name', () => {
  const { regs, styles } = load()
  assert.equal(regs.length, 1); assert.equal(regs[0].id, 'dsh-abl-console-r2'); assert.equal(styles.length, 0)
})

test('every tab renders the snapshot with per-card timestamps', { skip }, () => {
  const exp = load().regs[0].factory(() => React)
  const cp = { now: NOW, updatedAt: Date.parse(SNAP.generated_at), loading: false, onRetry() {} }
  const html = {}
  for (const [id, View] of Object.entries(exp.views)) {
    html[id] = S.renderToStaticMarkup(React.createElement('div', null, View({ d: SNAP, cp })))
    assert.match(html[id], /updated 10s ago/, `${id} cards carry a timestamp`)
  }
  assert.match(html.home, /AMBER/); assert.match(html.home, /crash loop/); assert.match(html.home, /3 \/ 5 up/)
  assert.match(html.models, /1 \/ 2 slots busy/); assert.match(html.models, /Service unreachable/); assert.match(html.models, /ollama :11434 is down/)
  assert.match(html.voice, /never/); assert.match(html.voice, /active \(waiting\)/)
  assert.match(html.fleet, /212↻/); assert.match(html.desks, /desk-options :8091 is down/)
})

test('loading state shows spinners, not blank cards', { skip }, () => {
  const exp = load().regs[0].factory(() => React)
  const out = S.renderToStaticMarkup(React.createElement(exp.Card, { title: 'X', now: NOW, loading: true }))
  assert.match(out, /class="spin"/); assert.match(out, /aria-busy="true"/)
})

test('unreachable card has a reason and a retry button; stale stamp turns amber', { skip }, () => {
  const exp = load().regs[0].factory(() => React)
  const out = S.renderToStaticMarkup(React.createElement(exp.Card, { title: 'X', now: NOW, updatedAt: NOW - 120000, unreachable: 'hub :8902 is down', onRetry() {} }))
  assert.match(out, /Service unreachable/); assert.match(out, /hub :8902 is down/); assert.match(out, />Retry</)
  assert.match(out, /stamp stale/); assert.match(out, /updated 2m ago/)
})

test('Panel first paint: tabs + spinners, never blank', { skip }, () => {
  const exp = load().regs[0].factory(() => React)
  const html = S.renderToStaticMarkup(React.createElement(exp.Panel))
  for (const t of ['Home', 'Models &amp; Router', 'Voice &amp; Autopilot', 'Fleet', 'Desks']) assert.ok(html.includes(t), t)
  assert.ok((html.match(/class="spin"/g) || []).length >= 4)
})

test('apply registers sidebar + keyed main panel', { skip }, () => {
  const exp = load().regs[0].factory(() => React); const reg = []
  exp.apply({ slots: { inject: (s, f) => f(), register: (o, c) => { reg.push({ ...o }); return () => {} } } })
  assert.deepEqual(reg, [{ name: 'sidebar.panellist', id: 'abl-console-r2', order: 20, label: 'Console' }, { name: 'main', key: 'abl-console-r2' }])
})
