// Plain-language summaries derived from an incident detail (shared by the issue card and evidence view).
import { PLATFORM_LABEL, usd } from './theme'
import type { ExplainedChange, Fix, IncidentDetail, IncidentSummary, PotentialResponse, WorldState } from './types'

export const isBaseline = (f: Fix) => f.id === 'none' || f.id === 'do_nothing' || /do nothing/i.test(f.name)

export const recommendedFix = (d: IncidentDetail) => d.fixes.find((f) => f.id === (d.applied ?? d.recommended))

export const shortHypothesis = (name: string) => name.replace(/\s*\(.*\)\s*$/, '')

export const needsDecision = (i: IncidentSummary) => i.status === 'open' || i.status === 'persisting'

export function bestHypothesis(d: IncidentDetail) {
  return [...d.hypotheses].sort((a, b) => b.explained - a.explained)[0]
}

// Segment from the L2 trail step ("segments covering 80%: Fintech apps (71%), Strategy games (20%)"), else the category.
export function affectedCategories(d: IncidentDetail): string[] {
  for (const step of d.trail) {
    const m = step.finding.match(/segments covering \d+%:\s*([^;]+)/i)
    if (m) {
      const cats = m[1].split(/,\s*/).map((c) => c.replace(/\s*\(.*?\)\s*/g, '').trim()).filter(Boolean)
      if (cats.length) return cats
    }
  }
  return d.category ? [d.category] : []
}

export const isFraud = (d: IncidentDetail) => d.kind === 'fraud' || d.root === 'new_accounts' || !!d.l3

export function whatHappened(d: IncidentDetail, countryName: string) {
  const plat = PLATFORM_LABEL[d.platform] ?? d.platform
  const cats = affectedCategories(d).slice(0, 2).join(' and ').toLowerCase()
  if (isFraud(d)) return `${plat} sign-ups in ${countryName} jumped. Completions in ${cats} look like growth, but quality fell.`
  if (d.kind === 'tracking' || d.root === 'revenue')
    return `Advertisers confirm far fewer ${plat} completions in ${countryName} (${cats}). Activity is normal, revenue is not.`
  return d.trail[0]?.finding ?? d.title
}

export function why(d: IncidentDetail) {
  const best = bestHypothesis(d)
  if (!best) return { proven: false, text: 'Still looking into it.' }
  const pct = Math.round(best.explained * 100)
  const name = shortHypothesis(best.name)
  if (best.verdict === 'supported') return { proven: true, text: `Proven, ${pct}% explained: ${name.charAt(0).toLowerCase()}${name.slice(1)}.` }
  return { proven: false, text: `Not yet proven. Best lead: ${name.toLowerCase()} (${pct}%).` }
}

export function whatToDo(d: IncidentDetail) {
  const fix = recommendedFix(d)
  if (!fix) return 'No action tested yet.'
  return `${fix.name}: +${usd(fix.delta_p50_usd)} margin this week vs doing nothing.`
}

// Name of a planned event, e.g. "Weekend Boost (Casual games, …): rewards +36% …" → "Weekend Boost".
export const eventName = (event: string) => event.split(/[(:]/)[0].trim()

// One status sentence for the home screen, most urgent first.
// mood drives Argus' eye in the header: problem (a decision is waiting), investigating (watching or checking a fix), calm.
export type StatusSentence = { text: string; tone: 'ok' | 'issue' | 'neutral'; mood: 'calm' | 'problem' | 'investigating' }
export function statusSentence(state: WorldState): StatusSentence {
  const name = (iso: string) => state.countries.find((c) => c.iso === iso)?.name ?? iso
  const decide = [...new Set(state.incidents.filter(needsDecision).map((i) => i.country))]
  if (decide.length) return { text: decide.length === 1 ? `${name(decide[0])} needs attention` : `${decide.length} countries need attention`, tone: 'issue', mood: 'problem' }
  const applied = state.incidents.filter((i) => i.status === 'fix_applied')
  if (applied.length) return { text: `Fix applied in ${name(applied[0].country)} · checking the result`, tone: 'neutral', mood: 'investigating' }
  const watch = state.countries.filter((c) => c.status === 'watch')
  if (watch.length) return { text: watch.length === 1 ? `${watch[0].name} looks unusual · watching` : `${watch.length} countries look unusual · watching`, tone: 'issue', mood: 'investigating' }
  const waiting = state.countries.filter((c) => c.status === 'waiting')
  if (waiting.length) return { text: `Data still arriving from ${waiting.map((c) => c.name).join(', ')}`, tone: 'neutral', mood: 'investigating' }
  const verified = state.incidents.find((i) => i.status === 'verified')
  if (verified) return { text: `Fix verified in ${name(verified.country)}`, tone: 'neutral', mood: 'calm' }
  const resolved = state.incidents.find((i) => i.status === 'resolved' && hoursAgo(i.resolved_t ?? i.resolved_at ?? i.detected_at, state.clock) <= 24)
  const explained = state.countries.filter((c) => c.status === 'explained')
  if (resolved) return { text: `${name(resolved.country)} back to normal`, tone: 'neutral', mood: 'calm' }
  if (explained.length === 1) {
    const ev = state.explained.find((e) => e.country === explained[0].iso)
    return { text: ev ? explainedLine(ev, explained[0].name, state.clock.day_of_week) : `${explained[0].name}: planned change, no alarm`, tone: 'neutral', mood: 'calm' }
  }
  if (explained.length) return { text: `${explained.length} planned changes, as expected`, tone: 'neutral', mood: 'calm' }
  return { text: `All ${state.countries.length} markets as expected`, tone: 'ok', mood: 'calm' }
}

// "Fake-account cluster in Canada (Android)" → "Fake accounts in Canada"; "Tracking break in United Kingdom (iOS, …)"
// → "Tracking break in the United Kingdom".
export function shortTitle(title: string | undefined | null) {
  let t = (title ?? '').replace(/\s*\(.*\)\s*$/, '').replace(/^Fake-account cluster/i, 'Fake accounts')
  t = t.replace(/ in (United Kingdom|United States|Netherlands|Philippines)\b/, ' in the $1')
  return t
}

// The card's two plain lines: what is going on, and why Argus is sure.
export function cardLines(card: { what_happened: string; why: string } | undefined, fraud: boolean): [string, string] {
  if (!card) return ['…', '…']
  let first = card.what_happened.split(/(?<=\.)\s/)[0]
  const n = card.what_happened.match(/About (\d+) fake accounts/i)
  if (fraud && n) first = `About ${Math.round(Number(n[1]) / 10) * 10} fake accounts are farming high-paying offers.`
  const pct = card.why.match(/(\d+)% explained/)
  const clause = card.why
    .replace(/\s*\(.*?\)\s*\.?$/, '')
    .replace(/\s+and they share .*$/i, '')
    .replace(/^Their stated interests/i, 'their interests')
  const proven = /proven/i.test(card.why)
  const body = clause.charAt(0).toLowerCase() + clause.slice(1)
  const second = `${proven ? 'Proven' : 'Likely'}: ${body}${pct ? ` (${pct[1]}% explained)` : ''}.`
  return [first, second]
}

const KPI_PLAIN: Record<string, string> = {
  new_accounts: 'New sign-ups',
  active_users: 'Active users',
  completions: 'Completions',
  revenue: 'Revenue',
  rewards: 'Rewards',
  margin: 'Margin',
  quality_per_completion: 'Quality per completion',
}
export function metricLabel(meta: { kpi: string; unit: string } | undefined) {
  if (!meta) return ''
  const name = KPI_PLAIN[meta.kpi] ?? meta.kpi
  return `${name} ${/usd/i.test(meta.unit) ? '($) ' : ''}per hour`
}

// "Found by Argus at 06:00, nobody asked · advertisers would react in ~22 h". The backend's found_line when present,
// otherwise composed from the detection hour and reaction_h.
export function foundLine(found: string | undefined, detectedAt: number | undefined, reactionH: number | null | undefined, clock: WorldState['clock']) {
  if (found) return found
  if (detectedAt == null) return ''
  const hourNow = clock.utc_hour ?? clock.t % 24
  const h = (((hourNow - (clock.t - detectedAt)) % 24) + 24) % 24
  const first = `Found by Argus at ${String(h).padStart(2, '0')}:00, nobody asked`
  if (reactionH == null) return first
  return `${first} · ${reactionH <= 0 ? 'losses are happening now' : `advertisers would react in ~${Math.round(reactionH)} h`}`
}

// "Germany +31% · planned promo, no alarm": the backend's line when present.
// "Completed offers +10% vs a normal Monday · planned promo" (with the country in front for the status line).
const KPI_WORDS: Record<string, string> = { completions: 'completed offers', revenue: 'revenue', rewards: 'rewards paid', new_accounts: 'new sign-ups', margin: 'margin', impressions: 'offer views', starts: 'offer starts' }
export function explainedLine(e: ExplainedChange, name: string | null, weekday?: string) {
  const pct = e.change_pct != null ? `${e.change_pct > 0 ? '+' : '−'}${Math.abs(Math.round(e.change_pct))}%` : ''
  const noun = /promo|boost|sale|campaign/i.test(e.event) ? 'planned promo' : /late|delay|missing/i.test(e.event) ? 'late data' : `planned ${eventName(e.event).toLowerCase()}`
  const kpi = e.kpi ? (KPI_WORDS[e.kpi] ?? e.kpi.replace(/_/g, ' ')) : null
  if (kpi && pct) {
    const what = `${kpi} ${pct} vs a normal ${weekdayName(weekday)} · ${noun}`
    return name ? `${name}: ${what}` : what.charAt(0).toUpperCase() + what.slice(1)
  }
  if (e.line) return name ? e.line : e.line.replace(/^[^+−\-]*?(?=[+−\-]\d)/, '')
  return `${name ? `${name} ` : ''}${pct} · ${noun}, no alarm`.trim()
}
const weekdayName = (d?: string) => ({ Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday', Sun: 'Sunday' })[d ?? ''] ?? 'day'

// Growth wording, plain: what users like vs what they mostly see, and what showing more of it is worth.
// The backend's line when present; otherwise composed from the same fields.
const listOf = (xs: string[]) => {
  const w = xs.map((x) => x.toLowerCase())
  return w.length > 1 ? `${w.slice(0, -1).join(', ')} and ${w[w.length - 1]}` : (w[0] ?? '')
}
const offersOf = (c: string) => (/ apps$/i.test(c) ? `${c.replace(/ apps$/i, '').toLowerCase()} offers` : c.toLowerCase())
export function growthLine(p: PotentialResponse['countries'][number], name: string) {
  if (p.line) return p.line
  const seen = p.mostly_seen?.filter((c) => !p.top_categories.includes(c)) ?? []
  const like = p.top_categories.length ? `users like ${listOf(p.top_categories)}` : 'users would welcome a different mix'
  return `${name}: ${like}${seen.length ? `, but mostly see ${seen.map(offersOf).join(' and ')}` : ''}. Showing more of what they like: about +${usd(p.potential_usd_day)} a day.`
}

// Hours since a simulated time given as an hour number (t) or a label like "Tue 08:00".
function hoursAgo(at: number | string | null | undefined, clock: WorldState['clock']): number {
  if (at == null) return Infinity
  if (typeof at === 'number') return clock.t - at
  const m = at.match(/([A-Z][a-z]{2})\s+(\d{2}):\d{2}/)
  if (!m || clock.day_of_week == null || clock.utc_hour == null) return Infinity
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const dd = (days.indexOf(clock.day_of_week) - days.indexOf(m[1]) + 7) % 7
  return dd * 24 + (clock.utc_hour - Number(m[2]))
}
