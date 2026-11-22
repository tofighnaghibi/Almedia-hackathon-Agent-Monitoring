import { useEffect, useId, useRef, useState } from 'react'
import { useSvgFont } from '../../lib/useSvgFont'
import { bandPath, monotonePath, type Pt } from '../../lib/curves'
import type { SeriesPoint } from '../../lib/types'

type Props = {
  series: SeriesPoint[]
  leftAt?: { t: number; label: string } | null
  // Values to draw instead of series.actual (e.g. the counterfactual); the line animates between them.
  values?: number[]
  width?: number
  height?: number
  fontSize?: number
  showStart?: boolean // label the first hour on the axis
  normal?: number[] // a dashed "normal day" line (what a day without the planned event looks like)
  bandLabel?: string | false // the grey band's label, shown once inside the chart
  markers?: { t: number; label: string }[] // known events (e.g. a planned promo), drawn as quiet grey ticks
}

const ease = (x: number) => 1 - Math.pow(1 - x, 3)

// Grey band = what the twin expected for each hour (lo..hi); line = what happened,
// white inside the band and orange outside it (exact, via an SVG mask of the band).
export default function NormalZoneChart({ series, leftAt, values, width: W = 356, height: H = 120, fontSize = 14, showStart = true, normal, bandLabel = 'Grey = what Argus expected', markers = [] }: Props) {
  const id = useId().replace(/:/g, '')
  const { ref: svgRef, px } = useSvgFont<SVGSVGElement>(W)
  const fs = px(fontSize)
  const target = values ?? series.map((p) => p.actual)
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const start = performance.now()
    const a = from.current.length === target.length ? from.current : target
    let raf = 0
    const tick = (now: number) => {
      const k = ease(Math.min(1, (now - start) / 900))
      const cur = target.map((v, i) => a[i] + (v - a[i]) * k)
      setShown(cur)
      if (k < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.join(',')])

  if (series.length < 2) return null
  const M = { l: 2, r: 2, t: leftAt || markers.length || (bandLabel && showStart) ? fs + 10 : 6, b: fs + 10 }
  const all = [...series.flatMap((p) => [p.lo, p.hi, p.actual]), ...target, ...(normal ?? [])]
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad
  hi += pad
  const n = series.length - 1
  const x = (i: number) => M.l + (i / n) * (W - M.l - M.r)
  const y = (v: number) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b)
  const upper: Pt[] = series.map((p, i) => [x(i), y(p.hi)])
  const lower: Pt[] = series.map((p, i) => [x(i), y(p.lo)])
  const band = bandPath(upper, lower)
  const line = monotonePath(shown.map((v, i) => [x(i), y(v)]))
  const li = leftAt ? series.findIndex((p) => p.t === leftAt.t) : -1
  const labelLeft = li >= 0 && x(li) > W * 0.55
  return (
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="What was expected versus what happened">
      <defs>
        <mask id={`out-${id}`} maskUnits="userSpaceOnUse" x={0} y={0} width={W} height={H}>
          <rect width={W} height={H} fill="white" />
          <path d={band} fill="black" />
        </mask>
      </defs>
      <path d={band} fill="#7B7FA8" opacity={0.22} />
      <path d={monotonePath(series.map((p, i) => [x(i), y(p.expected)]))} fill="none" stroke="#7B7FA8" strokeOpacity={0.35} strokeWidth={1} strokeDasharray="3 4" />
      {normal && normal.length === series.length && (
        <g>
          <path d={bandPath(shown.map((v, i) => [x(i), y(v)] as Pt), normal.map((v, i) => [x(i), y(v)] as Pt))} fill="#8B7CFF" opacity={0.22} />
          <path d={monotonePath(normal.map((v, i) => [x(i), y(v)]))} fill="none" stroke="#B4A9FF" strokeWidth={2} strokeDasharray="5 4" />
          <text x={x(Math.round(n * 0.45))} y={Math.min(H - M.b - 4, y(normal[Math.round(n * 0.45)]) + fs + 4)} fontSize={fs} fill="#B4A9FF" textAnchor="middle">
            normal day
          </text>
        </g>
      )}
      <path d={line} fill="none" stroke="#F1F0FF" strokeWidth={2} strokeLinecap="round" />
      <path d={line} fill="none" stroke="#FF4D8D" strokeWidth={2.4} strokeLinecap="round" mask={`url(#out-${id})`} />
      {li >= 0 && (
        <g>
          <line x1={x(li)} x2={x(li)} y1={fs + 4} y2={H - M.b} stroke="#FF4D8D" strokeOpacity={0.6} strokeDasharray="2 3" />
          <text x={x(li) + (labelLeft ? 4 : -4)} y={fs} fontSize={fs} fill="#FF8FB7" textAnchor={labelLeft ? 'end' : 'start'}>
            Unusual since {leftAt!.label.replace(/^[A-Za-z]{3}\s+/, '')}
          </text>
        </g>
      )}
      {markers.map((m) => {
        const i = Math.max(0, series.findIndex((p) => p.t >= m.t))
        const right = x(i) > W * 0.6
        return (
          <g key={`${m.t}-${m.label}`}>
            <line x1={x(i)} x2={x(i)} y1={fs + 4} y2={H - M.b} stroke="#9A9CC4" strokeOpacity={0.55} strokeDasharray="1.5 3" />
            <circle cx={x(i)} cy={fs + 4} r={2.5} fill="#7B7FA8" />
            <text x={x(i) + (right ? -6 : 6)} y={fs} fontSize={fs} fill="#9A9CC4" textAnchor={right ? 'end' : 'start'}>
              {m.label}
            </text>
          </g>
        )
      })}
      {bandLabel && (
        <text x={x(0) + 2} y={showStart ? fs : H - 3} fontSize={fs} fill="#9A9CC4">
          {bandLabel}
        </text>
      )}
      {showStart && (
        <text x={x(0)} y={H - 3} fontSize={fs} fill="#9A9CC4">
          {series[0].label}
        </text>
      )}
      <text x={x(n)} y={H - 3} fontSize={fs} fill="#9A9CC4" textAnchor="end">
        now
      </text>
    </svg>
  )
}
