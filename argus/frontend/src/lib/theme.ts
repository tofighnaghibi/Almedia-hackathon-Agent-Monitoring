import type { CountryStatus, NodeState, Verdict } from './types'

// "Aurora" palette, mirrored from the CSS variables in index.css (MapLibre and SVG need literal values).
// Mint = fine / verified / best option; pink = problem; neutral = explained / normal band;
// violet = glow and focus accent only, never data meaning.
export const AURORA = {
  bg: '#05060D',
  panel: '#0E1020',
  line: '#1E2140',
  fg: '#F1F0FF',
  fg2: '#9A9CC4',
  ok: '#3CF2C4',
  problem: '#FF4D8D',
  neutral: '#7B7FA8',
  accent: '#8B7CFF',
  land: '#23305A',
  border: '#2A2F5C',
  oceanIn: '#141436',
  oceanOut: '#06061A',
} as const

export const MINT = AURORA.ok
export const PINK = AURORA.problem
export const PINK_SOFT = '#FF8FB7' // a lesser problem (watch): still pink, lighter
export const NEUTRAL = AURORA.neutral
export const MUTED = AURORA.fg2

// Globe encoding, quiet → loud: the more a market needs attention, the brighter and warmer it is.
export const FINE = '#3E8F86' // dim, low-saturation teal close to the Earth texture
export const STATUS_COLOR: Record<CountryStatus, string> = {
  ok: FINE,
  watch: PINK_SOFT,
  incident: PINK,
  explained: NEUTRAL,
  waiting: '#4A4E78',
}

export const STATUS_LABEL: Record<CountryStatus, string> = {
  ok: 'As expected',
  watch: 'Unusual · checking',
  incident: 'Needs attention',
  explained: 'Planned change',
  waiting: 'Data still arriving',
}

export const NODE_COLOR: Record<NodeState, string> = {
  root: PINK,
  broken: '#FF6FA3',
  symptom: PINK_SOFT,
  normal: '#3A3F6B',
}

export const VERDICT_COLOR: Record<Verdict, string> = {
  supported: MINT,
  partial: PINK_SOFT,
  rejected: '#4A4E78',
}

// Potential: one-hue ramp from land blue to mint (upside is "good").
export const POTENTIAL_STOPS: [number, string][] = [
  [0, '#0E3530'],
  [0.5, '#1E8F7D'],
  [1, '#3CF2C4'],
]

export function potentialColor(t: number) {
  const x = Math.max(0, Math.min(1, t))
  for (let i = 1; i < POTENTIAL_STOPS.length; i++) {
    const [b, cb] = POTENTIAL_STOPS[i]
    const [a, ca] = POTENTIAL_STOPS[i - 1]
    if (x <= b) return mix(ca, cb, (x - a) / (b - a))
  }
  return POTENTIAL_STOPS[POTENTIAL_STOPS.length - 1][1]
}

function mix(a: string, b: string, t: number) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16))
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16))
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('')
}

export const usd = (v: number, digits = 0) => {
  const a = Math.abs(v)
  const s = a >= 1_000_000 ? `$${(a / 1e6).toFixed(2)}M` : a >= 1000 ? `$${(a / 1000).toFixed(a >= 100_000 ? 0 : 1)}k` : `$${a.toFixed(digits)}`
  return v < 0 ? `−${s}` : s
}

export const signed = (v: number, digits = 1) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(digits)

export const PLATFORM_LABEL: Record<string, string> = { ios: 'iOS', android: 'Android', iOS: 'iOS', Android: 'Android' }

// Growth level 0..1 for an opportunity in $/day: log scale between the smallest and the largest market, so the biggest
// is clearly brightest and the smallest sits near the dim base (a linear scale lets one market wash out the rest).
export function growthLevel(v: number, values: number[]) {
  const pos = values.filter((x) => x > 0)
  if (!pos.length || v <= 0) return 0
  const lo = Math.log(Math.min(...pos))
  const hi = Math.log(Math.max(...pos))
  return hi > lo ? Math.min(1, Math.max(0, (Math.log(v) - lo) / (hi - lo))) : 1
}
