import { usd } from '../../lib/theme'

const NAMES: Record<string, string> = {
  new_accounts: 'New accounts',
  active_users: 'Active users',
  impressions: 'Impressions',
  starts: 'Starts',
  completions: 'Completions',
  revenue: 'Revenue / acceptance',
  rewards: 'Reward share',
  quality: 'Quality',
}

export default function ShapleyBars({ items }: { items: { mechanism: string; value_usd: number }[] }) {
  const sorted = [...items].sort((a, b) => Math.abs(b.value_usd) - Math.abs(a.value_usd))
  const max = Math.max(1, ...sorted.map((i) => Math.abs(i.value_usd)))
  const total = items.reduce((s, i) => s + i.value_usd, 0)
  return (
    <div className="space-y-2">
      {sorted.map((it, i) => {
        const w = (Math.abs(it.value_usd) / max) * 50
        const neg = it.value_usd < 0
        return (
          <div key={it.mechanism} className="grid grid-cols-[150px_1fr_80px] items-center gap-3 text-[14px]">
            <span className="truncate text-fg">{NAMES[it.mechanism] ?? it.mechanism}</span>
            <div className="relative h-3.5">
              <div className="absolute inset-y-0 left-1/2 w-px bg-fg-2" />
              <div
                className="grow-x absolute inset-y-0.5 rounded-sm"
                style={{
                  left: neg ? `${50 - w}%` : '50%',
                  width: `${Math.max(w, 0.4)}%`,
                  background: neg ? '#FF4D8D' : '#3CF2C4',
                  transformOrigin: neg ? 'right' : 'left',
                  animationDelay: `${i * 60}ms`,
                }}
              />
            </div>
            <span className={`num text-right ${neg ? 'text-problem' : 'text-ok'}`}>{usd(it.value_usd)}/h</span>
          </div>
        )
      })}
      <div className="mt-3 border-t border-line pt-3">
        <div className="flex items-baseline justify-between text-[16px]">
          <span className="text-fg">Total change in margin</span>
          <span className="num font-semibold text-fg">{usd(total)}/h</span>
        </div>
        <p className="mt-1 text-[14px] text-fg-2">Each step's share of the change; together they add up to the total.</p>
      </div>
    </div>
  )
}
