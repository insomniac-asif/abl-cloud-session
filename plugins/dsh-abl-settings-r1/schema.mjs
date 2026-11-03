// schema.mjs — the ONE definition of ABL settings: keys, types, defaults, guardrails.
// Shared by the server (validation) and sent to the client (rendering). The Python reader
// (odysseus/src/abl_settings.py) carries the same defaults and floors; a test pins parity.
//
// Values live in settings.json as FLAT dotted keys ("quiet.start": "23:00") so every
// reader, Python or JS, does a dict lookup. Keys absent from the file resolve to the
// default below, so deleting settings.json is a complete rollback.

export const TIERS = ['GREEN', 'YELLOW', 'RED']
export const CATEGORIES = ['fraud', 'security', 'money', 'bills', 'people', 'food', 'parcels']
export const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
export const CHAIN_BACKENDS = ['jev', 'deepseek', 'local', 'light']

// Safety floors: enforced on write (422) AND re-applied by every reader.
export const TIER_FLOORS = { fraud: 'RED', security: 'RED', money: 'YELLOW' }

export function buildSchema(opts = {}) {
  const systems = opts.autopilotSystems ?? ['autopilot', 'morning_briefing', 'research_bridge', 'finance_voice', 'chimes']
  const channels = opts.channels ?? ['sms', 'imessage', 'whatsapp', 'discord', 'email']
  const s = []
  const add = (key, def) => s.push({ key, ...def })

  // ── Models ───────────────────────────────────────────────────────────────
  add('models.primary', { section: 'models', label: 'Primary model (chat)', type: 'string', default: 'llama-27b@127.0.0.1:8085',
    suggestions: opts.modelOptions ?? ['llama-27b@127.0.0.1:8085'], confirm: 'Chat will switch to this model endpoint.' })
  add('models.light', { section: 'models', label: 'Lightweight model', type: 'string', default: 'qwen2.5:3b@127.0.0.1:11434',
    suggestions: opts.lightOptions ?? ['qwen2.5:3b@127.0.0.1:11434'] })
  add('models.triage_chain', { section: 'models', label: 'Triage chain (in order)', type: 'ordered', options: CHAIN_BACKENDS,
    default: ['jev', 'deepseek', 'local', 'light'], min: 1 })
  add('models.review_enabled', { section: 'models', label: 'Review role (desktop 35B)', type: 'bool', default: false })
  add('models.reserve_interactive_slot', { section: 'models', label: 'Reserve one 27B slot for chat', type: 'bool', default: true })

  // ── Autopilot (desktop pulls these) ──────────────────────────────────────
  add('autopilot.enabled', { section: 'autopilot', label: 'Autopilot (master)', type: 'bool', default: true })
  add('autopilot.follow_quiet_hours', { section: 'autopilot', label: 'Respect quiet hours', type: 'bool', default: true })
  for (const name of systems) {
    add(`autopilot.systems.${name}`, { section: 'autopilot', label: name.replace(/_/g, ' '), type: 'bool', default: true })
  }

  // ── Quiet hours (current hard-coded behaviour = defaults) ────────────────
  add('quiet.enabled', { section: 'quiet', label: 'Quiet hours', type: 'bool', default: true })
  add('quiet.start', { section: 'quiet', label: 'Start', type: 'time', default: '23:00' })
  add('quiet.end', { section: 'quiet', label: 'End', type: 'time', default: '09:00' })
  add('quiet.tz', { section: 'quiet', label: 'Time zone', type: 'tz', default: 'America/New_York' })
  add('quiet.days', { section: 'quiet', label: 'Days', type: 'set', options: DAYS, default: [...DAYS] })
  add('quiet.breakthrough', { section: 'quiet', label: 'Alerts that break through', type: 'enum',
    options: ['urgent_red', 'all_red', 'none'], default: 'urgent_red' })
  add('quiet.speaker', { section: 'quiet', label: 'Speaker during quiet hours', type: 'enum',
    options: ['silent', 'chimes_only', 'reduced'], default: 'silent' })
  add('quiet.speaker_volume', { section: 'quiet', label: 'Reduced volume', type: 'int', min: 0, max: 100, default: 20 })
  add('quiet.yellow_policy', { section: 'quiet', label: 'YELLOW drafts during quiet hours', type: 'enum',
    options: ['hold', 'send'], default: 'hold',
    help: 'hold: drafts wait until quiet hours end (+ the normal timer). Nobody reviews at 3 am.' })

  // ── Alerts & autoresponder ───────────────────────────────────────────────
  const tierDefaults = { fraud: 'RED', security: 'RED', money: 'RED', bills: 'YELLOW', people: 'YELLOW', food: 'GREEN', parcels: 'GREEN' }
  for (const c of CATEGORIES) {
    add(`alerts.tiers.${c}`, { section: 'alerts', label: `${c} tier`, type: 'enum', options: TIERS, default: tierDefaults[c],
      floor: TIER_FLOORS[c], locked: TIER_FLOORS[c] === 'RED' })
  }
  add('autoresponder.mode', { section: 'alerts', label: 'Autoresponder mode', type: 'enum', options: ['shadow', 'draft', 'live'],
    default: 'shadow', confirm: { live: 'LIVE mode sends replies without you. Type the channel list carefully.' } })
  add('autoresponder.autosend_channels', { section: 'alerts', label: 'Channels allowed to auto-send', type: 'set',
    options: channels, default: [], confirm: 'Replies on these channels can go out without review.' })
  add('autoresponder.hold_seconds', { section: 'alerts', label: 'YELLOW review window (s)', type: 'int', min: 15, max: 600, default: 60 })

  // ── Speaker (desktop pulls these) ────────────────────────────────────────
  add('speaker.volume', { section: 'speaker', label: 'Volume', type: 'int', min: 0, max: 100, default: 70 })
  add('speaker.chime_volume', { section: 'speaker', label: 'Chime volume', type: 'int', min: 0, max: 100, default: 40 })
  add('speaker.rate', { section: 'speaker', label: 'Speech rate', type: 'float', min: 0.7, max: 1.3, step: 0.05, default: 1.0 })
  add('speaker.voice', { section: 'speaker', label: 'Voice', type: 'string', default: '', suggestions: opts.voices ?? [] })
  add('speaker.max_utterance_s', { section: 'speaker', label: 'Max utterance (s)', type: 'int', min: 5, max: 60, default: 20 })
  return s
}

export const SECTIONS = [
  { id: 'models', label: 'Models' },
  { id: 'autopilot', label: 'Autopilot' },
  { id: 'quiet', label: 'Quiet hours' },
  { id: 'alerts', label: 'Alerts & autoresponder' },
  { id: 'speaker', label: 'Speaker' },
]

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/
const TZ_RE = /^[A-Za-z_]+(\/[A-Za-z0-9_+\-]+){0,2}$|^UTC$/

/** Validate one value against its field. Returns an error string or null. */
export function validateField(field, value) {
  switch (field.type) {
    case 'bool': return typeof value === 'boolean' ? null : 'must be true/false'
    case 'string': return typeof value === 'string' && value.length <= 200 ? null : 'must be a string (≤200 chars)'
    case 'time': return typeof value === 'string' && TIME_RE.test(value) ? null : 'must be HH:MM (24h)'
    case 'tz': return typeof value === 'string' && TZ_RE.test(value) ? null : 'must be an IANA zone like America/New_York'
    case 'int':
      if (!Number.isInteger(value)) return 'must be a whole number'
      return value < field.min || value > field.max ? `must be ${field.min}–${field.max}` : null
    case 'float':
      if (typeof value !== 'number' || !Number.isFinite(value)) return 'must be a number'
      return value < field.min || value > field.max ? `must be ${field.min}–${field.max}` : null
    case 'enum': {
      if (!field.options.includes(value)) return `must be one of ${field.options.join(', ')}`
      if (field.floor && TIERS.indexOf(value) < TIERS.indexOf(field.floor)) {
        return field.locked ? `locked at ${field.floor} (safety floor)` : `cannot be lower than ${field.floor} (safety floor)`
      }
      return null
    }
    case 'set':
    case 'ordered': {
      if (!Array.isArray(value)) return 'must be a list'
      if (new Set(value).size !== value.length) return 'has duplicates'
      const bad = value.filter(v => !field.options.includes(v))
      if (bad.length) return `unknown: ${bad.join(', ')}`
      if (field.min && value.length < field.min) return `needs at least ${field.min}`
      return null
    }
    default: return 'unknown field type'
  }
}
