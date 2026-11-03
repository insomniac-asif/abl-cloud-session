// dsh-abl-console-r2 — server half. One authenticated proxy route to Odysseus's
// GET /api/abl/dashboard (src/abl_dashboard.py), so the browser makes ONE same-origin call
// (behind dsh sign-in) instead of probing seven services, and never sees the Odysseus token.
//   GET /api/abl-console/dashboard[?fresh=1] → the snapshot | {ok:false, error, at}
import { readFile } from 'node:fs/promises'

export const name = 'dsh-abl-console-r2'
export const inject = ['connection']

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } })
}

export function makeHandler(config = {}, fetchImpl = globalThis.fetch) {
  const url = config.dashboardUrl || 'http://127.0.0.1:7800/api/abl/dashboard'
  const timeoutMs = Number(config.timeoutMs) || 6000
  let token = null
  async function getToken() {
    if (token !== null) return token
    try { token = (await readFile(config.tokenFile || '', 'utf8')).trim() } catch { token = '' }
    return token
  }
  return async (request) => {
    const fresh = new URL(request.url).searchParams.get('fresh') === '1'
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), timeoutMs)
    try {
      const t = await getToken()
      const r = await fetchImpl(url + (fresh ? '?fresh=1' : ''), {
        headers: t ? { authorization: `Bearer ${t}` } : {}, signal: ctl.signal,
      })
      if (r.status === 401 || r.status === 403) {
        token = null                                   // token rotated? re-read next time
        return json(502, { ok: false, error: `Odysseus refused the console token (HTTP ${r.status})`, at: new Date().toISOString() })
      }
      if (!r.ok) return json(502, { ok: false, error: `Odysseus dashboard HTTP ${r.status}`, at: new Date().toISOString() })
      const body = await r.json()
      return json(200, { ok: true, ...body })
    } catch (e) {
      const why = e?.name === 'AbortError' ? `timed out after ${timeoutMs} ms` : (e?.cause?.code || e?.message || String(e))
      return json(502, { ok: false, error: `Odysseus unreachable: ${why}`, at: new Date().toISOString() })
    } finally {
      clearTimeout(timer)
    }
  }
}

export function apply(ctx, config = {}) {
  const dispose = ctx.connection.fetch.register({
    path: '/api/abl-console/dashboard', methods: ['GET'], requestBody: 'buffered', fetch: makeHandler(config),
  })
  if (typeof ctx.effect === 'function') ctx.effect(() => dispose)
}

export default { name, inject, apply }
