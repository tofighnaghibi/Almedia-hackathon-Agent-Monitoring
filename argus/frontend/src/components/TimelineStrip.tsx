import { useLayoutEffect, useRef, useState } from 'react'
import { AURORA, MINT, PINK } from '../lib/theme'
import type { TimelineEvent } from '../lib/types'

type Props = {
  events: TimelineEvent[]
  now: number
  clock?: string // "Mon 06:00": the simulated time, shown at the strip's "now" end
  hours?: number
  countryName: (iso: string) => string
  onOpen: (e: TimelineEvent) => void
}

// Backend labels in plain words for the audience.
function plain(label: string) {
  return label
    .replace(/left the normal zone/gi, 'became unusual')
    .replace(/\bnew accounts\b/gi, 'new sign-ups')
    .replace(/\s*\((Casual|Strategy|Fintech|Streaming|Shopping|Surveys)[^)]*\)/g, '')
}

const KIND_COLOUR: Record<TimelineEvent['kind'], string> = {
  anomaly: PINK,
  detected: PINK,
  verified: MINT,
  approved: AURORA.fg2,
  planned: AURORA.neutral,
  explained: AURORA.neutral,
  waiting: AURORA.neutral,
}

// A thin, quiet record of the last 72 hours: what Argus noticed and decided. Hover for the label; click an
// incident event to open its card.
export default function TimelineStrip({ events, now, hours = 72, clock, countryName, onOpen }: Props) {
  const [active, setActive] = useState<number | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.getBoundingClientRect().width))
    ro.observe(el)
    setWidth(el.getBoundingClientRect().width)
    return () => ro.disconnect()
  }, [])
  const start = now - hours
  const visible = events.filter((e) => e.t >= start && e.t <= now)
  const pos = (t: number) => ((t - start) / hours) * 100
  // Close events are spread at least 14 px apart, in time order (pushed right, then pulled back if they overflow).
  const GAP = 14
  const px = (t: number) => (pos(t) / 100) * width
  const xs: number[] = []
  const sorted = [...visible].sort((a, b) => a.t - b.t)
  sorted.forEach((e, i) => xs.push(i ? Math.max(px(e.t), xs[i - 1] + GAP) : px(e.t)))
  for (let i = xs.length - 1; i >= 0; i--) xs[i] = Math.min(xs[i], (i === xs.length - 1 ? width : xs[i + 1] - GAP))
  const placed = sorted.map((e, i) => ({ e, left: width ? (xs[i] / width) * 100 : pos(e.t), offset: 0 }))
  const days = [...Array(Math.floor(hours / 24) + 1).keys()].map((d) => now - (now % 24) - d * 24).filter((t) => t >= start)
  return (
    <div ref={ref} className="pointer-events-auto relative h-10 w-full select-none" onMouseLeave={() => setActive(null)}>
      <div className="absolute inset-x-0 top-5 h-px bg-line" />
      {days.map((t) => (
        <div key={t} className="absolute top-[14px] h-3 w-px bg-line" style={{ left: `${pos(t)}%` }} />
      ))}
      <div className="pointer-events-auto absolute right-0 top-7 cursor-default whitespace-nowrap text-[14px] text-fg-2" title="Simulated time: the marketplace runs hour by hour">
        {clock ? <span className="num">{clock}</span> : 'now'}
      </div>
      <div className="absolute left-0 top-7 text-[14px] text-fg-2">72 h ago</div>
      {placed.map(({ e, left, offset }, i) => (
        <button
          key={`${e.t}-${e.kind}-${i}`}
          onMouseEnter={() => setActive(i)}
          onFocus={() => setActive(i)}
          onClick={() => onOpen(e)}
          aria-label={`${e.time_label}: ${plain(e.label)}`}
          className="absolute top-5 -translate-x-1/2 -translate-y-1/2 p-1.5"
          style={{ left: `calc(${left}% + ${offset}px)` }}
        >
          <span className="block h-2 w-2 rounded-full transition-transform duration-300 ease-out" style={{ background: KIND_COLOUR[e.kind], transform: active === i ? 'scale(1.8)' : undefined }} />
        </button>
      ))}
      {active != null && placed[active] && (
        <div
          className="fade-in pointer-events-none absolute bottom-9 w-max max-w-[360px] -translate-x-1/2 rounded-lg bg-panel px-3 py-2 text-[14px] text-fg ring-1 ring-line"
          style={{ left: `clamp(120px, calc(${placed[active].left}% + ${placed[active].offset}px), calc(100% - 120px))` }}
        >
          <span className="num text-fg-2">{placed[active].e.time_label}</span> · {countryName(placed[active].e.country)}
          <div>{plain(placed[active].e.label)}</div>
        </div>
      )}
    </div>
  )
}
