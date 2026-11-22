// Metric vocabulary and the "add a metric" client. The backend (POST /api/metrics) resolves a formula written with
// metric ids or business words, asks when a word is ambiguous, checks it on the data and starts watching it.
// `toOutcome` turns whatever the endpoint returns into one of three plain outcomes for the UI.
import type { MetricResponse } from './types'

export const METRIC_PLAIN: Record<string, string> = {
  new_accounts: 'New sign-ups',
  active_users: 'Active users',
  impressions: 'Offer views',
  starts: 'Offer starts',
  completions: 'Completions',
  revenue: 'Revenue',
  rewards: 'Rewards paid',
  quality: 'Valuable users',
  margin: 'Margin',
  quality_per_completion: 'Quality per completion',
  calendar: 'Planned events',
}
const humanize = (s: string) => s.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
export const plainMetric = (id: string, fallback?: string) =>
  METRIC_PLAIN[id] ?? (fallback && !fallback.includes('_') ? fallback : humanize(fallback ?? id))

// Business words → metric ids (mirrors the backend's aliases so the mock and the pre-check agree).
const ALIASES: [string, string][] = [
  ['new accounts', 'new_accounts'], ['new users', 'new_accounts'], ['signups', 'new_accounts'], ['signup', 'new_accounts'],
  ['sign-ups', 'new_accounts'], ['sign ups', 'new_accounts'], ['registrations', 'new_accounts'],
  ['active users', 'active_users'], ['dau', 'active_users'], ['views', 'impressions'], ['offer starts', 'starts'],
  ['completed offers', 'completions'], ['gmv', 'revenue'], ['reward cost', 'rewards'], ['payouts', 'rewards'],
  ['profit', 'margin'], ['contribution margin', 'margin'], ['valuable users', 'quality'],
]
// Words that could mean more than one metric: Argus asks instead of guessing.
export const AMBIGUOUS: Record<string, string[]> = {
  users: ['active_users', 'new_accounts'],
  user: ['active_users', 'new_accounts'],
  accounts: ['new_accounts', 'active_users'],
  conversions: ['completions', 'starts'],
  cost: ['rewards', 'revenue'],
  costs: ['rewards', 'revenue'],
  value: ['revenue', 'quality'],
}

export function splitNameFormula(name: string, formula: string): { name: string; formula: string } {
  if (!formula.trim() && name.includes('=')) {
    const [l, ...r] = name.split('=')
    return { name: l.trim(), formula: r.join('=').trim() }
  }
  return { name: name.trim(), formula: formula.trim() }
}

// Local resolver: used by the mock, and to offer a choice if the backend only reports a generic failure.
export function resolveLocally(formula: string, known: string[]) {
  let s = ` ${formula.toLowerCase()} `.replace(/ divided by | per /g, ' / ').replace(/ minus /g, ' - ').replace(/ plus /g, ' + ').replace(/ times /g, ' * ')
  for (const [phrase, id] of [...ALIASES].sort((a, b) => b[0].length - a[0].length)) s = s.replace(new RegExp(`(?<![a-z_])${phrase.replace('-', '\\-')}(?![a-z_])`, 'g'), ` ${id} `)
  const tokens = s.match(/[a-z_][a-z0-9_]*|\d+\.?\d*|[()+\-*/]/g) ?? []
  const words = tokens.filter((t) => /^[a-z_]/.test(t))
  const ambiguous = words.filter((w) => !known.includes(w) && AMBIGUOUS[w]).map((w) => ({ word: w, options: AMBIGUOUS[w] }))
  const unknown = words.filter((w) => !known.includes(w) && !AMBIGUOUS[w])
  const parents = [...new Set(words.filter((w) => known.includes(w)))]
  const bad = !tokens.length || /^[*/+]|[*/+\-]$/.test(tokens.join(''))
  return { expr: tokens.join(' ').replace(/\s+/g, ' '), parents, ambiguous, unknown, bad }
}

export type ChoiceOption = { id: string; label: string; formula?: string }
export type MetricOutcome =
  | { kind: 'added'; id: string; label: string; expr: string; typed: string; readAs: [string, string][]; parents: string[]; breakdown: string[]; fit: string; countries: number; note: string; checks: { name: string; ok: boolean; detail: string }[] }
  | { kind: 'choose'; message: string; word: string; options: ChoiceOption[] }
  | { kind: 'exists'; message: string; id: string } // same as a metric Argus already watches
  | { kind: 'error'; message: string }

// Shape of POST /api/metrics (docs/API.md "Automatic extension"); older backends return only node + validation.
type Raw = MetricResponse & {
  status?: 'added' | 'ambiguous' | 'invalid' | 'duplicate'
  message?: string
  ambiguity?: { term: string; candidates: string[]; suggested_formulas?: string[] }
  ambiguous?: Record<string, string[]> // older / mock shape
  unknown?: Record<string, string[]>
  placement?: { parents?: string[]; breakdown?: string[] }
  monitoring?: { countries?: number }
  note?: string // "Added because you asked for …"
  fit?: { text?: string; share?: number } | string
}

function fitText(raw: Raw): string {
  if (typeof raw.fit === 'string') return raw.fit
  if (raw.fit?.text) return raw.fit.text
  if (raw.fit?.share != null) return `matches ${Math.round(raw.fit.share * 100)}% of hours`
  const detail = raw.validation?.checks.map((c) => c.detail).join(' ') ?? ''
  const m = detail.match(/(\d+(?:\.\d+)?)% of (?:segment-)?hours/)
  return m ? `matches ${m[1]}% of hours` : 'matches past data'
}

const askWhich = (word: string, ids: string[]) => `“${word}” could mean ${ids.map((o) => plainMetric(o).toLowerCase()).join(' or ')}. Which one?`

export function toOutcome(raw: Raw, formula: string, known: string[]): MetricOutcome {
  const node = raw.node as { id?: string; label?: string; expr?: string; formula?: string; parents?: string[]; breakdown?: string[]; note?: string } | null
  if (node?.id && (raw.status === 'duplicate' || (!raw.status && raw.validation?.checks.some((c) => c.name === 'duplicate' && !c.ok))))
    return { kind: 'exists', id: node.id, message: `Argus already watches this as ${plainMetric(node.id, node.label)}.` }
  if (node?.id && (raw.status === 'added' || !raw.status)) {
    // Words Argus translated, from the formula check: "read 'signups' as new_accounts".
    const readAs = [...(raw.validation?.checks.find((c) => c.name === 'formula')?.detail ?? '').matchAll(/read '([^']+)' as ([a-z_]+)/g)].map((m) => [m[1], m[2]] as [string, string])
    const expr = node.expr ?? node.formula ?? formula
    // What the person typed; when unknown (demo scene), put their words back into the resolved formula.
    const typed = formula || readAs.reduce((f, [w, id]) => replaceWord(f, id, w), expr)
    return {
      kind: 'added',
      id: node.id,
      label: node.label ?? plainMetric(node.id),
      expr,
      typed,
      readAs,
      parents: raw.placement?.parents ?? node.parents ?? [],
      breakdown: raw.placement?.breakdown ?? node.breakdown ?? [],
      fit: fitText(raw),
      countries: raw.monitoring?.countries ?? 20,
      note: raw.note ?? (raw.node as { note?: string } | null)?.note ?? '',
      checks: raw.validation?.checks ?? [],
    }
  }
  // Ambiguous word: offer each meaning; with the documented shape each button re-submits a suggested formula.
  if (raw.ambiguity) {
    const { term, candidates, suggested_formulas: sf } = raw.ambiguity
    return { kind: 'choose', word: term, message: askWhich(term, candidates), options: candidates.map((id, i) => ({ id, label: plainMetric(id), formula: sf?.[i] })) }
  }
  const amb = raw.ambiguous && Object.entries(raw.ambiguous)[0]
  const local = resolveLocally(formula, known)
  const ambWord = amb ? { word: amb[0], options: amb[1] } : !raw.status ? local.ambiguous[0] : undefined
  if (ambWord) return { kind: 'choose', word: ambWord.word, message: askWhich(ambWord.word, ambWord.options), options: ambWord.options.map((id) => ({ id, label: plainMetric(id) })) }
  // "Unknown metric 'revnue'. Did you mean revenue?": a calm choice, not an error.
  const msg = raw.message ?? ''
  const dym = msg.match(/Unknown metric '([^']+)'\.\s*Did you mean ([a-z_]+(?:,\s*[a-z_]+)*)\?/i)
  if (dym) {
    const ids = dym[2].split(/,\s*/)
    return { kind: 'choose', word: dym[1], message: `Argus doesn't know “${dym[1]}”. Did you mean…`, options: ids.map((id) => ({ id, label: plainMetric(id) })) }
  }
  const unk = raw.unknown && Object.entries(raw.unknown).find(([, s]) => s.length)
  if (unk) {
    const ids = unk[1].filter((x) => known.includes(x))
    if (ids.length) return { kind: 'choose', word: unk[0], message: `Argus doesn't know “${unk[0]}”. Did you mean…`, options: ids.map((id) => ({ id, label: plainMetric(id) })) }
  }
  const failed = raw.validation?.checks.find((c) => !c.ok)
  return { kind: 'error', message: msg || failed?.detail || 'That formula could not be checked.' }
}

// Replace one word of the formula by the chosen metric id.
export const replaceWord = (formula: string, word: string, id: string) =>
  formula.replace(new RegExp(`(?<![A-Za-z_])${word}(?![A-Za-z_])`, 'gi'), id)
