// dsh-abl-settings-r1 — client half. Classic script: executing it only REGISTERS the
// factory (dsh client module contract); the body, including CSS injection, runs at
// materialization. React comes from the shell's seeded module table.
/* eslint-disable */
window.__ModuleLoader__.load({
  id: 'dsh-abl-settings-r1',
  factory: function (require) {
    var React = require('react')
    var h = React.createElement
    var useState = React.useState, useEffect = React.useEffect, useCallback = React.useCallback, useRef = React.useRef

    var PANEL_ID = 'abl-settings'
    var API = '/api/abl-settings'
    var CACHE_KEY = 'abl-settings-cache-v1'

    // ── styles (ABL palette) ────────────────────────────────────────────────
    if (typeof document !== 'undefined' && !document.getElementById('abl-settings-css')) {
      var css = document.createElement('style')
      css.id = 'abl-settings-css'
      css.textContent = [
        '.abls{--navy:#161b30;--lav:#cbbbff;--amber:#f7ad31;--slate:#a7b3c1;--red:#ff6b7a;--line:rgba(167,179,193,.18);',
        'background:var(--navy);color:#fff;height:100%;overflow:auto;padding:16px;box-sizing:border-box;font:14px/1.45 system-ui,sans-serif}',
        '.abls *{box-sizing:border-box}',
        '.abls h1{font-size:18px;margin:0 0 4px}.abls .meta{color:var(--slate);font-size:12px;margin-bottom:14px}',
        '.abls .banner{border:1px solid var(--line);border-left:3px solid var(--slate);padding:8px 10px;border-radius:6px;margin:0 0 12px;color:var(--slate)}',
        '.abls .banner.warn{border-left-color:var(--amber);color:var(--amber)}.abls .banner.err{border-left-color:var(--red);color:var(--red)}',
        '.abls .sec{border:1px solid var(--line);border-radius:10px;margin-bottom:10px;overflow:hidden}',
        '.abls .sec>button{all:unset;cursor:pointer;display:flex;justify-content:space-between;width:100%;padding:12px 14px;box-sizing:border-box}',
        '.abls .sec>button:focus-visible{outline:2px solid var(--lav)}.abls .sec .sum{color:var(--slate);font-size:12px}',
        '.abls .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px;padding:4px 14px 14px}',
        '@media (max-width:720px){.abls .grid{grid-template-columns:1fr}}',
        '.abls .f{display:flex;flex-direction:column;gap:6px;min-width:0}.abls .f label{font-size:12px;color:var(--slate);display:flex;gap:6px;align-items:center}',
        '.abls .chip{font-size:10px;padding:1px 6px;border-radius:9px;border:1px solid var(--line);color:var(--slate)}',
        '.abls .chip.changed{border-color:var(--lav);color:var(--lav)}.abls .chip.dirty{border-color:var(--amber);color:var(--amber)}.abls .chip.lock{border-color:var(--red);color:var(--red)}',
        '.abls input[type=text],.abls input[type=time],.abls input[type=number],.abls select{background:rgba(255,255,255,.04);color:#fff;border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:100%}',
        '.abls input:focus,.abls select:focus{outline:2px solid var(--lav);outline-offset:0}',
        '.abls .seg{display:flex;flex-wrap:wrap;gap:6px}.abls .seg button{background:transparent;color:var(--slate);border:1px solid var(--line);border-radius:14px;padding:4px 10px;cursor:pointer}',
        '.abls .seg button.on{background:var(--lav);color:var(--navy);border-color:var(--lav)}.abls .seg button:disabled{opacity:.45;cursor:not-allowed}',
        '.abls .err{color:var(--red);font-size:12px}.abls .help{color:var(--slate);font-size:12px}',
        '.abls .bar{position:sticky;bottom:0;display:flex;gap:8px;align-items:center;justify-content:space-between;background:var(--navy);border-top:2px solid var(--amber);padding:10px 0;margin-top:10px}',
        '.abls .btn{background:var(--lav);color:var(--navy);border:0;border-radius:6px;padding:7px 14px;font-weight:600;cursor:pointer}',
        '.abls .btn.ghost{background:transparent;color:var(--slate);border:1px solid var(--line)}.abls .btn:disabled{opacity:.5}',
        '.abls table{width:100%;border-collapse:collapse;font-size:12px}.abls td{border-top:1px solid var(--line);padding:5px 4px;color:var(--slate);vertical-align:top}',
        '.abls .ord{display:flex;flex-direction:column;gap:4px}.abls .ord div{display:flex;gap:6px;align-items:center}',
        '.abls .toast{position:fixed;right:16px;bottom:16px;background:#232a46;border:1px solid var(--lav);border-radius:8px;padding:10px 12px;display:flex;gap:10px;align-items:center;z-index:50}'
      ].join('')
      document.head.appendChild(css)
    }

    // ── data ────────────────────────────────────────────────────────────────
    function call(path, init) {
      return fetch(API + path, Object.assign({ credentials: 'same-origin', headers: { 'content-type': 'application/json' } }, init || {}))
        .then(function (r) { return r.json().catch(function () { return {} }).then(function (b) { return { status: r.status, body: b } }) })
    }
    function cacheGet() { try { return JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null') } catch (e) { return null } }
    function cachePut(doc) { try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(doc)) } catch (e) {} }
    function same(a, b) { return JSON.stringify(a) === JSON.stringify(b) }
    function summary(sec, eff) {
      if (sec === 'models') return 'primary ' + eff['models.primary'] + ' · chain ' + (eff['models.triage_chain'] || []).join('→')
      if (sec === 'autopilot') return eff['autopilot.enabled'] ? 'ON' : 'OFF'
      if (sec === 'quiet') return eff['quiet.enabled'] ? eff['quiet.start'] + '–' + eff['quiet.end'] + ' ' + eff['quiet.tz'] : 'off'
      if (sec === 'alerts') return eff['autoresponder.mode'] + ' · hold ' + eff['autoresponder.hold_seconds'] + 's'
      if (sec === 'speaker') return 'volume ' + eff['speaker.volume'] + ' · chimes ' + eff['speaker.chime_volume']
      return ''
    }

    // ── field controls ──────────────────────────────────────────────────────
    function Control(p) {
      var f = p.field, v = p.value, set = p.onChange, dis = !!f.locked
      switch (f.type) {
        case 'bool':
          return h('div', { className: 'seg' },
            h('button', { className: v ? 'on' : '', onClick: function () { set(true) }, disabled: dis, 'aria-pressed': !!v }, 'On'),
            h('button', { className: !v ? 'on' : '', onClick: function () { set(false) }, disabled: dis, 'aria-pressed': !v }, 'Off'))
        case 'enum':
          return h('div', { className: 'seg', role: 'radiogroup', 'aria-label': f.label }, f.options.map(function (o) {
            var below = f.floor && ['GREEN', 'YELLOW', 'RED'].indexOf(o) < ['GREEN', 'YELLOW', 'RED'].indexOf(f.floor)
            return h('button', { key: o, className: v === o ? 'on' : '', disabled: dis || below, role: 'radio', 'aria-checked': v === o,
              onClick: function () { set(o) } }, o)
          }))
        case 'set':
          return h('div', { className: 'seg' }, f.options.map(function (o) {
            var on = (v || []).indexOf(o) >= 0
            return h('button', { key: o, className: on ? 'on' : '', 'aria-pressed': on, onClick: function () {
              set(on ? v.filter(function (x) { return x !== o }) : (v || []).concat([o]))
            } }, o)
          }))
        case 'ordered': {
          var list = v || []
          var rest = f.options.filter(function (o) { return list.indexOf(o) < 0 })
          function move(i, d) { var c = list.slice(); var t = c[i]; c[i] = c[i + d]; c[i + d] = t; set(c) }
          return h('div', { className: 'ord' },
            list.map(function (o, i) {
              return h('div', { key: o },
                h('span', { className: 'chip changed' }, (i + 1) + '. ' + o),
                h('button', { className: 'btn ghost', disabled: i === 0, onClick: function () { move(i, -1) }, 'aria-label': 'move up' }, '↑'),
                h('button', { className: 'btn ghost', disabled: i === list.length - 1, onClick: function () { move(i, 1) }, 'aria-label': 'move down' }, '↓'),
                h('button', { className: 'btn ghost', disabled: list.length <= (f.min || 0), onClick: function () { set(list.filter(function (x) { return x !== o })) }, 'aria-label': 'remove' }, '×'))
            }),
            rest.length ? h('div', { className: 'seg' }, rest.map(function (o) {
              return h('button', { key: o, onClick: function () { set(list.concat([o])) } }, '+ ' + o)
            })) : null)
        }
        case 'int':
        case 'float':
          return h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
            h('input', { type: 'range', min: f.min, max: f.max, step: f.step || 1, value: v, style: { flex: 1 }, 'aria-label': f.label,
              onChange: function (e) { set(f.type === 'int' ? parseInt(e.target.value, 10) : parseFloat(e.target.value)) } }),
            h('input', { type: 'number', min: f.min, max: f.max, step: f.step || 1, value: v, style: { width: '76px' },
              onChange: function (e) { var n = f.type === 'int' ? parseInt(e.target.value, 10) : parseFloat(e.target.value); if (!isNaN(n)) set(n) } }))
        case 'time':
          return h('input', { type: 'time', value: v, onChange: function (e) { set(e.target.value) } })
        default: {
          var listId = 'abls-dl-' + f.key.replace(/\W/g, '-')
          return h(React.Fragment, null,
            h('input', { type: 'text', value: v, list: listId, onChange: function (e) { set(e.target.value) } }),
            h('datalist', { id: listId }, (f.suggestions || (f.type === 'tz' ? ['America/New_York', 'America/Chicago', 'America/Los_Angeles', 'UTC'] : []))
              .map(function (s) { return h('option', { key: s, value: s }) })))
        }
      }
    }

    // ── panel ───────────────────────────────────────────────────────────────
    function Panel() {
      var s = useState(null), doc = s[0], setDoc = s[1]
      var d = useState({}), drafts = d[0], setDrafts = d[1]
      var e = useState({}), errors = e[0], setErrors = e[1]
      var b = useState(null), banner = b[0], setBanner = b[1]
      var o = useState({ quiet: true, alerts: true }), open = o[0], setOpen = o[1]
      var sv = useState(false), saving = sv[0], setSaving = sv[1]
      var t = useState(null), toast = t[0], setToast = t[1]
      var a = useState(null), audit = a[0], setAudit = a[1]
      var ro = useState(false), readOnly = ro[0], setReadOnly = ro[1]
      var toastTimer = useRef(null)

      var load = useCallback(function () {
        return call('/read').then(function (r) {
          if (r.status === 200 && r.body && r.body.schema) {
            setDoc(r.body); cachePut(r.body); setReadOnly(!r.body.ok)
            setBanner(r.body.ok ? null : { kind: 'err', text: r.body.error + '. Writes are refused until the file is fixed.' })
          } else throw new Error('HTTP ' + r.status)
        }).catch(function (err) {
          var cached = cacheGet()
          if (cached) { setDoc(cached); setReadOnly(true); setBanner({ kind: 'warn', text: 'Settings service unreachable (' + err.message + '); showing last loaded values, read-only.' }) }
          else setBanner({ kind: 'err', text: 'Settings service unreachable: ' + err.message })
        })
      }, [])
      useEffect(function () { load() }, [load])

      if (!doc) return h('div', { className: 'abls' }, h('h1', null, 'Settings'), banner ? h('div', { className: 'banner ' + banner.kind }, banner.text) : h('div', { className: 'meta' }, 'Loading…'))

      var eff = doc.effective
      var dirtyKeys = Object.keys(drafts).filter(function (k) { return !same(drafts[k], eff[k]) })
      function val(k) { return k in drafts ? drafts[k] : eff[k] }
      function setVal(k, v) { setDrafts(function (prev) { var n = Object.assign({}, prev); n[k] = v; return n }); setErrors(function (p) { var n = Object.assign({}, p); delete n[k]; return n }) }

      function confirmations() {
        for (var i = 0; i < dirtyKeys.length; i++) {
          var f = doc.schema.find(function (x) { return x.key === dirtyKeys[i] })
          if (!f || !f.confirm) continue
          var msg = typeof f.confirm === 'string' ? f.confirm : f.confirm[drafts[f.key]]
          if (msg && !window.confirm(f.label + ': ' + msg + '\n\nApply this change?')) return false
        }
        return true
      }
      function save() {
        if (!dirtyKeys.length || !confirmations()) return
        var set = {}; dirtyKeys.forEach(function (k) { set[k] = drafts[k] })
        setSaving(true)
        call('/write', { method: 'POST', body: JSON.stringify({ version: doc.version, set: set, unset: [] }) }).then(function (r) {
          setSaving(false)
          if (r.status === 200) {
            setDoc(r.body); cachePut(r.body); setDrafts({}); setErrors({}); setBanner(null); setAudit(null)
            clearTimeout(toastTimer.current)
            setToast({ version: r.body.version, n: (r.body.changed || []).length })
            toastTimer.current = setTimeout(function () { setToast(null) }, 10000)
          } else if (r.status === 409) {
            setBanner({ kind: 'warn', text: 'Changed in another tab/device (now v' + r.body.current + '). Reload: your unsaved edits are kept on top.' })
          } else if (r.status === 422) {
            setErrors(r.body.fields || {}); setBanner({ kind: 'err', text: 'Some values were rejected. See the highlighted fields.' })
          } else setBanner({ kind: 'err', text: (r.body && r.body.error) || ('Save failed (HTTP ' + r.status + ')') })
        }).catch(function (err) { setSaving(false); setBanner({ kind: 'err', text: 'Save failed: ' + err.message }) })
      }
      function undo() {
        call('/undo', { method: 'POST', body: JSON.stringify({ version: toast.version }) }).then(function (r) {
          setToast(null)
          if (r.status === 200) { setDoc(r.body); cachePut(r.body); setAudit(null) }
          else setBanner({ kind: 'warn', text: 'Undo not possible: ' + ((r.body && r.body.error) || r.status) })
        })
      }
      function toggleAudit() {
        if (audit) return setAudit(null)
        call('/audit?limit=20').then(function (r) { setAudit((r.body && r.body.records) || []) })
      }

      return h('div', { className: 'abls' },
        h('h1', null, 'Settings'),
        h('div', { className: 'meta' }, 'v' + doc.version + (doc.updated_at ? ' · saved ' + new Date(doc.updated_at).toLocaleString() + ' by ' + doc.updated_by : ' · all defaults') + ' · ' + doc.path),
        banner ? h('div', { className: 'banner ' + banner.kind, role: 'status' }, banner.text,
          banner.kind !== 'err' || !readOnly ? h('button', { className: 'btn ghost', style: { marginLeft: '8px' }, onClick: load }, 'Reload') : null) : null,
        doc.sections.map(function (sec) {
          var fields = doc.schema.filter(function (f) { return f.section === sec.id })
          var isOpen = !!open[sec.id]
          return h('section', { className: 'sec', key: sec.id },
            h('button', { onClick: function () { var n = Object.assign({}, open); n[sec.id] = !isOpen; setOpen(n) }, 'aria-expanded': isOpen },
              h('strong', null, (isOpen ? '▾ ' : '▸ ') + sec.label), h('span', { className: 'sum' }, summary(sec.id, eff))),
            isOpen ? h('div', { className: 'grid' }, fields.map(function (f) {
              var dirty = f.key in drafts && !same(drafts[f.key], eff[f.key])
              var srcChip = f.locked ? h('span', { className: 'chip lock' }, 'locked ' + f.floor)
                : dirty ? h('span', { className: 'chip dirty' }, 'unsaved')
                : doc.source[f.key] === 'settings' ? h('span', { className: 'chip changed' }, 'changed') : h('span', { className: 'chip' }, 'default')
              return h('div', { className: 'f', key: f.key },
                h('label', null, f.label, srcChip),
                readOnly ? h('div', null, JSON.stringify(eff[f.key])) : h(Control, { field: f, value: val(f.key), onChange: function (v) { setVal(f.key, v) } }),
                f.help ? h('div', { className: 'help' }, f.help) : null,
                errors[f.key] ? h('div', { className: 'err', role: 'alert' }, errors[f.key]) : null)
            })) : null)
        }),
        h('section', { className: 'sec' },
          h('button', { onClick: toggleAudit, 'aria-expanded': !!audit }, h('strong', null, (audit ? '▾ ' : '▸ ') + 'Recent changes'), h('span', { className: 'sum' }, 'audit log')),
          audit ? h('div', { style: { padding: '0 14px 14px' } }, audit.length ? h('table', null, h('tbody', null, audit.map(function (r, i) {
            return h('tr', { key: i }, h('td', null, (r.ts || '').replace('T', ' ').slice(0, 19)), h('td', null, r.key), h('td', null, JSON.stringify(r.old) + ' → ' + JSON.stringify(r.new)), h('td', null, r.by))
          }))) : h('div', { className: 'help' }, 'No changes yet.')) : null),
        dirtyKeys.length && !readOnly ? h('div', { className: 'bar' },
          h('span', { style: { color: 'var(--amber)' } }, dirtyKeys.length + ' unsaved change' + (dirtyKeys.length > 1 ? 's' : '')),
          h('span', null,
            h('button', { className: 'btn ghost', onClick: function () { setDrafts({}); setErrors({}) } }, 'Discard'), ' ',
            h('button', { className: 'btn', onClick: save, disabled: saving }, saving ? 'Saving…' : 'Save'))) : null,
        toast ? h('div', { className: 'toast', role: 'status' }, 'Saved ' + toast.n + ' change' + (toast.n === 1 ? '' : 's') + '.',
          h('button', { className: 'btn ghost', onClick: undo }, 'Undo')) : null)
    }

    function Icon() {
      return h('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, 'aria-hidden': true },
        h('circle', { cx: 12, cy: 12, r: 3 }),
        h('path', { d: 'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z' }))
    }

    function apply(ctx) {
      var reg = function (slot, fn) {
        if (ctx.slots && typeof ctx.slots.inject === 'function') ctx.slots.inject(slot, fn)
        else fn()
      }
      reg('sidebar.panellist', function () {
        return ctx.slots.register({ name: 'sidebar.panellist', id: PANEL_ID, order: 990, label: 'Settings' }, Icon)
      })
      reg('main', function () {
        return ctx.slots.register({ name: 'main', key: PANEL_ID }, Panel)
      })
    }

    return { name: 'dsh-abl-settings-r1', apply: apply, Panel: Panel, Icon: Icon, Control: Control }
  }
})
