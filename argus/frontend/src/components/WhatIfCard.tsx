import { useState } from 'react'
import TwinFutures, { TwinButton } from './charts/TwinFutures'
import type { LaunchForecast } from '../lib/types'

const dollars = (v: number) => (v >= 1000 ? `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `$${v < 10 ? v.toFixed(1) : Math.round(v)}`)
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)

const RISK = {
  low: { word: 'Low', dot: 'bg-ok' },
  medium: { word: 'Medium', dot: 'bg-neutral' },
  high: { word: 'High', dot: 'bg-problem' },
} as const

// "What if we launch in …?": one calm card in plain words. Read-only: nothing in the live world changes.
export default function WhatIfCard({ f, onClose, presenter }: { f: LaunchForecast; onClose: () => void; presenter?: boolean }) {
  const [plays, setPlays] = useState(0)
  const weeks = f.margin_per_day_weekly_usd ?? []
  // "Launching in Argentina: about $25 margin per 1,000 users a day" → title, big number, unit.
  const [title, rest = ''] = f.headline.includes(': ') ? f.headline.split(/:\s(.+)/) : [f.headline, '']
  const m = rest.match(/^(about\s+)?(\+?\$[\d,.]+)\s+(.*)$/)
  const { p10, p50, p90 } = f.margin_per_1000_users_day_usd ?? f.margin_per_day_usd
  const max = p90 * 1.12
  const x = (v: number) => `${(v / max) * 100}%`
  const risk = RISK[f.fraud_risk.level] ?? RISK.medium
  const riskWhy = f.fraud_risk.text.replace(/^[A-Za-z]+:\s*/, '').replace(/,\s*based on.*$/, '')
  const r = f.best_reward
  const uplift = r.uplift_per_1000_users_day_usd
  const backtestMore = f.backtest.margin_per_user?.text
  return (
    <section className="fade-up pointer-events-auto w-[420px] rounded-3xl bg-panel/95 p-7 ring-1 ring-line backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="inline-block rounded-full bg-accent/15 px-2.5 py-0.5 text-[14px] text-accent">What if · nothing changes live</span>
          <h2 className="mt-2 text-[24px] font-semibold leading-tight text-fg">{title}</h2>
        </div>
        <button onClick={onClose} aria-label="Close" className="-mr-2 -mt-1 rounded-full px-2 text-[16px] text-fg-2 hover:text-fg">
          ×
        </button>
      </div>

      {m ? (
        <div className="mt-5">
          <div className="text-[14px] text-fg-2">{m[1] ? 'About' : 'Expected'}</div>
          <div className="flex items-baseline gap-2">
            <span className="num text-[40px] font-semibold leading-none text-fg">{m[2]}</span>
            <span className="text-[16px] text-fg-2">{m[3]}</span>
          </div>
        </div>
      ) : (
        <p className="mt-5 text-[16px] text-fg">{rest}</p>
      )}

      {!presenter && f.total_line && <div className="mt-2 text-[16px] text-fg">{f.total_line}</div>}

      <dl className="mt-6 space-y-3 text-[16px]">
        <div>
          <dt className="mb-1.5 text-fg-2">Best offers:</dt>
          <dd className="flex flex-wrap gap-1.5">
            {f.best_offer_mix.slice(0, 3).map((o) => (
              <span key={o.category} className="rounded-full bg-line/80 px-2.5 py-1 text-fg">{o.category}</span>
            ))}
          </dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="shrink-0 text-fg-2">Fraud risk:</dt>
          <dd className="text-fg">
            <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${risk.dot}`} />
            {risk.word}
            {riskWhy && <span className="text-fg-2"> · {riskWhy}</span>}
          </dd>
        </div>
      </dl>

      {/* Try it in the twin: margin a day over the first 4 weeks as users build up, with its likely range. */}
      {weeks.length > 1 && (
        <div className="mt-5">
          <TwinButton onClick={() => setPlays((n) => n + 1)} done={plays > 0} />
          {plays > 0 && (
            <div className="mt-3">
              <TwinFutures
                lines={[{ id: 'ar', label: 'Margin a day', color: '#3CF2C4', strong: true, p10: [0, ...weeks.map((w) => w.p10)], p50: [0, ...weeks.map((w) => w.p50)], p90: [0, ...weeks.map((w) => w.p90)] }]}
                xLabels={['launch', ...weeks.map((_, i) => `week ${i + 1}`)]}
                width={560}
                height={260}
                playKey={plays}
                compact
              />
            </div>
          )}
        </div>
      )}

      {/* The money, best offers and fraud risk first; everything else on request. */}
      <details className="mt-5 text-[14px]">
        <summary className="cursor-pointer list-none text-fg-2 hover:text-fg">More ▸</summary>
        <div className="mt-3">
          <div className="relative h-2 rounded-full bg-line/70">
            <div className="absolute inset-y-0 rounded-full bg-ok/35" style={{ left: x(p10), width: `calc(${x(p90)} - ${x(p10)})` }} />
            <div className="absolute -inset-y-1 w-[3px] -translate-x-1/2 rounded-full bg-ok" style={{ left: x(p50) }} />
          </div>
          <div className="relative mt-1.5 h-4 text-fg-2">
            <span className="num absolute -translate-x-1/2" style={{ left: x(p10) }}>{dollars(p10)}</span>
            <span className="num absolute -translate-x-1/2" style={{ left: x(p90) }}>{dollars(p90)}</span>
          </div>
          <div className="mt-1 text-fg-2">{f.range_line.replace(/\s*\(p10.p90\)/i, '')}</div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="shrink-0 text-fg-2">Best reward:</span>
            <span className="text-fg">
              <span className="num">{Math.round(r.reward_share * 100)}%</span> of the offer value to the user
              {r.current_share !== r.reward_share && (
                <span className="text-fg-2">
                  {' '}(usual {Math.round(r.current_share * 100)}%{uplift && uplift > 0 ? `, +$${uplift.toFixed(2)} per 1,000 users a day` : ''})
                </span>
              )}
            </span>
          </div>
          <div className="mt-4 border-t border-line pt-3 [&_div]:[text-wrap:balance]">
            <div className="text-fg-2">{f.learned_line}</div>
            <div className="mt-1 text-ok">{f.backtest_line}</div>
            {backtestMore && <div className="mt-0.5 text-fg-2">{cap(backtestMore)}</div>}
          </div>
        </div>
      </details>
    </section>
  )
}
