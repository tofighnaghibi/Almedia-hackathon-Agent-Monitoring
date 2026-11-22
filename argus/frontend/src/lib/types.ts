// Types mirror docs/API.md exactly.

export type CountryStatus = 'ok' | 'watch' | 'incident' | 'explained' | 'waiting'
export type IncidentStatus = 'open' | 'persisting' | 'fix_applied' | 'verified' | 'resolved'
export type Severity = 'low' | 'medium' | 'high'
export type Level = 'L1' | 'L2' | 'L3'
export type NodeState = 'root' | 'broken' | 'symptom' | 'normal'
export type Verdict = 'supported' | 'partial' | 'rejected'
export type InjectType = 'promo' | 'bot_farm' | 'tracking_break' | 'data_delay'

export interface Kpis {
  new_accounts: number
  completions: number
  revenue: number
  rewards: number
  margin: number
  quality_per_completion: number
}

export interface CountryState {
  iso: string
  name: string
  lat: number
  lng: number
  status: CountryStatus
  health_z: number
  kpis: Kpis
  potential_usd_day: number
  potential_pct?: number // potential as % of the country's current daily margin
  attention?: number // 0..1: money at stake × confidence (globe brightness within pink)
}

// Plain-language issue card (incident.card): short sentences, no model terms.
export interface IssueCardText {
  title: string
  looks_like: string // e.g. "+$85/h of 'growth'"
  at_stake_7d_usd: number
  what_happened: string
  why: string
  what_to_do: string
  status_line: string // "Needs a decision" | "Fix applied · verifying" | "Prediction verified" | ...
}

export interface IncidentSummary {
  id: string
  title: string
  country: string
  status: IncidentStatus
  severity: Severity
  impact_usd_h: number
  risk_7d_usd?: number // 7-day margin lost by doing nothing vs the recommended fix
  reaction_h?: number | null // hours until advertisers react; 0 = now; null = n/a
  status_label?: string // "Open" | "Still going" | "Fix applied" | "Fix verified" | "Resolved"
  card?: IssueCardText
  detected_at?: number
  resolved_at?: number | string | null // the backend sends a label, e.g. "Tue 08:00"
  resolved_t?: number | null // the same hour as a number (null while open)
  found_line?: string // "Found by Argus at 06:00, nobody asked · advertisers would react in ~22 h"
  short_title?: string // ≤ ~6 plain words, the card title
  title_detail?: string | null // the rest of the full title, e.g. "Casual-game and strategy-game offers on iOS"
}

export interface ExplainedChange {
  t: number
  country: string
  event: string
  change_pct?: number // KPI move explained by the planned event
  kpi?: string // which KPI moved (e.g. completions)
  line?: string // "Germany +31% · planned promo, no alarm"
  naive_z: number
  twin_z: number
}

export interface WorldState {
  clock: { t: number; label: string; utc_hour?: number; day_of_week?: string; day?: number }
  countries: CountryState[]
  incidents: IncidentSummary[]
  explained: ExplainedChange[]
  attention: { watched_series: number; investigations: number; l3_runs: number; segments_total?: number; segments_opened?: number; users_total?: number; users_checked?: number }
}

export interface TrailStep {
  t: number
  level: Level
  action: string
  finding: string
}

export interface GraphNode {
  id: string
  label: string
  z: number
  state: NodeState
}

export interface GraphEdge {
  source: string
  target: string
  type: string
}

export interface Fix {
  id: string
  name: string
  description: string
  days: number[]
  p10: number[]
  p50: number[]
  p90: number[]
  delta_p50_usd: number
  delta_daily?: { p10: number[]; p50: number[]; p90: number[] } // cumulative margin vs do nothing, days 1..7
  events?: { after_h: number; label: string; probability: number }[] // advertiser reactions in the simulated future
  advertiser_risk: string
}

export interface L3Result {
  summary: {
    n_users: number
    n_flagged: number
    coherence_flagged: number
    coherence_others: number
    top_devices: string[]
  }
  points: { coherence: number; payout_focus: number; flagged: boolean; device?: string; account_age_days?: number }[]
  links?: { a: number; b: number }[] // flagged accounts sharing a device (indices into points)
  groups?: { device: string; count: number }[] // flagged accounts per device, largest first
}

export interface SeriesPoint {
  t: number
  label: string
  actual: number
  expected: number
  lo: number // twin −2σ
  hi: number // twin +2σ
}

export interface IncidentDetail {
  id: string
  title: string
  found_line?: string
  short_title?: string
  title_detail?: string | null
  replay?: ReplayBlock
  status: IncidentStatus
  severity?: Severity
  impact_usd_h?: number
  risk_7d_usd?: number
  reaction_h?: number | null
  kind?: string // "fraud" | "tracking" | ...
  root?: string
  card?: IssueCardText
  series_meta?: { kpi: string; label: string; unit: string }
  series?: SeriesPoint[] // alerting KPI for the root segment, last 48 h
  left_zone_at?: { t: number; label: string } | null
  counterfactual?: { label: string; series: { t: number; value: number }[]; explained_share: number } | null
  country: string
  platform: string
  category: string
  detected_at: number
  trail: TrailStep[]
  graph: { nodes: GraphNode[]; edges: GraphEdge[] }
  contributions: { mechanism: string; value_usd: number }[]
  hypotheses: { name: string; explained: number; verdict: Verdict }[]
  l3: L3Result | null
  fixes: Fix[]
  recommended: string
  applied: string | null
  verification: { status: 'pending' | 'verified' | 'missed'; predicted: number[] | null; actual: number | null }
  reports: { ceo: string; manager: string; engineer: string; source?: string }
}

export interface CountryDetail {
  iso: string
  name: string
  segments: {
    platform: string
    category: string
    kpi_z: Record<string, number>
    kpi_ratio?: Record<string, number> // observed / usual
    deviation_usd_h: number
  }[]
  // Simplified country view (docs/API.md "summary").
  summary?: {
    status: CountryStatus
    verdict: string
    numbers: { label: string; value: number; usual: number; unit: string; line: string }[]
    series: { metric: string; unit: string; kpi?: string; change_label?: string; points: { t: number; label: string; value: number; usual: number; lo: number; hi: number; normal?: number }[]; events?: { t: number; label: string }[] }
    moving: { line: string; platform: string; category: string | null; kpi: string; ratio: number }[]
    unusual?: { line: string; platform: string; categories: string[] | null; kpi: string; ratio: number; z: number; view: 'who' | 'why' }[] // all unusual segments, ranked
    segments_total?: number
  }
}

export interface PotentialResponse {
  countries: {
    iso: string
    potential_usd_day: number
    potential_pct?: number
    current_margin_usd_day?: number
    top_categories: string[] // what users there like (under-served and locally distinctive)
    explain: string[]
    mostly_seen?: string[] // what they mostly see today
    mix?: { category: string; liked: number; seen: number }[] // share users would choose vs share of offers shown
    line?: string // "Brazil: users like casual games and surveys, but mostly see fintech offers. Showing more …"
  }[]
  summary_line?: string // "Showing each country more of what its users like: about +$X a day in total · biggest in …"
}

export interface MetricResponse {
  node: Record<string, unknown> | null
  validation: { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] }
}

export interface EdgeType {
  type: string // 'formula' | 'breakdown' | 'mechanism' | 'exogenous'
  label: string // 'Formula' | 'Breakdown' | 'Possible cause' | 'Known event'
  meaning?: string
}

export interface MetricGraph {
  nodes: { id: string; label: string; parents: string[]; edge: string; origin?: string; expr?: string | null; note?: string }[]
  edges: (GraphEdge & { label?: string })[]
  edge_types?: EdgeType[]
  exogenous: { id: string; label: string }[]
  levels: Record<string, string>
}

export interface GraphTreeRow {
  depth: number
  id: string
  label: string
  relation: string | null // null for roots, else 'formula' | 'mechanism' | 'exogenous'
  relation_label: string | null
  expr?: string | null
  unit?: string
  origin?: string
  breakdown?: string // "global → country → platform → offer category"
  repeat?: boolean
}

export interface TimelineEvent {
  t: number
  time_label: string
  kind: 'planned' | 'anomaly' | 'detected' | 'waiting' | 'approved' | 'verified' | 'explained'
  country: string
  label: string
}

export interface DemoResponse {
  scene: string
  state: WorldState
  incident_id?: string
  metric?: MetricResponse // new_metric scene: the metric it added
  frames?: DemoFrame[] // ?frames=1: one per simulated hour the scene advanced through (time-lapse)
}

export interface DemoFrame {
  t: number
  clock: WorldState['clock']
  countries: (Partial<CountryState> & { iso: string })[]
  incidents?: (Partial<IncidentSummary> & { id: string })[]
}

export const DEMO_SCENES = [
  'start',
  'promo',
  'bot_farm',
  'investigate',
  'approve',
  'verify',
  'new_metric',
  'tracking_break',
] as const
export type DemoScene = (typeof DEMO_SCENES)[number]

// POST /api/what_if/launch: read-only forecast for entering a new country (docs/API.md "Enter a new country").
type Range = { p10: number; p50: number; p90: number }
export interface LaunchForecast {
  country: { iso: string; name: string; region: string; lat: number; lng: number }
  horizon_days: number
  margin_per_1000_users_day_usd?: Range // the per-user prediction (headline)
  users?: { daily_users: number; assumed: boolean; source: string }
  margin_per_day_usd: Range // total at that user count
  margin_per_day_weekly_usd?: Range[] // margin a day in weeks 1–4 as users build up
  best_offer_mix: { category: string; margin_share: number; taste: number }[]
  best_reward: { reward_share: number; current_share: number; uplift_per_1000_users_day_usd?: number }
  fraud_risk: { level: 'low' | 'medium' | 'high'; farm_chance_4w: number; expected_loss_usd_4w: number; text: string }
  learned_from: { iso: string; name: string; weight: number }[]
  backtest: { n: number; margin_per_user?: { text: string }; offers?: { text: string }; reward?: { text: string } }
  headline: string
  range_line: string
  total_line?: string
  learned_line: string
  backtest_line: string
}

// "Watch Argus think": how Argus found the issue, step by step (incident detail `replay`, docs/API.md).
export interface ReplayBlock {
  l1?: { sentence: string; metric?: string; series?: SeriesPoint[]; left_at?: { t: number; label: string } | null }
  l2?: { sentence: string; cells?: { platform: string; category: string; z: number; driving: boolean }[] }
  walk?: { sentence: string; steps: { node: string; verdict: 'fine' | 'affected' | 'cause' }[] }
  l3?: { sentence: string } | null
  proof?: { sentence: string } | null
}

// POST /api/what_if/offer_mix: 7 days in the twin, current offers vs a better-fit mix (cumulative margin, USD).
export interface OfferMixWhatIf {
  country: string
  days: number[]
  current: { p10: number[]; p50: number[]; p90: number[] }
  better: { p10: number[]; p50: number[]; p90: number[] }
  gain_week_usd: { p10: number; p50: number; p90: number }
  gain_day_usd: number
  line: string
}
