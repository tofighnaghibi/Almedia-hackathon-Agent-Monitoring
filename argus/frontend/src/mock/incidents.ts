// Incident detail templates for the mock world (bot farm and tracking break).
import type { Fix, GraphEdge, GraphNode, IncidentDetail, IncidentStatus, NodeState } from '../lib/types'

type Args = {
  id: string
  title: string
  status: IncidentStatus
  kind: 'bot_farm' | 'tracking_break'
  country: string
  countryName: string
  platform: string
  detectedAt: number
  applied: string | null
  hoursSinceApply: number | null
  impact: number
}

function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
}

function gauss(r: () => number) {
  return Math.sqrt(-2 * Math.log(r() + 1e-9)) * Math.cos(2 * Math.PI * r())
}

export const CORE_EDGES: GraphEdge[] = [
  { source: 'new_accounts', target: 'active_users', type: 'mechanism' },
  { source: 'active_users', target: 'impressions', type: 'mechanism' },
  { source: 'impressions', target: 'starts', type: 'mechanism' },
  { source: 'starts', target: 'completions', type: 'mechanism' },
  { source: 'completions', target: 'revenue', type: 'mechanism' },
  { source: 'completions', target: 'rewards', type: 'mechanism' },
  { source: 'completions', target: 'quality', type: 'mechanism' },
  { source: 'revenue', target: 'margin', type: 'formula' },
  { source: 'rewards', target: 'margin', type: 'formula' },
  { source: 'quality', target: 'quality_per_completion', type: 'formula' },
  { source: 'completions', target: 'quality_per_completion', type: 'formula' },
]

export const CORE_LABELS: Record<string, string> = {
  new_accounts: 'New accounts',
  active_users: 'Active users',
  impressions: 'Offer impressions',
  starts: 'Offer starts',
  completions: 'Completions',
  revenue: 'Advertiser revenue',
  rewards: 'User rewards',
  quality: 'Valuable users',
  margin: 'Contribution margin',
  quality_per_completion: 'Quality / completion',
}

function graph(spec: Record<string, [number, NodeState]>): { nodes: GraphNode[]; edges: GraphEdge[] } {
  return {
    nodes: Object.keys(CORE_LABELS).map((id) => ({
      id,
      label: CORE_LABELS[id],
      z: spec[id]?.[0] ?? 0.3,
      state: spec[id]?.[1] ?? 'normal',
    })),
    edges: CORE_EDGES,
  }
}

function fan(seed: number, daily: number, drift: number[], spread: number): Pick<Fix, 'days' | 'p10' | 'p50' | 'p90'> {
  const days = [1, 2, 3, 4, 5, 6, 7]
  const p50: number[] = []
  const p10: number[] = []
  const p90: number[] = []
  const r = rng(seed)
  let cum = 0
  days.forEach((d, i) => {
    cum += daily + (drift[i] ?? drift[drift.length - 1]) + gauss(r) * daily * 0.01
    const w = spread * Math.sqrt(d) * daily
    p50.push(Math.round(cum))
    p10.push(Math.round(cum - w * 1.15))
    p90.push(Math.round(cum + w * 0.85))
  })
  return { days, p10, p50, p90 }
}

function withDelta(fixes: Omit<Fix, 'delta_p50_usd'>[]): Fix[] {
  const base = fixes[0].p50[6]
  return fixes.map((f) => ({ ...f, delta_p50_usd: Math.round(f.p50[6] - base) }))
}


const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const hourLabel = (t: number) => `${DAY[Math.floor(t / 24) % 7]} ${String(t % 24).padStart(2, '0')}:00`

// Alerting KPI for the last 48 h: twin expectation with a ±2σ band, an excursion starting at `leftAt`,
// and the counterfactual replay (the excursion removed except for a residual share).
function mockSeries(now: number, leftAt: number, direction: 1 | -1, size: number, explained: number, seed: number) {
  const r = rng(seed)
  const series = []
  const cf = []
  for (let t = now - 47; t <= now; t++) {
    const expected = 50 + 16 * Math.sin(((t % 24) - 8) / 24 * 2 * Math.PI)
    const noise = gauss(r) * 2.2
    const h = t - leftAt
    const excess = h >= 0 ? direction * size * Math.min(1, (h + 1) / 4) * expected : 0
    const actual = expected + noise + excess
    series.push({ t, label: hourLabel(t), actual: +actual.toFixed(1), expected: +expected.toFixed(1), lo: +(expected * 0.86).toFixed(1), hi: +(expected * 1.14).toFixed(1) })
    cf.push({ t, value: +(h >= 0 ? expected + noise + excess * (1 - explained) : actual).toFixed(1) })
  }
  return { series, cf }
}

export function buildIncidentDetail(a: Args): IncidentDetail {
  const d = a.detectedAt
  const plat = a.platform === 'ios' ? 'iOS' : 'Android'
  if (a.kind === 'bot_farm') {
    const r = rng(7)
    const points = Array.from({ length: 600 }, (_, i) => {
      const flagged = i < 88
      const c = flagged ? 0.28 + gauss(r) * 0.07 : 0.73 + gauss(r) * 0.11
      const p = flagged ? 0.86 + gauss(r) * 0.06 : 0.36 + gauss(r) * 0.15
      return {
        coherence: Math.round(Math.min(0.99, Math.max(0.02, c)) * 1000) / 1000,
        payout_focus: Math.round(Math.min(0.99, Math.max(0.01, p)) * 1000) / 1000,
        flagged,
        device: flagged ? ['emulator-x86', 'generic_x86_64', 'sdk_gphone'][i % 3] : `Phone ${10 + (i % 7)}`,
        account_age_days: flagged ? 1 : 30 + ((i * 37) % 400),
      }
    })
    const links: { a: number; b: number }[] = []
    for (let i = 0; i < 88 && links.length < 120; i++) for (let j = i + 3; j < 88 && links.length < 120; j += 9) if (i % 3 === j % 3) links.push({ a: i, b: j })
    const daily = 21_400
    const fixes = withDelta([
      {
        id: 'do_nothing',
        name: 'Do nothing',
        description: 'Leave the cluster running. Advertiser chargebacks expected after ~24 h, fintech budget cut after ~48 h.',
        ...fan(11, daily, [900, -2600, -4300, -4600, -4700, -4700, -4700], 0.07),
        advertiser_risk: 'High: chargeback at ~24 h (p50), budget cut at ~48 h',
        events: [
          { after_h: 21, label: 'Advertisers start reclaiming money', probability: 1 },
          { after_h: 45, label: 'Advertiser cuts the budget', probability: 1 },
        ],
      },
      {
        id: 'block_cluster',
        name: 'Block the fake accounts',
        description: 'Suspend the 88 flagged accounts and their device fingerprints; claw back pending rewards.',
        ...fan(12, daily, [-300, -150, -120, -100, -100, -100, -100], 0.025),
        advertiser_risk: 'Low: 30% chance the farm returns at half size within 48 h',
      },
      {
        id: 'verify_new_accounts',
        name: `Verify new ${plat} accounts`,
        description: `Require phone verification for new ${plat} accounts in ${a.countryName} for 7 days.`,
        ...fan(13, daily, [-700, -550, -500, -480, -480, -480, -480], 0.03),
        advertiser_risk: 'Low: ~12% fewer genuine sign-ups during verification',
      },
      {
        id: 'cap_rewards',
        name: 'Cap rewards',
        description: 'Cut reward share to 35% on fintech and strategy offers until the cluster dies out.',
        ...fan(14, daily, [-1200, -1500, -1700, -1800, -1800, -1800, -1800], 0.045),
        advertiser_risk: 'Medium: fraud continues at 40%, genuine starts drop 15%',
      },
    ])
    const recommended = 'block_cluster'
    const appliedFix = fixes.find((f) => f.id === (a.applied ?? recommended))!
    const h = a.hoursSinceApply
    const actual = h != null && h >= 24 ? Math.round(appliedFix.p50[0] + (appliedFix.p90[0] - appliedFix.p50[0]) * 0.32) : null
    return {
      series_meta: { kpi: 'new_accounts', label: `New accounts, ${a.countryName} · Android`, unit: 'per hour' },
      ...(() => {
        const leftAt = d - 3
        const { series, cf } = mockSeries(d + (a.hoursSinceApply ?? 0) + 2, leftAt, 1, 0.9, 0.94, 31)
        return { series, left_zone_at: { t: leftAt, label: hourLabel(leftAt) }, counterfactual: { label: 'Without the 88 flagged fake accounts', series: cf, explained_share: 0.94 } }
      })(),
      id: a.id,
      title: a.title,
      status: a.status,
      country: a.country,
      platform: a.platform,
      category: 'fintech',
      detected_at: d,
      trail: [
        { t: d, level: 'L1', action: `Watch ${a.country} × 6 KPIs against twin forecast`, finding: 'Quality / completion z = −8.2 and rewards z = +6.5 for 2 consecutive hours' },
        { t: d, level: 'L1', action: 'Compare with naive detector and marketing calendar', finding: 'Revenue +18% looks like growth; no planned event covers it' },
        { t: d, level: 'L2', action: 'Break down Δ by platform × category', finding: `${plat} · fintech + strategy games cover 91% of |Δ|` },
        { t: d + 1, level: 'L2', action: 'Walk the causal graph upstream', finding: 'Root = new accounts (z = 9.3 given parents); revenue and margin are symptoms' },
        { t: d + 1, level: 'L3', action: 'Sample 600 users, score taste coherence', finding: '88 new accounts, coherence 0.28 vs 0.73, emulator-x86 devices' },
        { t: d + 1, level: 'L3', action: 'Counterfactual in the twin: remove the cluster', finding: 'Explains 94% of Δmargin → hypothesis supported' },
        { t: d + 2, level: 'L2', action: 'Fast-forward 4 fixes × 40 runs × 7 days', finding: `Block the fake accounts: +$${(fixes[1].delta_p50_usd / 1000).toFixed(1)}k p50 vs do nothing` },
        ...(a.applied ? [{ t: d + 2, level: 'L1' as const, action: 'Human approved fix', finding: `${appliedFix.name} applied to reality` }] : []),
        ...(actual != null ? [{ t: d + 26, level: 'L1' as const, action: 'Verify prediction after 24 h', finding: 'Actual margin inside the p10–p90 band → verified' }] : []),
      ],
      graph: graph({
        new_accounts: [9.3, 'root'],
        active_users: [4.4, 'symptom'],
        impressions: [3.9, 'symptom'],
        starts: [3.6, 'symptom'],
        completions: [7.1, 'symptom'],
        revenue: [6.0, 'symptom'],
        rewards: [6.5, 'symptom'],
        quality: [-8.6, 'broken'],
        margin: [-4.8, 'symptom'],
        quality_per_completion: [-8.2, 'symptom'],
      }),
      contributions: [
        { mechanism: 'new_accounts', value_usd: -268.4 },
        { mechanism: 'quality', value_usd: -141.2 },
        { mechanism: 'rewards', value_usd: -96.3 },
        { mechanism: 'revenue', value_usd: 38.1 },
        { mechanism: 'completions', value_usd: 22.6 },
        { mechanism: 'starts', value_usd: -8.4 },
        { mechanism: 'impressions', value_usd: 4.2 },
        { mechanism: 'active_users', value_usd: -2.9 },
      ],
      hypotheses: [
        { name: 'Fraud cluster (bot farm)', explained: 0.94, verdict: 'supported' },
        { name: 'Organic Android growth', explained: 0.31, verdict: 'partial' },
        { name: 'Planned marketing event', explained: 0.03, verdict: 'rejected' },
        { name: 'Tracking break', explained: 0.02, verdict: 'rejected' },
        { name: 'Data delay', explained: 0.0, verdict: 'rejected' },
      ],
      l3: {
        summary: { n_users: 600, n_flagged: 88, coherence_flagged: 0.28, coherence_others: 0.73, top_devices: ['emulator-x86', 'generic_x86_64', 'sdk_gphone'] },
        points,
        links,
        groups: [
          { device: 'emulator-x86', count: 30 },
          { device: 'generic_x86_64', count: 29 },
          { device: 'sdk_gphone', count: 29 },
        ],
      },
      fixes,
      recommended,
      applied: a.applied,
      verification: {
        status: actual != null ? 'verified' : 'pending',
        predicted: [appliedFix.p10[0], appliedFix.p50[0], appliedFix.p90[0]],
        actual,
      },
      reports: {
        ceo: `A bot farm is farming rewards in ${a.countryName}. Revenue looks up, but it is fake: we pay out about $${Math.round(a.impact)}/h in rewards for users advertisers will charge back. Blocking the 88 accounts protects about $${(fixes[1].delta_p50_usd / 1000).toFixed(0)}k of margin this week and avoids an advertiser budget cut. Recommendation: approve "Block the fake accounts".`,
        manager: `${a.countryName} · ${plat} · fintech and strategy-game offers carry 91% of the deviation. 88 new accounts on emulator devices complete payout-heavy offers with near-zero quality. The fintech advertiser will see a >20% fraud share within 24 h. Block the cluster now; if it returns, switch on phone verification for new ${plat} accounts.`,
        engineer: `Alert: quality_per_completion z=−8.2, rewards z=+6.5 (3 h window, 2 consecutive hours). L2: ${a.country}/${a.platform}/{fintech,strategy} = 91% of |Δ|. Given-parents z: new_accounts 9.3 (root), quality 8.6 (broken mechanism), others < 2. Shapley over 8 mechanisms sums to Δmargin = −$452/h. L3: TasteModel coherence 0.28 vs 0.73, fingerprint expansion over shared device IDs, 88 flagged. Counterfactual v(all) − v(all∖cluster) = 94% of Δ. Fast-forward n=40, 168 h.`,
      },
    }
  }

  // Tracking break: no L3 run (impact × uncertainty low).
  const daily = 31_800
  const fixes = withDelta([
    { id: 'do_nothing', name: 'Do nothing', description: 'Keep serving the offers; the advertiser keeps rejecting 85% of completions.', ...fan(21, daily, [-5600, -5600, -5600, -5600, -5600, -5600, -5600], 0.03), advertiser_risk: 'Users still earn rewards we are not paid for' },
    { id: 'notify_advertiser', name: 'Notify advertiser', description: 'Open a ticket with the advertiser; their SDK fix is expected in 12–30 h.', ...fan(22, daily, [-5600, -1800, -200, 0, 0, 0, 0], 0.05), advertiser_risk: 'Fix timing uncertain (12–30 h)' },
    { id: 'pause_offers', name: 'Pause offers', description: 'Pause the affected iOS game offers until tracking recovers.', ...fan(23, daily, [-2100, -2100, -2100, -2100, -2100, -2100, -2100], 0.02), advertiser_risk: 'Lost volume on paused offers' },
    { id: 'pause_notify', name: 'Pause + notify', description: 'Pause now and notify; resume as soon as confirmations recover.', ...fan(24, daily, [-2100, -700, -100, 0, 0, 0, 0], 0.025), advertiser_risk: 'Low' },
  ])
  const appliedFix = fixes.find((f) => f.id === (a.applied ?? 'pause_notify'))!
  const h = a.hoursSinceApply
  const actual = h != null && h >= 24 ? Math.round(appliedFix.p50[0] - (appliedFix.p50[0] - appliedFix.p10[0]) * 0.25) : null
  return {
    series_meta: { kpi: 'revenue', label: `Revenue, ${a.countryName} · iOS`, unit: 'USD per hour' },
    ...(() => {
      const leftAt = d - 2
      const { series, cf } = mockSeries(d + (a.hoursSinceApply ?? 0) + 1, leftAt, -1, 0.8, 0.97, 41)
      return { series, left_zone_at: { t: leftAt, label: hourLabel(leftAt) }, counterfactual: { label: 'With tracking repaired', series: cf, explained_share: 0.97 } }
    })(),
    id: a.id,
    title: a.title,
    status: a.status,
    country: a.country,
    platform: a.platform,
    category: 'casual',
    detected_at: d,
    trail: [
      { t: d, level: 'L1', action: `Watch ${a.country} × 6 KPIs against twin forecast`, finding: 'Revenue z = −7.4, completions normal' },
      { t: d, level: 'L2', action: 'Break down Δ by platform × category', finding: 'iOS · casual + strategy games cover 96% of |Δ|' },
      { t: d, level: 'L2', action: 'Walk the causal graph upstream', finding: 'Root = revenue mechanism (acceptance 15% vs 97%)' },
      { t: d, level: 'L2', action: 'Skip L3: impact × uncertainty low', finding: 'Single mechanism, users not involved' },
      { t: d + 1, level: 'L2', action: 'Fast-forward 4 fixes × 40 runs × 7 days', finding: 'Pause + notify is best p50 with tight p10' },
      ...(a.applied ? [{ t: d + 1, level: 'L1' as const, action: 'Human approved fix', finding: `${appliedFix.name} applied to reality` }] : []),
      ...(actual != null ? [{ t: d + 25, level: 'L1' as const, action: 'Verify prediction after 24 h', finding: 'Actual margin inside the p10–p90 band → verified' }] : []),
    ],
    graph: graph({
      revenue: [-7.4, 'root'],
      margin: [-6.1, 'symptom'],
    }),
    contributions: [
      { mechanism: 'revenue', value_usd: -598.7 },
      { mechanism: 'completions', value_usd: -11.2 },
      { mechanism: 'rewards', value_usd: 6.3 },
      { mechanism: 'starts', value_usd: 3.1 },
      { mechanism: 'impressions', value_usd: -2.0 },
      { mechanism: 'active_users', value_usd: 1.4 },
      { mechanism: 'new_accounts', value_usd: 0.8 },
      { mechanism: 'quality', value_usd: 0.0 },
    ],
    hypotheses: [
      { name: 'Tracking break (advertiser SDK)', explained: 0.97, verdict: 'supported' },
      { name: 'Data delay', explained: 0.04, verdict: 'rejected' },
      { name: 'Fraud cluster', explained: 0.01, verdict: 'rejected' },
    ],
    l3: null,
    fixes,
    recommended: 'pause_notify',
    applied: a.applied,
    verification: { status: actual != null ? 'verified' : 'pending', predicted: [appliedFix.p10[0], appliedFix.p50[0], appliedFix.p90[0]], actual },
    reports: {
      ceo: `An advertiser in ${a.countryName} is only confirming 15% of iOS game completions, so we pay rewards without being paid: about $${Math.round(a.impact)}/h. Pausing the offers and notifying the advertiser recovers the margin within about a day. Recommendation: approve "Pause + notify".`,
      manager: `${a.countryName} · iOS · casual and strategy games. Completions are normal, confirmations dropped from 97% to 15%: a tracking break on the advertiser side. Pause the affected offers and open a ticket; resume once confirmations recover.`,
      engineer: `Alert: revenue z=−7.4, completions z=0.4. L2: ${a.country}/ios/{casual,strategy} = 96% of |Δ|. Given-parents z: revenue|completions = −7.4 (root, acceptance 0.15 vs μ 0.97). No L3 (impact × uncertainty below threshold). Shapley: revenue mechanism = 98% of Δmargin.`,
    },
  }
}
