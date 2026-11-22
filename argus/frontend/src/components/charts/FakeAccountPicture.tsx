import { useEffect, useMemo, useState } from 'react'
import { useSvgFont } from '../../lib/useSvgFont'
import { AURORA, MINT, PINK } from '../../lib/theme'
import type { L3Result } from '../../lib/types'

type Props = { l3: L3Result; width?: number; height?: number }

// "Emu-A1" → "Emulator A"; other names stay as they are.
function deviceName(device: string) {
  if (device === 'other') return 'Other devices'
  const m = device.match(/^emu[-_ ]?([a-z])/i)
  return m ? `Emulator ${m[1].toUpperCase()}` : device
}

function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
}

// Sampled accounts as dots. Real users stay loosely spread; flagged accounts are pulled out into one cluster per
// shared device, with lines between accounts that share it.
export default function FakeAccountPicture({ l3, width: W = 896, height: H = 400 }: Props) {
  const [pulled, setPulled] = useState(false)
  const { ref: svgRef, px } = useSvgFont<SVGSVGElement>(W)
  useEffect(() => {
    const id = window.setTimeout(() => setPulled(true), 350)
    return () => window.clearTimeout(id)
  }, [l3])

  const layout = useMemo(() => {
    const r = rng(17)
    const split = W * 0.58
    // Real users: a calm, evenly spread disc (sunflower layout); position carries no meaning.
    const n = l3.points.length
    const R = Math.min(split - 60, H - 60) / 2
    const cx0 = (split + 10) / 2
    const cy0 = H / 2 + 8
    const loose = l3.points.map((_, i) => {
      const rad = R * Math.sqrt((i + 0.5) / n)
      const a = i * 2.39996 + (r() - 0.5) * 0.08
      return [cx0 + Math.cos(a) * rad, cy0 + Math.sin(a) * rad] as [number, number]
    })
    // Device groups for flagged accounts (largest first); accounts without a group go to "other".
    const flaggedIdx = l3.points.map((p, i) => (p.flagged ? i : -1)).filter((i) => i >= 0)
    const order = (l3.groups ?? []).map((g) => g.device)
    const byDevice = new Map<string, number[]>()
    for (const i of flaggedIdx) {
      const dev = l3.points[i].device && order.includes(l3.points[i].device!) ? l3.points[i].device! : 'other'
      byDevice.set(dev, [...(byDevice.get(dev) ?? []), i])
    }
    const groups = [...byDevice.entries()].sort((a, b) => (a[0] === 'other' ? 1 : b[0] === 'other' ? -1 : b[1].length - a[1].length))
    // Group centres stacked in the right field; members in a sunflower spiral.
    const right0 = split + 60
    const rightW = W - right0 - 20
    const centres = groups.map((_, gi) => {
      const k = groups.length
      const cols2 = k <= 3 ? 1 : 2 // up to three groups stack vertically, so their labels never collide
      const rows2 = Math.ceil(k / cols2)
      // one column: clusters in the left third of the field, labels to their right
      const colX = cols2 === 1 ? right0 + rightW * 0.28 : right0 + ((gi % cols2) + 0.5) * (rightW / cols2)
      return [colX, 30 + (Math.floor(gi / cols2) + 0.5) * ((H - 60) / rows2)] as [number, number]
    })
    const pulledPos = new Map<number, [number, number]>()
    groups.forEach(([, members], gi) => {
      members.forEach((idx, j) => {
        const a = j * 2.39996
        const rad = 8.5 * Math.sqrt(j + 0.5)
        pulledPos.set(idx, [centres[gi][0] + Math.cos(a) * rad, centres[gi][1] + Math.sin(a) * rad])
      })
    })
    const radiusOf = (members: number[]) => 8.5 * Math.sqrt(members.length + 0.5) + 12
    return { loose, pulledPos, groups, centres, radiusOf, split }
  }, [l3, W, H])

  const pos = (i: number): [number, number] => (pulled && layout.pulledPos.get(i)) || layout.loose[i]
  const links = (l3.links ?? []).filter((l) => l.a < l3.points.length && l.b < l3.points.length)

  return (
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Sampled accounts; flagged accounts clustered by shared device">
      <text x={20} y={14} fontSize={px(14)} fill={AURORA.fg2}>
        Real users
      </text>
      {layout.groups.length > 0 && (
        <text x={layout.split + 60} y={14} fontSize={px(14)} fill="#FF8FB7">
          Fake accounts, by shared device
        </text>
      )}
      {/* device links */}
      <g style={{ opacity: pulled ? 0.55 : 0, transition: 'opacity 400ms ease-out 700ms' }}>
        {links.map((l, i) => {
          const [ax, ay] = pos(l.a)
          const [bx, by] = pos(l.b)
          return <line key={i} x1={ax} y1={ay} x2={bx} y2={by} stroke={PINK} strokeWidth={1} />
        })}
      </g>
      {l3.points.map((p, i) => {
        const [px, py] = pos(i)
        return (
          <circle
            key={i}
            r={p.flagged ? 4.2 : 2.6}
            fill={p.flagged ? PINK : MINT}
            opacity={p.flagged ? 0.95 : 0.4}
            style={{ transform: `translate(${px}px, ${py}px)`, transition: p.flagged ? `transform 900ms cubic-bezier(.2,.8,.2,1) ${(i % 20) * 12}ms` : undefined }}
          />
        )
      })}
      {layout.groups.map(([device, members], gi) => {
        const [cx, cy] = layout.centres[gi]
        const rad = layout.radiusOf(members)
        return (
          <g key={device} style={{ opacity: pulled ? 1 : 0, transition: 'opacity 400ms ease-out 900ms' }}>
            <circle cx={cx} cy={cy} r={rad} fill="none" stroke={PINK} strokeOpacity={0.35} strokeDasharray="3 4" />
            <text x={cx + rad + 12} y={cy - 2} fontSize={px(14)} fill={AURORA.fg}>
              {deviceName(device)}
            </text>
            <text x={cx + rad + 12} y={cy + px(14) + 2} fontSize={px(14)} fill="#FF8FB7">
              {members.length} {members.length === 1 ? 'account' : 'accounts'}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
