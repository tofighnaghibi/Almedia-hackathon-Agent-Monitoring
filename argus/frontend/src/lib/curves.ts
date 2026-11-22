// Monotone cubic interpolation (Fritsch–Carlson): smooth lines that never overshoot the data.
export type Pt = [number, number]

export function monotonePath(pts: Pt[]): string {
  const n = pts.length
  if (n === 0) return ''
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`
  const dx: number[] = []
  const slope: number[] = []
  for (let i = 0; i < n - 1; i++) {
    dx.push(pts[i + 1][0] - pts[i][0])
    slope.push((pts[i + 1][1] - pts[i][1]) / (dx[i] || 1))
  }
  const m: number[] = [slope[0]]
  for (let i = 1; i < n - 1; i++) m.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2)
  m.push(slope[n - 2])
  for (let i = 0; i < n - 1; i++) {
    if (slope[i] === 0) {
      m[i] = 0
      m[i + 1] = 0
      continue
    }
    const a = m[i] / slope[i]
    const b = m[i + 1] / slope[i]
    const h = a * a + b * b
    if (h > 9) {
      const t = 3 / Math.sqrt(h)
      m[i] = t * a * slope[i]
      m[i + 1] = t * b * slope[i]
    }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3
    d += ` C${pts[i][0] + h},${pts[i][1] + m[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - m[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`
  }
  return d
}

// Closed band between an upper and a lower series (same x positions).
export function bandPath(upper: Pt[], lower: Pt[]): string {
  if (!upper.length) return ''
  const top = monotonePath(upper)
  const rev = [...lower].reverse()
  const bottom = monotonePath(rev).replace(/^M/, 'L')
  return `${top} ${bottom} Z`
}
