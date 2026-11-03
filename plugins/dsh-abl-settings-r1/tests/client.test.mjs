// Executes client.js exactly as the dsh shell does: as a classic script that only
// registers a factory via window.__ModuleLoader__.load, then materializes it with a
// require() seeded with React. Needs react@18 + react-dom@18 resolvable via
// ABL_TEST_NODE_MODULES (the repo vendors no node_modules); skipped otherwise.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import { buildSchema } from '../schema.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const nm = process.env.ABL_TEST_NODE_MODULES
let React, ReactDOMServer
if (nm) {
  const req = createRequire(join(nm, 'noop.js'))
  React = req('react'); ReactDOMServer = req('react-dom/server')
}
const skip = !React && 'set ABL_TEST_NODE_MODULES to a dir with react@18 + react-dom@18'

function loadClient() {
  const registrations = []
  const style = []
  const sandbox = {
    window: { __ModuleLoader__: { load: r => registrations.push(r) } },
    document: {
      getElementById: () => null,
      createElement: () => ({}),
      head: { appendChild: el => style.push(el) },
    },
  }
  vm.runInNewContext(readFileSync(join(here, '..', 'client.js'), 'utf8'), sandbox)
  return { registrations, style, sandbox }
}

test('executing client.js only registers the factory (no side effects before materialization)', () => {
  const { registrations, style } = loadClient()
  assert.equal(registrations.length, 1)
  assert.equal(registrations[0].id, 'dsh-abl-settings-r1')       // must equal the package name
  assert.equal(typeof registrations[0].factory, 'function')
  assert.equal(style.length, 0)
})

test('factory materializes with the seeded react and injects CSS once', { skip }, () => {
  const { registrations, style } = loadClient()
  const asked = []
  const exp = registrations[0].factory(id => { asked.push(id); if (id === 'react') return React; throw new Error('unexpected require ' + id) })
  assert.deepEqual(asked, ['react'])
  assert.equal(exp.name, 'dsh-abl-settings-r1')
  assert.equal(typeof exp.apply, 'function')
  assert.equal(style.length, 1)
  assert.match(style[0].textContent, /#161b30/)
  assert.match(style[0].textContent, /#cbbbff/)
  assert.match(style[0].textContent, /#f7ad31/)
  assert.match(style[0].textContent, /#a7b3c1/)
})

test('apply registers the sidebar entry and the keyed main panel via slots.inject', { skip }, () => {
  const { registrations } = loadClient()
  const exp = registrations[0].factory(() => React)
  const injected = [], registered = []
  exp.apply({ slots: {
    inject(slot, fn) { injected.push(slot); fn() },
    register(opts, comp) { registered.push([opts, comp]); return () => {} },
  } })
  assert.deepEqual(injected, ['sidebar.panellist', 'main'])
  // objects come from the vm realm (different Object.prototype): compare structure, not prototype
  assert.deepEqual({ ...registered[0][0] }, { name: 'sidebar.panellist', id: 'abl-settings', order: 990, label: 'Settings' })
  assert.deepEqual({ ...registered[1][0] }, { name: 'main', key: 'abl-settings' })
  assert.equal(registered[1][1], exp.Panel)
})

test('every schema field type renders a control; floors disable lower tiers', { skip }, () => {
  const { registrations } = loadClient()
  const exp = registrations[0].factory(() => React)
  const schema = buildSchema()
  const defaults = Object.fromEntries(schema.map(f => [f.key, f.default]))
  for (const f of schema) {
    const html = ReactDOMServer.renderToStaticMarkup(React.createElement(exp.Control, { field: f, value: defaults[f.key], onChange() {} }))
    assert.ok(html.length > 10, `${f.key} rendered`)
  }
  const money = schema.find(f => f.key === 'alerts.tiers.money')
  const html = ReactDOMServer.renderToStaticMarkup(React.createElement(exp.Control, { field: money, value: 'RED', onChange() {} }))
  assert.match(html, /<button[^>]*disabled=""[^>]*>GREEN<\/button>/)
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>YELLOW<\/button>/)
  const fraud = schema.find(f => f.key === 'alerts.tiers.fraud')
  const fh = ReactDOMServer.renderToStaticMarkup(React.createElement(exp.Control, { field: fraud, value: 'RED', onChange() {} }))
  assert.equal((fh.match(/disabled=""/g) || []).length, 3)          // locked: every option disabled
})

test('panel first paint shows the loading state (no crash before data)', { skip }, () => {
  const { registrations } = loadClient()
  const exp = registrations[0].factory(() => React)
  const html = ReactDOMServer.renderToStaticMarkup(React.createElement(exp.Panel))
  assert.match(html, /Settings/)
  assert.match(html, /Loading/)
})

test('sidebar icon renders an svg', { skip }, () => {
  const { registrations } = loadClient()
  const exp = registrations[0].factory(() => React)
  assert.match(ReactDOMServer.renderToStaticMarkup(React.createElement(exp.Icon)), /^<svg/)
})
