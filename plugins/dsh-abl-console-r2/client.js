// dsh-abl-console-r2 — client. Classic script: registers a factory with the dsh module
// loader; the body (incl. CSS) runs at materialization. React comes from the seeded table.
/* eslint-disable */
window.__ModuleLoader__.load({
  id: 'dsh-abl-console-r2',
  factory: function (require) {
    var React = require('react')
    var h = React.createElement
    var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef, useCallback = React.useCallback

    var PANEL_ID = 'abl-console-r2'
    var ENDPOINT = '/api/abl-console/dashboard'
    var REFRESH_MS = 30000
    var STALE_S = 90
    var TABS = [
      { id: 'home', label: 'Home' }, { id: 'models', label: 'Models & Router' },
      { id: 'voice', label: 'Voice & Autopilot' }, { id: 'fleet', label: 'Fleet' }, { id: 'desks', label: 'Desks' }
    ]

    if (typeof document !== 'undefined' && !document.getElementById('abl-console-r2-css')) {
      var css = document.createElement('style')
      css.id = 'abl-console-r2-css'
      css.textContent = [
        '.ablc{--navy:#161b30;--lav:#cbbbff;--amber:#f7ad31;--slate:#a7b3c1;--red:#ff6b7a;--green:#7ee0b0;--line:rgba(167,179,193,.18);',
        'background:var(--navy);color:#fff;height:100%;overflow:auto;padding:16px;box-sizing:border-box;font:14px/1.45 system-ui,sans-serif}',
        '.ablc *{box-sizing:border-box}.ablc .top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}',
        '.ablc h1{font-size:18px;margin:0;display:flex;align-items:center;gap:8px}.ablc .meta{color:var(--slate);font-size:12px}',
        '.ablc .dot{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--slate)}',
        '.ablc .dot.green,.ablc .dot.ok,.ablc .dot.up{background:var(--green)}.ablc .dot.amber,.ablc .dot.warn,.ablc .dot.degraded{background:var(--amber)}',
        '.ablc .dot.red,.ablc .dot.err,.ablc .dot.down{background:var(--red)}',
        '.ablc .tabs{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;margin-bottom:10px;border-bottom:1px solid var(--line)}',
        '.ablc .tabs button{all:unset;cursor:pointer;padding:6px 12px;border-radius:14px;color:var(--slate);white-space:nowrap;transition:background .15s,color .15s}',
        '.ablc .tabs button[aria-selected=true]{background:var(--lav);color:var(--navy);font-weight:600}.ablc .tabs button:focus-visible{outline:2px solid var(--lav)}',
        '.ablc .pane{animation:ablcIn .18s ease-out}@keyframes ablcIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}',
        '@media (prefers-reduced-motion:reduce){.ablc .pane{animation:none}.ablc .spin{animation-duration:2s}}',
        '.ablc .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}',
        '.ablc .card{border:1px solid var(--line);border-radius:10px;padding:12px;min-height:110px;display:flex;flex-direction:column;gap:8px;transition:border-color .2s}',
        '.ablc .card.err{border-color:rgba(255,107,122,.5)}.ablc .card.warn{border-color:rgba(247,173,49,.5)}',
        '.ablc .card header{display:flex;justify-content:space-between;align-items:center;gap:6px}.ablc .card h2{font-size:13px;margin:0;color:var(--slate);font-weight:600;letter-spacing:.02em}',
        '.ablc .stamp{font-size:11px;color:var(--slate)}.ablc .stamp.stale{color:var(--amber)}',
        '.ablc .big{font-size:22px;font-weight:600}.ablc .row{display:flex;justify-content:space-between;gap:8px;font-size:13px}.ablc .row span:first-child{color:var(--slate)}',
        '.ablc .bar{height:6px;border-radius:3px;background:rgba(255,255,255,.08);overflow:hidden}.ablc .bar i{display:block;height:100%;background:var(--lav)}',
        '.ablc .bar i.hot{background:var(--amber)}.ablc .bar i.crit{background:var(--red)}',
        '.ablc .spin{width:22px;height:22px;border:3px solid rgba(203,187,255,.25);border-top-color:var(--lav);border-radius:50%;animation:ablcSpin .8s linear infinite;margin:auto}',
        '@keyframes ablcSpin{to{transform:rotate(360deg)}}',
        '.ablc .unreach{color:var(--red);font-weight:600}.ablc .why{color:var(--slate);font-size:12px;word-break:break-word}',
        '.ablc .btn{background:transparent;color:var(--lav);border:1px solid var(--lav);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}',
        '.ablc .btn:disabled{opacity:.5}.ablc .banner{border-left:3px solid var(--amber);padding:6px 10px;margin-bottom:10px;color:var(--amber);background:rgba(247,173,49,.06);border-radius:4px}',
        '.ablc ul{margin:0;padding-left:18px}.ablc li{margin:2px 0}.ablc table{width:100%;border-collapse:collapse;font-size:13px}.ablc td{padding:4px 2px;border-top:1px solid var(--line)}'
      ].join('')
      document.head.appendChild(css)
    }

    // ── data hook: one endpoint, 30 s refresh, pause when hidden, keep last good data ──
    function useDashboard() {
      var s = useState({ data: null, error: null, loading: true, lastOk: null, busy: false })
      var st = s[0], set = s[1]
      var inflight = useRef(false)
      var load = useCallback(function (fresh) {
        if (inflight.current) return
        inflight.current = true
        set(function (p) { return Object.assign({}, p, { busy: true }) })
        fetch(ENDPOINT + (fresh ? '?fresh=1' : ''), { credentials: 'same-origin' })
          .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status } }) })
          .then(function (b) {
            if (b && b.ok !== false && b.services) set({ data: b, error: null, loading: false, lastOk: Date.now(), busy: false })
            else set(function (p) { return Object.assign({}, p, { error: (b && b.error) || 'unknown error', loading: false, busy: false }) })
          })
          .catch(function (e) { set(function (p) { return Object.assign({}, p, { error: 'Console server unreachable: ' + e.message, loading: false, busy: false }) }) })
          .then(function () { inflight.current = false })
      }, [])
      useEffect(function () {
        load(false)
        var id = setInterval(function () { if (typeof document === 'undefined' || document.visibilityState !== 'hidden') load(false) }, REFRESH_MS)
        function onVis() { if (document.visibilityState === 'visible') load(false) }
        if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVis)
        return function () { clearInterval(id); if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVis) }
      }, [load])
      return [st, load]
    }

    function useNow() {
      var n = useState(Date.now()), now = n[0], setNow = n[1]
      useEffect(function () { var id = setInterval(function () { setNow(Date.now()) }, 1000); return function () { clearInterval(id) } }, [])
      return now
    }

    function ago(ts, now) {
      if (!ts) return 'never'
      var s = Math.max(0, Math.round((now - ts) / 1000))
      return s < 60 ? s + 's ago' : s < 3600 ? Math.round(s / 60) + 'm ago' : Math.round(s / 3600) + 'h ago'
    }

    // ── card: loading spinner | "service unreachable" | content, with its own timestamp ──
    function Card(p) {
      var now = p.now
      var stale = p.updatedAt && (now - p.updatedAt) / 1000 > STALE_S
      var state = p.loading ? 'loading' : p.unreachable ? 'err' : (p.level || 'ok')
      return h('section', { className: 'card ' + (state === 'err' ? 'err' : state === 'warn' ? 'warn' : ''), 'aria-busy': !!p.loading },
        h('header', null,
          h('h2', null, h('span', { className: 'dot ' + (state === 'loading' ? '' : state), style: { marginRight: '6px' } }), p.title),
          h('span', { className: 'stamp' + (stale ? ' stale' : ''), title: p.updatedAt ? new Date(p.updatedAt).toLocaleString() : '' },
            p.loading ? '' : 'updated ' + ago(p.updatedAt, now))),
        p.loading ? h('div', { className: 'spin', role: 'status', 'aria-label': 'loading' })
          : p.unreachable ? h('div', null,
              h('div', { className: 'unreach' }, p.unreachableTitle || 'Service unreachable'),
              h('div', { className: 'why' }, p.unreachable),
              p.onRetry ? h('button', { className: 'btn', style: { marginTop: '6px' }, onClick: p.onRetry }, 'Retry') : null)
          : p.children)
    }

    function Bar(p) {
      var pct = Math.max(0, Math.min(100, p.pct || 0))
      return h('div', { className: 'bar', title: pct + '%' }, h('i', { className: pct > 95 ? 'crit' : pct > 80 ? 'hot' : '', style: { width: pct + '%' } }))
    }
    function Row(p) { return h('div', { className: 'row' }, h('span', null, p.k), h('span', null, p.v)) }

    // ── tabs ──────────────────────────────────────────────────────────────────
    function svcBy(d, name) { return (d.services || []).find(function (s) { return s.name === name }) }
    function downReason(s) { return s ? (s.name + ' :' + s.port + ' is ' + s.status + (s.detail ? ' (' + s.detail + ')' : '')) : 'not probed' }

    function HomeTab(c) {
      var d = c.d, up = (d.services || []).filter(function (s) { return s.status === 'up' }).length, total = (d.services || []).length
      var host = d.host || {}, ram = host.ram || {}, swap = host.swap || {}, gpu = host.gpu || {}, abl = d.abl || {}
      return [
        h(Card, Object.assign({ key: 'status', title: 'Overall', level: d.status === 'green' ? 'ok' : d.status === 'amber' ? 'warn' : 'err' }, c.cp),
          h('div', { className: 'big' }, (d.status || '?').toUpperCase()),
          (d.alerts || []).length ? h('ul', null, d.alerts.slice(0, 6).map(function (a, i) { return h('li', { key: i }, a) })) : h('div', { className: 'why' }, 'No alerts.')),
        h(Card, Object.assign({ key: 'svc', title: 'Services', level: up === total ? 'ok' : 'warn' }, c.cp),
          h('div', { className: 'big' }, up + ' / ' + total + ' up'), h(Bar, { pct: total ? 100 * up / total : 0 })),
        h(Card, Object.assign({ key: 'host', title: 'Host', unreachable: ram.status === 'unavailable' ? 'host metrics unavailable' : null }, c.cp),
          h(Row, { k: 'RAM', v: (ram.used_pct != null ? ram.used_pct + '%' : '—') }), h(Bar, { pct: ram.used_pct }),
          h(Row, { k: 'Swap', v: (swap.used_pct != null ? swap.used_pct + '%' : '—') }), h(Bar, { pct: swap.used_pct }),
          gpu.mem_total_mb ? [h(Row, { key: 'g', k: 'GPU mem', v: gpu.mem_used_mb + ' / ' + gpu.mem_total_mb + ' MB' }), h(Bar, { key: 'gb', pct: 100 * gpu.mem_used_mb / gpu.mem_total_mb })]
            : h(Row, { k: 'GPU', v: gpu.status || '—' })),
        h(Card, Object.assign({ key: 'abl', title: 'ABL', unreachable: abl.autoresponder && abl.autoresponder.status === 'unavailable' ? 'autoresponder not installed on this backend' : null, unreachableTitle: 'Autoresponder unavailable' }, c.cp),
          h(Row, { k: 'Autoresponder', v: (abl.autoresponder || {}).mode || '—' }),
          h(Row, { k: 'Held drafts', v: String((abl.autoresponder || {}).pending ?? '—') }),
          h(Row, { k: 'Settings', v: 'v' + ((abl.settings || {}).version || 0) }))
      ]
    }

    function ModelsTab(c) {
      var d = c.d, m = d.models || {}, ll = m.llama || {}, ol = m.ollama || {}, r = d.router || {}
      var llSvc = svcBy(d, 'llama-27b'), olSvc = svcBy(d, 'ollama')
      return [
        h(Card, Object.assign({ key: 'llama', title: '27B llama-server', unreachable: (llSvc && llSvc.status === 'down') || ll.status === 'unreachable' ? downReason(llSvc) : null }, c.cp),
          ll.status === 'ok' ? [h('div', { key: 'b', className: 'big' }, ll.busy + ' / ' + ll.slots + ' slots busy'), h(Bar, { key: 'bar', pct: ll.slots ? 100 * ll.busy / ll.slots : 0 })]
            : h(Row, { k: 'Slots', v: ll.status || '—' })),
        h(Card, Object.assign({ key: 'ollama', title: 'Ollama (3B)', unreachable: (olSvc && olSvc.status === 'down') || ol.status === 'unreachable' ? downReason(olSvc) : null }, c.cp),
          (ol.loaded || []).length ? h('ul', null, ol.loaded.map(function (x, i) { return h('li', { key: i }, x.name + ' · ' + x.vram_mb + ' MB VRAM') }))
            : h('div', { className: 'why' }, 'No models loaded (idle).')),
        h(Card, Object.assign({ key: 'router', title: 'Router · last hour', unreachable: r.status === 'no-log' ? (r.hint || 'no router log') : null, unreachableTitle: 'Router log not found',
          level: (r.last_hour || {}).fallbacks > 6 ? 'warn' : 'ok' }, c.cp),
          h(Row, { k: 'Decisions', v: String((r.last_hour || {}).decisions ?? '—') }),
          h(Row, { k: 'Fallbacks', v: String((r.last_hour || {}).fallbacks ?? '—') }),
          h(Row, { k: 'By tier', v: Object.keys(r.by_tier || {}).map(function (k) { return k + ' ' + r.by_tier[k] }).join(' · ') || '—' })),
        h(Card, Object.assign({ key: 'recent', title: 'Recent decisions', unreachable: r.status === 'no-log' ? 'no router log' : null, unreachableTitle: 'Router log not found' }, c.cp),
          h('table', null, h('tbody', null, (r.recent || []).slice(0, 8).map(function (x, i) {
            return h('tr', { key: i }, h('td', null, String(x.tier || x.role || x.decision || '?')), h('td', null, String(x.reason || x.model || '')),
              h('td', null, x.fallback || x.fallback_from ? '↩ ' + (x.fallback_from || 'fallback') : ''))
          }))))
      ]
    }

    function freshness(x, now) { return x && x.updated ? ago(Date.parse(x.updated), now) : 'never' }
    function VoiceTab(c) {
      var d = c.d, abl = d.abl || {}, units = d.units || []
      var timers = units.filter(function (u) { return /timer$/.test(u.unit || '') })
      function job(key, title, unitName) {
        var x = abl[key] || {}, u = units.find(function (z) { return z.unit === unitName }) || {}
        return h(Card, Object.assign({ key: key, title: title, level: x.status === 'error' ? 'err' : x.status === 'never-run' ? 'warn' : 'ok' }, c.cp),
          h(Row, { k: 'Last result', v: x.status || '—' }), h(Row, { k: 'Last run', v: freshness(x, c.cp.now) }),
          h(Row, { k: 'Timer', v: u.active ? u.active + (u.sub ? ' (' + u.sub + ')' : '') : 'not installed' }))
      }
      return [
        job('briefing', 'Morning briefing', 'abl-morning-briefing.timer'),
        job('research_bridge', 'Research bridge', 'abl-research-bridge.timer'),
        job('finance', 'Spending summary', 'abl-finance-voice.timer'),
        h(Card, Object.assign({ key: 'ar', title: 'Autoresponder' }, c.cp),
          h(Row, { k: 'Mode', v: (abl.autoresponder || {}).mode || '—' }),
          h(Row, { k: 'Held drafts', v: String((abl.autoresponder || {}).pending ?? '—') }),
          h(Row, { k: 'Timers active', v: timers.filter(function (t) { return t.active === 'active' }).length + ' / ' + timers.length }))
      ]
    }

    function FleetTab(c) {
      var d = c.d, core = (d.services || []).filter(function (s) { return s.group !== 'desks' })
      return [
        h(Card, Object.assign({ key: 'svcs', title: 'Core services', level: core.every(function (s) { return s.status === 'up' }) ? 'ok' : 'warn' }, c.cp),
          h('table', null, h('tbody', null, core.map(function (s) {
            return h('tr', { key: s.name }, h('td', null, h('span', { className: 'dot ' + s.status })), h('td', null, s.name), h('td', null, ':' + s.port),
              h('td', null, s.status === 'up' ? (s.latency_ms != null ? s.latency_ms + ' ms' : 'up') : (s.detail || s.status)))
          })))),
        h(Card, Object.assign({ key: 'units', title: 'systemd units', level: (d.units || []).some(function (u) { return u.active === 'failed' || u.restarts >= 5 }) ? 'warn' : 'ok' }, c.cp),
          (d.units || []).length ? h('table', null, h('tbody', null, d.units.map(function (u) {
            var bad = u.active === 'failed' || u.restarts >= 5
            return h('tr', { key: u.unit }, h('td', null, h('span', { className: 'dot ' + (bad ? 'err' : u.active === 'active' ? 'ok' : '') })),
              h('td', null, u.unit), h('td', null, u.active + (u.restarts ? ' · ' + u.restarts + '↻' : '')))
          }))) : h('div', { className: 'why' }, 'No unit data (systemctl unavailable).'))
      ]
    }

    function DesksTab(c) {
      var desks = (c.d.services || []).filter(function (s) { return s.group === 'desks' })
      if (!desks.length) return [h(Card, Object.assign({ key: 'none', title: 'Desks', unreachable: 'no desks configured in ABL_DASHBOARD_SERVICES', unreachableTitle: 'No desks' }, c.cp))]
      return desks.map(function (s) {
        return h(Card, Object.assign({ key: s.name, title: s.name.replace(/^desk-/, ''), unreachable: s.status === 'down' ? downReason(s) : null,
          level: s.status === 'degraded' ? 'warn' : 'ok' }, c.cp),
          h('div', { className: 'big' }, s.status === 'up' ? 'Up' : s.status), h(Row, { k: 'Port', v: ':' + s.port }),
          h(Row, { k: 'Latency', v: s.latency_ms != null ? s.latency_ms + ' ms' : '—' }))
      })
    }

    var TAB_VIEWS = { home: HomeTab, models: ModelsTab, voice: VoiceTab, fleet: FleetTab, desks: DesksTab }

    function Panel(props) {
      var dash = useDashboard(), st = dash[0], load = dash[1]
      var now = useNow()
      var t = useState('home'), tab = t[0], setTab = t[1]
      var d = st.data
      var updatedAt = d && d.generated_at ? Date.parse(d.generated_at) : st.lastOk
      var retry = function () { load(true) }
      var cp = { now: now, updatedAt: updatedAt, loading: st.loading && !d, onRetry: retry }
      var body
      if (!d && st.loading) body = TAB_VIEWS[tab]({ d: { services: [], models: {}, host: {}, abl: {}, router: {}, units: [] }, cp: cp })
      else if (!d) body = [h(Card, { key: 'down', title: 'Console', now: now, unreachable: st.error, unreachableTitle: 'Service unreachable', onRetry: retry })]
      else body = TAB_VIEWS[tab]({ d: d, cp: cp })
      return h('div', { className: 'ablc' },
        h('div', { className: 'top' },
          h('h1', null, h('span', { className: 'dot ' + (d ? d.status : '') }), 'ABL Console'),
          h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
            h('span', { className: 'meta' }, st.busy ? 'refreshing…' : 'auto-refresh 30s'),
            h('button', { className: 'btn', onClick: retry, disabled: st.busy, 'aria-label': 'refresh now' }, '↻ Refresh'))),
        d && st.error ? h('div', { className: 'banner', role: 'status' }, 'Last refresh failed (' + st.error + '). Showing data from ' + ago(st.lastOk, now) + '.') : null,
        h('div', { className: 'tabs', role: 'tablist' }, TABS.map(function (x) {
          return h('button', { key: x.id, role: 'tab', 'aria-selected': tab === x.id, onClick: function () { setTab(x.id) } }, x.label)
        })),
        h('div', { className: 'pane', key: tab, role: 'tabpanel' }, h('div', { className: 'grid' }, body)))
    }

    function Icon() {
      return h('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, 'aria-hidden': true },
        h('rect', { x: 3, y: 4, width: 18, height: 14, rx: 2 }), h('path', { d: 'M7 14l3-3 3 2 4-5' }), h('path', { d: 'M8 21h8' }))
    }

    function apply(ctx) {
      var reg = function (slot, fn) { if (ctx.slots && typeof ctx.slots.inject === 'function') ctx.slots.inject(slot, fn); else fn() }
      reg('sidebar.panellist', function () { return ctx.slots.register({ name: 'sidebar.panellist', id: PANEL_ID, order: 20, label: 'Console' }, Icon) })
      reg('main', function () { return ctx.slots.register({ name: 'main', key: PANEL_ID }, Panel) })
    }

    return { name: 'dsh-abl-console-r2', apply: apply, Panel: Panel, Icon: Icon, Card: Card, views: TAB_VIEWS, ago: ago }
  }
})
