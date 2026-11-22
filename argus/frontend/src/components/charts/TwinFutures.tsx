import { useEffect, useId, useState } from 'react'
import { bandPath, monotonePath, type Pt } from '../../lib/curves'
import { useSvgFont } from '../../lib/useSvgFont'

// "Try it in the twin ▶": futures played forward in Argus' digital twin, one line per option with its likely range.
// Used for the fix futures, the growth what-if and the launch forecast, so all three read the same way.
export type TwinLine = {
  id: string
  label: string
  color: string
  strong?: boolean // the option to look at (thick line, labelled band)
  p10: number[]
  p50: number[]
  p90: number[] // one value per x step, starting at x = 0
}

type Props = {
  lines: TwinLine[]
  xLabels: string[] // one per x step, e.g. ['now', 'day 1', …]
  gap?: { from: string; to: string; label: string } // shade between two lines' middles and label the gap at the end
  events?: { x: number; label: string }[] // markers along the first line (e.g. advertiser reactions)
  width?: number
  height?: number
  playKey?: unknown // change to replay the draw-in
  compact?: boolean // narrow cards: no end labels in the plot; a legend and the gap line sit below it
}

export default function TwinFutures({ lines, xLabels, gap, events = [], width: W = 896, height: H = 340, playKey, compact }: Props) {
  const id = useId().replace(/:/g, '')
  const { ref, px } = useSvgFont<SVGSVGElement>(W)
  const FS = px(14)
  const [drawn, setDrawn] = useState(false)
  useEffect(() => {
    setDrawn(false)
    const t = window.setTimeout(() => setDrawn(true), 60)
    return () => window.clearTimeout(t)
  }, [playKey])

  const n = xLabels.length - 1
  const all = lines.flatMap((l) => [...l.p10, ...l.p90])
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad
  hi += pad
  const M = { l: 12, r: compact ? 12 : 220, t: 14 + events.length * (FS + 6), b: FS + 14 }
  const x = (i: number) => M.l + (i / n) * (W - M.l - M.r)
  const y = (v: number) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b)
  const pts = (arr: number[]): Pt[] => arr.map((v, i) => [x(i), y(v)])

  // End labels, nudged apart so they never overlap.
  const labels = lines.map((l) => ({ l, y: y(l.p50[n]) })).sort((a, b) => a.y - b.y)
  for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + FS + 6)

  const from = gap && lines.find((l) => l.id === gap.from)
  const to = gap && lines.find((l) => l.id === gap.to)
  const gapPath = from && to ? bandPath(pts(to.p50), pts(from.p50)) : null
  const strong = lines.find((l) => l.strong)

  const svg = (
    <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Futures played forward in the twin">
      <defs>
        <clipPath id={`draw-${id}`}>
          <rect x={0} y={0} height={H} width={drawn ? W : M.l} style={{ transition: 'width 1.8s cubic-bezier(0.3, 0.7, 0.2, 1)' }} />
        </clipPath>
      </defs>
      {xLabels.map((t, i) => (
        <text key={i} x={x(i)} y={H - 4} fontSize={FS} fill="#9A9CC4" textAnchor={i === 0 ? 'start' : i === n ? 'end' : 'middle'}>
          {t}
        </text>
      ))}
      <g clipPath={`url(#draw-${id})`}>
        {gapPath && <path d={gapPath} fill="#3CF2C4" opacity={0.16} />}
        {lines
          .slice()
          .sort((a, b) => Number(!!a.strong) - Number(!!b.strong))
          .map((l) => (
            <g key={l.id}>
              <path d={bandPath(pts(l.p90), pts(l.p10))} fill={l.color} opacity={l.strong ? 0.16 : 0.08} />
              <path d={monotonePath(pts(l.p50))} fill="none" stroke={l.color} strokeWidth={l.strong ? 3.5 : 2} strokeLinecap="round" className={l.strong ? 'fix-glow' : undefined} />
            </g>
          ))}
        {events.map((e, i) => {
          const base = lines[0]
          const vx = x(e.x)
          const vy = y(base.p50[Math.min(n, Math.round(e.x))])
          const ly = FS + i * (FS + 6)
          return (
            <g key={e.label}>
              <line x1={vx} x2={vx} y1={ly + 6} y2={vy - 7} stroke="#FF8FB7" strokeOpacity={0.5} strokeDasharray="2 3" />
              <circle cx={vx} cy={vy} r={5} fill="#05060D" stroke="#FF4D8D" strokeWidth={2} />
              <text x={vx + 6} y={ly + 4} fontSize={FS} fill="#FF8FB7">
                {e.label}
              </text>
            </g>
          )
        })}
      </g>
      {strong && (
        <text x={x(Math.round(n * 0.6))} y={Math.min(H - M.b - 4, y(strong.p10[Math.round(n * 0.6)]) + FS + 6)} fontSize={FS} fill="#9A9CC4" textAnchor="middle">
          likely range
        </text>
      )}
      {!compact && labels.map(({ l, y: ly }) => (
        <text key={l.id} x={W - M.r + 12} y={ly + 5} fontSize={FS} fill={l.color} fontWeight={l.strong ? 600 : 400} className={drawn ? 'fade-in' : 'opacity-0'}>
          {l.label}
        </text>
      ))}
      {gap && to && !compact && (
        <text x={W - M.r + 12} y={Math.min(H - M.b, labels[labels.length - 1].y + FS * 2.2)} fontSize={FS * 1.15} fill="#3CF2C4" fontWeight={700} className={drawn ? 'fade-in' : 'opacity-0'}>
          {gap.label}
        </text>
      )}
    </svg>
  )
  if (!compact) return svg
  return (
    <div>
      {svg}
      <div className={`mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[14px] ${drawn ? 'fade-in' : 'opacity-0'}`}>
        {lines.map((l) => (
          <span key={l.id} className="flex items-center gap-1.5" style={{ color: l.color }}>
            <span className="h-[3px] w-4 rounded-full" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
        {gap && <span className="ml-auto text-[16px] font-bold text-ok">{gap.label}</span>}
      </div>
    </div>
  )
}

// The button that starts a twin rollout; the chart appears and plays when pressed.
export function TwinButton({ onClick, busy, done }: { onClick: () => void; busy?: boolean; done?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="inline-flex items-center gap-2 rounded-full bg-accent/15 px-4 py-2 text-[16px] text-[#B4A9FF] ring-1 ring-accent/40 hover:bg-accent/25 disabled:opacity-60"
    >
      {busy ? 'Running the twin…' : done ? 'Play it again ▶' : 'Try it in the twin ▶'}
    </button>
  )
}
