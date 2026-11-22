// A small, deterministic stand-in for the backend world. Shapes match docs/API.md.
// It is not a model of the business: just enough dynamics to make the UI tell the
// demo story (promo explained, bot farm detected, fix approved, prediction verified).
import type {
  CountryDetail,
  CountryState,
  CountryStatus,
  ExplainedChange,
  IncidentDetail,
  IncidentStatus,
  IncidentSummary,
  InjectType,
  Kpis,
  GraphTreeRow,
  MetricGraph,
  TimelineEvent,
  MetricResponse,
  PotentialResponse,
  WorldState,
} from '../lib/types'
import { CORE_EDGES, CORE_LABELS, buildIncidentDetail } from './incidents'
import { resolveLocally, splitNameFormula } from '../lib/metrics'

export const MOCK_COUNTRIES = [
  ['US', 'United States', 39.8, -98.6, 900_000, 1.0, -6],
  ['CA', 'Canada', 56.1, -106.3, 140_000, 0.9, -5],
  ['MX', 'Mexico', 23.6, -102.5, 200_000, 0.3, -6],
  ['BR', 'Brazil', -14.2, -51.9, 300_000, 0.3, -3],
  ['GB', 'United Kingdom', 54.0, -2.0, 250_000, 0.85, 0],
  ['DE', 'Germany', 51.2, 10.4, 200_000, 0.85, 1],
  ['FR', 'France', 46.6, 2.2, 150_000, 0.7, 1],
  ['NL', 'Netherlands', 52.1, 5.3, 70_000, 0.8, 1],
  ['ES', 'Spain', 40.5, -3.7, 110_000, 0.55, 1],
  ['IT', 'Italy', 41.9, 12.6, 100_000, 0.55, 1],
  ['PL', 'Poland', 51.9, 19.1, 90_000, 0.4, 1],
  ['SE', 'Sweden', 60.1, 18.6, 50_000, 0.8, 1],
  ['TR', 'Türkiye', 39.0, 35.2, 100_000, 0.2, 3],
  ['ZA', 'South Africa', -30.6, 22.9, 60_000, 0.25, 2],
  ['IN', 'India', 20.6, 78.9, 350_000, 0.12, 5],
  ['ID', 'Indonesia', -0.8, 113.9, 180_000, 0.15, 7],
  ['PH', 'Philippines', 12.9, 121.8, 160_000, 0.15, 8],
  ['JP', 'Japan', 36.2, 138.3, 120_000, 0.9, 9],
  ['KR', 'South Korea', 35.9, 127.8, 80_000, 0.7, 9],
  ['AU', 'Australia', -25.3, 133.8, 90_000, 0.85, 10],
] as const

export const CATEGORY_KEYS = ['casual', 'strategy', 'fintech', 'streaming', 'shopping', 'surveys']
export const PLATFORMS = ['ios', 'android']
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const TOTAL_DAU = MOCK_COUNTRIES.reduce((s, c) => s + c[4], 0)

function mulberry32(seed: number) {
  return () => {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export type MockEvent = {
  type: InjectType
  country: string
  platform: string
  start: number
  fixedAt?: number
}

type MockIncident = {
  id: string
  kind: 'bot_farm' | 'tracking_break'
  country: string
  platform: string
  detectedAt: number
  appliedAt: number | null
  applied: string | null
  event: MockEvent
}

export class MockWorld {
  t = 336
  rand = mulberry32(42)
  events: MockEvent[] = []
  incidents: MockIncident[] = []
  explained: ExplainedChange[] = []
  extraMetrics: string[] = []
  extraFormulas = new Map<string, { expr: string; parents: string[]; label: string }>()
  noise = new Map<string, number>()

  label() {
    const day = Math.floor(this.t / 24)
    const hh = String(this.t % 24).padStart(2, '0')
    return `Day ${day + 1} · ${DAYS[day % 7]} ${hh}:00 UTC`
  }

  inject(type: InjectType, country: string, platform?: string) {
    const ev: MockEvent = {
      type,
      country,
      platform: platform ?? (type === 'bot_farm' ? 'android' : type === 'tracking_break' ? 'ios' : 'all'),
      start: this.t,
    }
    this.events.push(ev)
    return ev
  }

  step(hours: number) {
    for (let i = 0; i < hours; i++) {
      this.t += 1
      this.noise.clear()
      this.tick()
    }
  }

  // Event severity in z units at the current time (0 = no effect).
  eventZ(ev: MockEvent): number {
    const h = this.t - ev.start
    if (h < 0) return 0
    const inc = this.incidents.find((i) => i.event === ev)
    if (ev.type === 'bot_farm') {
      const ramp = Math.min(9.3, 0.6 + 1.45 * h)
      if (inc?.appliedAt != null) return ramp * Math.exp(-(this.t - inc.appliedAt) / 3)
      return ramp
    }
    if (ev.type === 'tracking_break') {
      const ramp = Math.min(7.4, 1.2 + 2.2 * h)
      if (inc?.appliedAt != null) return ramp * Math.exp(-(this.t - inc.appliedAt) / 4)
      return ramp
    }
    if (ev.type === 'promo') return h < 48 ? 5.1 : 0
    return 0
  }

  tick() {
    for (const ev of this.events) {
      const h = this.t - ev.start
      if (ev.type === 'promo' && h >= 1 && h < 48 && !this.explained.some((e) => e.country === ev.country && e.t >= ev.start)) {
        this.explained.unshift({
          t: this.t,
          country: ev.country,
          event: 'Weekend Boost (games)',
          naive_z: 5.1,
          twin_z: 0.7,
        })
      }
      if ((ev.type === 'bot_farm' || ev.type === 'tracking_break') && !this.incidents.some((i) => i.event === ev)) {
        const z = this.eventZ(ev)
        const prevZ = ev.type === 'bot_farm' ? Math.min(9.3, 0.6 + 1.45 * (h - 1)) : Math.min(7.4, 1.2 + 2.2 * (h - 1))
        if (z >= 4 && prevZ >= 4 - 1.5) {
          this.incidents.unshift({
            id: `inc-${this.incidents.length + 1}`,
            kind: ev.type,
            country: ev.country,
            platform: ev.platform,
            detectedAt: this.t,
            appliedAt: null,
            applied: null,
            event: ev,
          })
        }
      }
    }
  }

  incidentStatus(inc: MockIncident): IncidentStatus {
    if (inc.appliedAt == null) return this.t - inc.detectedAt >= 6 ? 'persisting' : 'open'
    const since = this.t - inc.appliedAt
    if (since >= 48) return 'resolved'
    if (since >= 24) return 'verified'
    return 'fix_applied'
  }

  impact(inc: MockIncident) {
    const z = this.eventZ(inc.event)
    const base = inc.kind === 'bot_farm' ? 812 : 640
    return Math.round(base * (z / (inc.kind === 'bot_farm' ? 9.3 : 7.4)) * 10) / 10
  }

  summary(inc: MockIncident): IncidentSummary {
    const name = MOCK_COUNTRIES.find((c) => c[0] === inc.country)?.[1] ?? inc.country
    const title =
      inc.kind === 'bot_farm'
        ? `Fraud cluster inflating ${name} Android installs`
        : `Advertiser confirms only 15% of ${name} iOS game completions`
    const status = this.incidentStatus(inc)
    const impact = this.impact(inc)
    return {
      id: inc.id,
      title,
      country: inc.country,
      status,
      severity: impact > 400 ? 'high' : impact > 150 ? 'medium' : 'low',
      impact_usd_h: impact,
      risk_7d_usd: inc.kind === 'bot_farm' ? 23_600 : 26_100,
      reaction_h: inc.kind === 'bot_farm' ? 21 : 0,
      status_label: { open: 'Open', persisting: 'Still going', fix_applied: 'Fix applied', verified: 'Fix verified', resolved: 'Resolved' }[status],
      card: this.card(inc, title, status, impact),
    }
  }

  card(inc: MockIncident, title: string, status: IncidentStatus, impact: number) {
    const name = MOCK_COUNTRIES.find((c) => c[0] === inc.country)?.[1] ?? inc.country
    const fraud = inc.kind === 'bot_farm'
    const status_line =
      status === 'open' || status === 'persisting' ? 'Needs a decision' : status === 'fix_applied' ? 'Fix applied · verifying' : status === 'verified' ? 'Prediction verified' : 'Resolved · prediction verified'
    return {
      title,
      looks_like: fraud ? `+$${Math.round(impact)}/h of 'growth'` : `−$${Math.round(impact)}/h of margin`,
      at_stake_7d_usd: fraud ? 23_600 : 26_100,
      what_happened: fraud
        ? `About 88 fake accounts appeared on Android in ${name} and are completing high-paying fintech and strategy-game offers.`
        : `Advertisers in ${name} confirm only 15% of iOS game completions, so we pay rewards without being paid.`,
      why: fraud
        ? "Their stated interests don't match what they do and they share emulator devices (proven, 94% explained)."
        : 'Completions are normal but confirmations collapsed: a tracking break on the advertiser side (proven, 97% explained).',
      what_to_do: fraud
        ? 'Block the cluster: suspend the flagged accounts and their devices. Saves about $23,600 over the next 7 days.'
        : 'Pause the affected offers and notify the advertiser. Saves about $26,100 over the next 7 days.',
      status_line,
    }
  }

  countryNoise(iso: string) {
    let n = this.noise.get(iso)
    if (n === undefined) {
      n = (this.rand() + this.rand() + this.rand() - 1.5) * 1.1
      this.noise.set(iso, n)
    }
    return n
  }

  kpis(iso: string, dau: number, value: number, tz: number, z: number, kind?: InjectType): Kpis {
    const localHour = (((this.t + tz) % 24) + 24) % 24
    const rhythm = 0.55 + 0.45 * Math.sin(((localHour - 9) / 24) * 2 * Math.PI) + 0.25
    const share = dau / TOTAL_DAU
    const completions = (854_000 / 24) * share * rhythm * (1 + this.countryNoise(iso) * 0.02)
    const avgPayout = 3.1 * value + 0.5
    let newAccounts = (dau * 0.012 * rhythm) / 24 * 4
    let comp = completions
    let revenue = completions * avgPayout
    let quality = 0.41
    if (kind === 'bot_farm') {
      newAccounts *= 1 + z * 0.35
      comp *= 1 + z * 0.03
      revenue *= 1 + z * 0.02
      quality *= 1 - z * 0.045
    }
    if (kind === 'tracking_break') revenue *= 1 - z * 0.035
    const rewards = comp * avgPayout * 0.55
    return {
      new_accounts: Math.round(newAccounts),
      completions: Math.round(comp),
      revenue: Math.round(revenue),
      rewards: Math.round(rewards),
      margin: Math.round(revenue * 0.97 - rewards),
      quality_per_completion: Math.round(quality * 1000) / 1000,
    }
  }

  countryState(): CountryState[] {
    return MOCK_COUNTRIES.map(([iso, name, lat, lng, dau, value, tz]) => {
      let status: CountryStatus = 'ok'
      let z = this.countryNoise(iso)
      let kind: InjectType | undefined
      for (const ev of this.events.filter((e) => e.country === iso)) {
        const h = this.t - ev.start
        if (h < 0) continue
        if (ev.type === 'data_delay' && h < 3) {
          status = 'waiting'
          kind = 'data_delay'
        }
        if (ev.type === 'promo' && h >= 1 && h < 48 && status === 'ok') {
          status = 'explained'
          z = 0.7
          kind = 'promo'
        }
        if (ev.type === 'bot_farm' || ev.type === 'tracking_break') {
          const ez = this.eventZ(ev)
          if (ez < 0.8) continue
          kind = ev.type
          const signed = ev.type === 'bot_farm' ? -ez * 0.88 : -ez
          if (Math.abs(signed) > Math.abs(z)) z = signed
          const inc = this.incidents.find((i) => i.event === ev)
          if (inc && inc.appliedAt == null) status = 'incident'
          else if (inc && Math.abs(z) < 2) status = 'ok'
          else if (Math.abs(z) >= 2.5) status = inc ? 'incident' : 'watch'
        }
      }
      return {
        iso,
        name,
        lat,
        lng,
        status,
        health_z: Math.round(z * 100) / 100,
        kpis: this.kpis(iso, dau, value, tz, Math.abs(z), kind),
        potential_usd_day: this.potential(iso),
        potential_pct: this.potentialPct(iso),
      }
    })
  }

  potentialPct(iso: string) {
    const c = MOCK_COUNTRIES.find((x) => x[0] === iso)!
    const dailyMargin = (854_000 * (c[4] / TOTAL_DAU)) * (3.1 * c[5] + 0.5) * 0.42
    return Math.round((this.potential(iso) / dailyMargin) * 1000) / 10
  }

  potential(iso: string) {
    const c = MOCK_COUNTRIES.find((x) => x[0] === iso)!
    const r = mulberry32(iso.charCodeAt(0) * 31 + iso.charCodeAt(1))()
    return Math.round(((c[4] / 1000) * c[5] * (6 + r * 22) + 300) / 10) * 10
  }

  state(): WorldState {
    const incidents = this.incidents.map((i) => this.summary(i))
    const active = incidents.filter((i) => i.status === 'open' || i.status === 'persisting' || i.status === 'fix_applied')
    return {
      clock: { t: this.t, label: this.label() },
      countries: this.countryState(),
      incidents,
      explained: this.explained.slice(0, 10),
      attention: {
        watched_series: 120 + this.extraMetrics.length * 20,
        investigations: active.length,
        l3_runs: this.incidents.filter((i) => i.kind === 'bot_farm').length,
      },
    }
  }

  incident(id: string): IncidentDetail | null {
    const inc = this.incidents.find((i) => i.id === id)
    if (!inc) return null
    const s = this.summary(inc)
    const extra = {
      severity: s.severity,
      impact_usd_h: s.impact_usd_h,
      risk_7d_usd: s.risk_7d_usd,
      reaction_h: s.reaction_h,
      kind: inc.kind === 'bot_farm' ? 'fraud' : 'tracking',
      root: inc.kind === 'bot_farm' ? 'new_accounts' : 'revenue',
      card: s.card,
    }
    return { ...extra, ...buildIncidentDetail({
      id: inc.id,
      title: s.title,
      status: s.status,
      kind: inc.kind,
      country: inc.country,
      countryName: MOCK_COUNTRIES.find((c) => c[0] === inc.country)?.[1] ?? inc.country,
      platform: inc.platform,
      detectedAt: inc.detectedAt,
      applied: inc.applied,
      hoursSinceApply: inc.appliedAt == null ? null : this.t - inc.appliedAt,
      impact: this.impact(inc),
    }) }
  }

  // Same shape as GET /api/graph/tree: outcomes and added metrics at the top, then inputs, depth-first.
  graphTree(): { roots: string[]; rows: GraphTreeRow[] } {
    const g = this.graph()
    const added = new Set(g.nodes.filter((n) => n.origin === 'extension').map((n) => n.id))
    const isInput = new Set(g.edges.filter((e) => !added.has(e.target)).map((e) => e.source))
    const roots = g.nodes.filter((n) => !isInput.has(n.id)).map((n) => n.id)
    const byId = new Map(g.nodes.map((n) => [n.id, n]))
    const rows: GraphTreeRow[] = []
    const seen = new Set<string>()
    const users = (ids: string[]) => ids.some((x) => x === 'new_accounts' || x === 'active_users')
    const walk = (id: string, depth: number, relation: string | null) => {
      const n = byId.get(id)
      const repeat = seen.has(id)
      seen.add(id)
      rows.push({
        depth, id, label: n?.label ?? id, relation,
        relation_label: relation === 'formula' ? 'Formula' : relation === 'mechanism' ? 'Possible cause' : relation ? 'Known event' : null,
        expr: n?.expr ?? null, origin: n?.origin,
        breakdown: users([id, ...(n?.parents ?? [])]) ? 'global → country → platform' : 'global → country → platform → offer category',
        repeat,
      })
      if (!repeat) for (const e of g.edges.filter((e) => e.target === id)) walk(e.source, depth + 1, e.type)
    }
    roots.forEach((r) => walk(r, 0, null))
    return { roots, rows }
  }

  timeline(): { events: TimelineEvent[] } {
    const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    const lab = (t: number) => `${DAYS[Math.floor(t / 24) % 7]} ${String(t % 24).padStart(2, '0')}:00`
    const out: TimelineEvent[] = []
    for (const ev of this.events) {
      if (ev.type === 'promo') out.push({ t: ev.start, time_label: lab(ev.start), kind: 'planned', country: ev.country, label: 'Planned: Weekend Boost (games)' })
      if (ev.type === 'data_delay') out.push({ t: ev.start, time_label: lab(ev.start), kind: 'waiting', country: ev.country, label: 'Data arrived incomplete: waiting, no alert' })
    }
    for (const inc of this.incidents) {
      const s = this.summary(inc)
      out.push({ t: inc.detectedAt - 2, time_label: lab(inc.detectedAt - 2), kind: 'anomaly', country: inc.country, label: 'First unusual hour: the alerting metric left the normal zone' })
      out.push({ t: inc.detectedAt, time_label: lab(inc.detectedAt), kind: 'detected', country: inc.country, label: `Detected: ${s.title}` })
      if (inc.appliedAt != null) out.push({ t: inc.appliedAt, time_label: lab(inc.appliedAt), kind: 'approved', country: inc.country, label: `Approved: ${inc.applied}` })
      if (inc.appliedAt != null && this.t - inc.appliedAt >= 24)
        out.push({ t: inc.appliedAt + 24, time_label: lab(inc.appliedAt + 24), kind: 'verified', country: inc.country, label: 'Prediction verified' })
    }
    return { events: out.filter((e) => e.t <= this.t).sort((a, b) => a.t - b.t) }
  }

  graph(): MetricGraph {
    const parents = (id: string) => CORE_EDGES.filter((e) => e.target === id).map((e) => e.source)
    const nodes: MetricGraph['nodes'] = Object.entries(CORE_LABELS).map(([id, label]) => ({
      id,
      label,
      parents: parents(id),
      edge: id === 'new_accounts' ? 'root' : CORE_EDGES.find((e) => e.target === id)?.type ?? 'mechanism',
      origin: 'core',
    }))
    const edges = [...CORE_EDGES]
    for (const id of this.extraMetrics) {
      const f = this.extraFormulas.get(id) ?? { expr: 'rewards / completions', parents: ['rewards', 'completions'], label: 'Reward cost per completed offer' }
      nodes.push({ id, label: f.label, parents: f.parents, edge: 'formula', origin: 'extension', expr: f.expr })
      for (const p of f.parents) edges.push({ source: p, target: id, type: 'formula' })
    }
    return { nodes, edges, exogenous: [{ id: 'calendar', label: 'Planned events (marketing calendar)' }], levels: {} }
  }

  approve(id: string, fixId: string): IncidentDetail | null {
    const inc = this.incidents.find((i) => i.id === id)
    if (!inc) return null
    if (inc.appliedAt == null) {
      inc.appliedAt = this.t
      inc.applied = fixId
    }
    return this.incident(id)
  }

  country(iso: string): CountryDetail | null {
    const c = MOCK_COUNTRIES.find((x) => x[0] === iso)
    if (!c) return null
    const r = mulberry32(this.t * 97 + iso.charCodeAt(0) * 7 + iso.charCodeAt(1))
    const g = () => (r() + r() + r() - 1.5) * 1.2
    const ev = this.events.find((e) => e.country === iso && (e.type === 'bot_farm' || e.type === 'tracking_break'))
    const z = ev ? this.eventZ(ev) : 0
    const promo = this.events.find((e) => e.country === iso && e.type === 'promo' && this.t - e.start < 48)
    const segments: CountryDetail['segments'] = []
    for (const platform of PLATFORMS) {
      for (const category of CATEGORY_KEYS) {
        const kz = { completions: g(), revenue: g(), rewards: g(), quality_per_completion: g() }
        let dev = g() * 12
        if (ev?.type === 'bot_farm' && platform === ev.platform && (category === 'fintech' || category === 'strategy')) {
          const w = category === 'fintech' ? 1 : 0.62
          kz.completions = 7.1 * w * (z / 9.3) + g() * 0.3
          kz.revenue = 6.0 * w * (z / 9.3) + g() * 0.3
          kz.rewards = 6.5 * w * (z / 9.3) + g() * 0.3
          kz.quality_per_completion = -8.2 * w * (z / 9.3) + g() * 0.3
          dev = 310 * w * (z / 9.3)
        }
        if (ev?.type === 'tracking_break' && platform === ev.platform && (category === 'casual' || category === 'strategy')) {
          const w = category === 'casual' ? 1 : 0.7
          kz.revenue = -7.4 * w * (z / 7.4) + g() * 0.3
          dev = -420 * w * (z / 7.4)
        }
        if (promo && (category === 'casual' || category === 'strategy')) {
          kz.rewards = 0.6 + g() * 0.3
        }
        segments.push({
          platform,
          category,
          kpi_z: Object.fromEntries(Object.entries(kz).map(([k, v]) => [k, Math.round(v * 100) / 100])),
          deviation_usd_h: Math.round(dev * 10) / 10,
        })
      }
    }
    return { iso, name: c[1], segments }
  }

  potentialResponse(): PotentialResponse {
    const cats = ['games', 'shopping', 'fintech', 'streaming', 'surveys']
    const why = ['gaming', 'commuter', 'money-minded', 'binge watcher', 'bargain hunter', 'quick earner']
    return {
      countries: MOCK_COUNTRIES.map(([iso]) => {
        const r = mulberry32(iso.charCodeAt(1) * 13 + iso.charCodeAt(0))
        const a = Math.floor(r() * cats.length)
        const b = (a + 1 + Math.floor(r() * (cats.length - 1))) % cats.length
        const e = Math.floor(r() * why.length)
        return {
          iso,
          potential_usd_day: this.potential(iso),
          potential_pct: this.potentialPct(iso),
          top_categories: [cats[a], cats[b]],
          explain: [why[e], why[(e + 2) % why.length]],
        }
      }),
    }
  }

  addMetric(nameIn: string, description: string, formulaIn?: string): MetricResponse & Record<string, unknown> {
    const known = Object.keys(CORE_LABELS)
    const { name, formula } = splitNameFormula(nameIn, formulaIn ?? (description.includes('/') || description.includes('-') ? description : ''))
    const r = resolveLocally(formula || description, known)
    const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'new_metric'
    const fail = (detail: string, extra: Record<string, unknown> = {}) => ({
      node: null,
      validation: { ok: false, checks: [{ name: 'formula', ok: false, detail }] },
      ...extra,
    })
    if (r.ambiguous.length) return fail('ambiguous word', { ambiguous: Object.fromEntries(r.ambiguous.map((a) => [a.word, a.options])) })
    if (r.unknown.length) return fail(`unknown metric “${r.unknown[0]}”`, { unknown: { [r.unknown[0]]: known.filter((k) => k.startsWith(r.unknown[0].slice(0, 3))).slice(0, 3) } })
    if (r.bad || !r.parents.length) return fail('This is not a formula Argus can compute. Use metric names with + − × ÷.')
    if (this.extraMetrics.includes(id)) return fail(`A metric called “${id}” already exists.`)
    this.extraMetrics.push(id)
    this.extraFormulas.set(id, { expr: r.expr, parents: r.parents, label: name })
    const ratio = r.expr.includes('/')
    return {
      node: { id, label: name, expr: r.expr, parents: r.parents, kind: 'formula', aggregation: ratio ? 'ratio' : 'sum', origin: 'extension' },
      placement: { parents: r.parents, breakdown: ['global', 'country', 'platform', ...(r.parents.some((p) => p === 'new_accounts' || p === 'active_users') ? [] : ['offer category'])] },
      fit: { share: 1 },
      validation: {
        ok: true,
        checks: [
          { name: 'names', ok: true, detail: `uses ${r.parents.join(', ')}` },
          { name: 'identity', ok: true, detail: 'recomputed on 336 h of history; 100% of hours defined' },
          { name: 'monitoring', ok: true, detail: 'baseline learned, now watched in 20 countries' },
        ],
      },
    }
  }

}
