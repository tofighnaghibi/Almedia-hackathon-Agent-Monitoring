import type { L3Result } from '../../lib/types'
import { useSvgFont } from '../../lib/useSvgFont'

const W = 440
const H = 236
const M = { l: 50, r: 10, t: 10, b: 44 }

export default function L3Scatter({ l3 }: { l3: L3Result }) {
  const { ref: svgRef, px } = useSvgFont<SVGSVGElement>(W)
  const x = (v: number) => M.l + v * (W - M.l - M.r)
  const y = (v: number) => H - M.b - v * (H - M.t - M.b)
  const flagged = l3.points.filter((p) => p.flagged)
  // One ring around every flagged account (all shared-device groups), not just the densest one.
  const fx0 = Math.min(...flagged.map((p) => p.coherence), 1)
  const fx1 = Math.max(...flagged.map((p) => p.coherence), 0)
  const fy0 = Math.min(...flagged.map((p) => p.payout_focus), 1)
  const fy1 = Math.max(...flagged.map((p) => p.payout_focus), 0)
  const others = l3.points.filter((p) => !p.flagged)
  const s = l3.summary
  return (
    <div>
      <div className="mb-2 grid grid-cols-3 gap-2 text-center">
        <Stat label="Accounts sampled" value={String(s.n_users)} />
        <Stat label="Flagged" value={String(s.n_flagged)} tone="text-problem" />
        <Stat label="Interests fit" value={`${s.coherence_flagged.toFixed(2)} vs ${s.coherence_others.toFixed(2)}`} />
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full">
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={x(v)} x2={x(v)} y1={M.t} y2={H - M.b} stroke="#1E2140" />
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} stroke="#1E2140" />
            <text x={x(v)} y={H - M.b + 17} fontSize={px(14)} fill="#9A9CC4" textAnchor="middle" className="num">
              {v}
            </text>
            <text x={M.l - 6} y={y(v) + 3} fontSize={px(14)} fill="#9A9CC4" textAnchor="end" className="num">
              {v}
            </text>
          </g>
        ))}
        <text x={(W + M.l) / 2} y={H - 2} fontSize={px(14)} fill="#9A9CC4" textAnchor="middle">
          Interests fit what they do →
        </text>
        <text transform={`translate(14,${(H - M.b) / 2}) rotate(-90)`} fontSize={px(14)} fill="#9A9CC4" textAnchor="middle">
          Payout focus →
        </text>
        {others.map((p, i) => (
          <circle key={i} cx={x(p.coherence)} cy={y(p.payout_focus)} r={2} fill="#3CF2C4" opacity={0.3} />
        ))}
        {flagged.map((p, i) => (
          <circle key={`f${i}`} cx={x(p.coherence)} cy={y(p.payout_focus)} r={2.6} fill="#FF4D8D" opacity={0.9} className="fade-in" style={{ animationDelay: `${300 + i * 6}ms` }} />
        ))}
        {flagged.length > 0 && (
          <g>
            <ellipse cx={(x(fx0) + x(fx1)) / 2} cy={(y(fy0) + y(fy1)) / 2} rx={Math.max(26, (x(fx1) - x(fx0)) / 2 + 14)} ry={Math.max(16, (y(fy0) - y(fy1)) / 2 + 12)} fill="none" stroke="#FF6FA3" strokeDasharray="4 3" />
            <text x={Math.min(W - 120, x(fx1) + 20)} y={y(fy1) - 4} fontSize={px(14)} fill="#FF8FB7" fontWeight={600}>
              Fake accounts
            </text>
            <text x={Math.min(W - 120, x(fx1) + 20)} y={y(fy1) + 14} fontSize={px(14)} fill="#9A9CC4">
              {s.top_devices[0]}
            </text>
          </g>
        )}
      </svg>
      <p className="mt-3 text-[16px] leading-relaxed text-fg-2">
        Real people's interests fit what they do (life signals predict what they complete). Bots chase payouts regardless.
      </p>
    </div>
  )
}

function Stat({ label, value, tone = 'text-fg' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="px-2 py-1.5">
      <div className={`num text-[16px] font-semibold ${tone}`}>{value}</div>
      <div className="text-[14px] text-fg-2">{label}</div>
    </div>
  )
}
