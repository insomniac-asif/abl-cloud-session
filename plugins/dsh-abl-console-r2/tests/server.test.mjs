import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import plugin, { makeHandler, apply, name, inject } from '../index.mjs'

function upstream(handler) {
  return new Promise(res => {
    const s = http.createServer(handler).listen(0, '127.0.0.1', () => res(s))
  })
}
const req = (q = '') => new Request('http://h/api/abl-console/dashboard' + q)

test('plugin shape + route registration', () => {
  assert.equal(name, 'dsh-abl-console-r2'); assert.deepEqual(inject, ['connection']); assert.equal(plugin.apply, apply)
  const reg = []
  apply({ connection: { fetch: { register: r => { reg.push(r); return () => {} } } } }, {})
  assert.equal(reg[0].path, '/api/abl-console/dashboard'); assert.deepEqual(reg[0].methods, ['GET']); assert.equal(reg[0].requestBody, 'buffered')
})

test('proxies the snapshot with the server-side token; passes fresh=1; never returns the token', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'c2-')); const tf = join(dir, 'tok'); await writeFile(tf, 'sekret\n')
  let seen
  const s = await upstream((rq, rs) => { seen = { auth: rq.headers.authorization, url: rq.url }; rs.end(JSON.stringify({ status: 'green', services: [] })) })
  const h = makeHandler({ dashboardUrl: `http://127.0.0.1:${s.address().port}/api/abl/dashboard`, tokenFile: tf })
  const r = await h(req('?fresh=1')); const b = await r.json()
  assert.equal(r.status, 200); assert.equal(b.ok, true); assert.equal(b.status, 'green')
  assert.equal(seen.auth, 'Bearer sekret'); assert.equal(seen.url, '/api/abl/dashboard?fresh=1')
  assert.ok(!JSON.stringify(b).includes('sekret'))
  s.close()
})

test('401 from Odysseus → readable 502', async () => {
  const s = await upstream((rq, rs) => { rs.statusCode = 401; rs.end('{}') })
  const r = await makeHandler({ dashboardUrl: `http://127.0.0.1:${s.address().port}/x` })(req())
  assert.equal(r.status, 502); assert.match((await r.json()).error, /refused the console token/)
  s.close()
})

test('500, unreachable and timeout all become {ok:false, error}', async () => {
  const s = await upstream((rq, rs) => { rs.statusCode = 500; rs.end('{}') })
  assert.match((await (await makeHandler({ dashboardUrl: `http://127.0.0.1:${s.address().port}/x` })(req())).json()).error, /HTTP 500/)
  s.close()
  const dead = await makeHandler({ dashboardUrl: 'http://127.0.0.1:9/x' })(req())
  const db = await dead.json(); assert.equal(db.ok, false); assert.match(db.error, /Odysseus unreachable/)
  const hang = await upstream(() => {})
  const t = await makeHandler({ dashboardUrl: `http://127.0.0.1:${hang.address().port}/x`, timeoutMs: 150 })(req())
  assert.match((await t.json()).error, /timed out after 150 ms/)
  hang.closeAllConnections(); hang.close()
})
