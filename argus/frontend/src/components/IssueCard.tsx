import { usd } from '../lib/theme'
import type { IncidentDetail, IncidentSummary } from '../lib/types'
import { cardLines, isFraud, metricLabel, recommendedFix, shortTitle } from '../lib/incident'
import NormalZoneChart from './charts/NormalZoneChart'

type Props = {
  summary: IncidentSummary
  detail: IncidentDetail | null // arrives later (the first fetch can take a few seconds)
  countryName: string
  checkedLine?: string // "Argus checked 1 of 240 segments and 600 of 8,816,000 users to find this."
  foundLine?: string // "Found by Argus at 06:00, nobody asked · advertisers would react in ~22 h"
  approving?: boolean
  onApprove?: (fixId: string) => void
  onEvidence: (tab: 'why' | 'who' | 'whatif') => void
  presenter?: boolean // demo: one big headline, secondary lines hidden
  pressing?: boolean // demo: the primary button is visibly pressed before the fixes open
  onReplay?: () => void // "How did Argus find this?" replay
  onClose: () => void
}

// "+$85/h of 'growth'" → "+$85" (shown as "Looks like +$85/h")
function amountOf(s: string): string {
  const m = s.match(/^\s*([+−-]?\$[\d,.]+[kM]?)/)
  return m ? m[1].replace('-', '−') : s
}

// L2 / manager: one decision, in plain words. No charts here; the evidence is one click away.
export default function IssueCard({ summary, detail, countryName, foundLine, checkedLine, onEvidence, onReplay, pressing, presenter, onClose }: Props) {
  const card = summary.card ?? detail?.card
  const status = summary.status
  const decided = status !== 'open' && status !== 'persisting'
  const amount = amountOf(card?.looks_like ?? `${usd(summary.impact_usd_h)}`)
  const atStake = card?.at_stake_7d_usd ?? summary.risk_7d_usd ?? 0
  const [line1, line2] = cardLines(card, detail ? isFraud(detail) : /fake/i.test(card?.title ?? summary.title ?? summary.short_title ?? ''))
  // Status chip from the issue tracker: pink only while open, mint once verified or resolved.
  const recovered = status === 'resolved' && detail != null && !detail.applied
  const label = recovered ? 'Recovered without action' : (summary.status_label ?? card?.status_line ?? 'Open')
  const chip = !decided ? 'bg-problem/15 text-problem' : status === 'verified' || status === 'resolved' ? 'bg-ok/15 text-ok' : 'bg-neutral/15 text-fg-2'

  return (
    <section className="fade-up pointer-events-auto max-h-[calc(100vh-7.5rem)] w-[420px] overflow-y-auto rounded-3xl bg-panel/95 px-6 py-5 ring-1 ring-line backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className={`inline-block rounded-full px-2.5 py-0.5 text-[14px] ${chip}`}>{label}</span>
          <h2 className="mt-2 text-[24px] font-semibold leading-tight text-fg">{summary.short_title ?? detail?.short_title ?? shortTitle(card?.title ?? summary.title ?? '')}</h2>
          {!presenter && (summary.title_detail ?? detail?.title_detail) && <p className="mt-1.5 text-[14px] leading-snug text-fg-2">{summary.title_detail ?? detail?.title_detail}</p>}
          {/* In the demo these two lines stay (the key points), trimmed to their first part. */}
          {foundLine && <p className="mt-1 text-[14px] leading-snug text-fg-2">{presenter ? foundLine.split(' · ')[0] : foundLine}</p>}
          {checkedLine && <p className="mt-1 text-[14px] leading-snug text-fg-2">{presenter ? checkedLine.replace(/ to find this\.$/, '') : checkedLine}</p>}
          {!presenter && onReplay && detail && (
            <button onClick={onReplay} className="mt-2 inline-flex items-center gap-2 rounded-full bg-accent/15 px-3 py-1 text-[14px] text-accent ring-1 ring-accent/30 hover:bg-accent/25">
              How did Argus find this? <span aria-hidden>▶</span>
            </button>
          )}
        </div>
        <button onClick={onClose} aria-label="Close" className="-mr-2 -mt-1 rounded-full px-2 text-[16px] text-fg-2 hover:text-fg">
          ×
        </button>
      </div>

      {!decided ? (
        <>
          <div className="mt-4 grid grid-cols-2 gap-6">
            <Big label="Looks like" value={`${amount}/h`} />
            <Big label="Doing nothing costs" value={usd(atStake)} note="this week" tone="text-[#FF8FB7]" />
          </div>
          {detail?.series && detail.series.length > 1 && (
            <figure className="mt-3">
              <div className="mb-1 text-[14px] text-fg-2">{metricLabel(detail.series_meta)}</div>
              <NormalZoneChart series={detail.series.slice(-24)} leftAt={detail.left_zone_at} width={372} height={76} showStart={false} />
            </figure>
          )}
          <div className="mt-3 space-y-1 text-[16px] leading-snug">
            <p className="text-fg">{line1}</p>
            {!presenter && <p className="text-fg-2">{line2}</p>}
          </div>
        </>
      ) : (
        <Outcome detail={detail && (detail.status === status || !['verified', 'resolved'].includes(status) || detail.verification.status !== 'pending') ? detail : null} stale={!!detail && ['verified', 'resolved'].includes(status) && detail.status !== status && detail.verification.status === 'pending'} countryName={countryName} statusLine={card?.status_line} recovered={recovered} />
      )}

      {/* The decision first (the futures in the twin, with Approve); the causal graph and the users as secondary views. */}
      <button
        onClick={() => onEvidence('whatif')}
        className={`mt-4 w-full rounded-xl py-3 text-[16px] font-semibold transition ${decided ? 'bg-line/80 text-fg hover:bg-line' : 'bg-ok text-bg hover:brightness-110'} ${pressing ? 'press-hint' : ''}`}
      >
        {decided ? 'See the fixes ›' : 'See fixes & approve ›'}
      </button>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {(
          [
            ['why', 'Why?'],
            ['who', 'Who?'],
          ] as const
        ).map(([tab, label]) => (
          <button key={tab} onClick={() => onEvidence(tab)} className="rounded-xl py-2 text-[16px] text-fg ring-1 ring-line transition hover:bg-line/60">
            {label}
          </button>
        ))}
      </div>
    </section>
  )
}

function Big({ label, value, note, tone = 'text-fg' }: { label: string; value: string; note?: string; tone?: string }) {
  return (
    <div>
      <div className="text-[14px] text-fg-2">{label}</div>
      <div className={`num mt-1 text-[40px] font-semibold leading-none tracking-tight ${tone}`}>{value}</div>
      {note && <div className="mt-2 text-[14px] text-fg-2">{note}</div>}
    </div>
  )
}


// After approval: the verification state replaces the decision.
function Outcome({ detail, stale, countryName, statusLine, recovered }: { detail: IncidentDetail | null; stale?: boolean; countryName: string; statusLine?: string; recovered?: boolean }) {
  if (stale) return <div className="mt-6 animate-pulse text-[16px] text-fg-2">Checking the result…</div>
  if (recovered)
    return (
      <div className="mt-6">
        <div className="text-[24px] font-semibold leading-snug text-ok">{countryName} is back to normal.</div>
        <p className="mt-2 text-[14px] text-fg-2">It recovered on its own; no action was needed.</p>
      </div>
    )
  const v = detail?.verification
  const fix = detail ? recommendedFix(detail) : undefined
  const band = v?.predicted ?? (fix ? [fix.p10[0], fix.p50[0], fix.p90[0]] : null)
  if (v?.status === 'verified')
    return (
      <div className="mt-6">
        <div className="text-[24px] font-semibold leading-snug text-ok">
          {countryName} is back to normal.
          <br />
          The prediction came true.
        </div>
        {band && v.actual != null && <BandBar band={band} actual={v.actual} />}
        <p className="mt-4 text-[14px] text-fg-2">
          {fix ? `${fix.name} was applied. ` : ''}Margin on the first day landed inside the range Argus predicted.
        </p>
      </div>
    )
  if (v?.status === 'missed')
    return (
      <div className="mt-6">
        <div className="text-[24px] font-semibold text-problem">The prediction missed.</div>
        <p className="mt-2 text-[14px] text-fg-2">{statusLine ?? 'Argus has adjusted its expectations.'}</p>
        {band && v.actual != null && <BandBar band={band} actual={v.actual} />}
      </div>
    )
  return (
    <div className="mt-6">
      <div className="text-[24px] font-semibold leading-snug text-fg">{fix ? `${fix.name} is live.` : 'The fix is live.'}</div>
      <p className="mt-2 text-[16px] leading-relaxed text-fg-2">
        Argus checks its own prediction after 24 hours
        {band ? `: first-day margin should land between ${usd(band[0])} and ${usd(band[2])}.` : '.'}
      </p>
    </div>
  )
}

function BandBar({ band, actual }: { band: number[]; actual: number }) {
  const [lo, , hi] = band
  const span = hi - lo || 1
  const pos = Math.max(0, Math.min(1, (actual - lo) / span))
  return (
    <div className="mt-5">
      <div className="relative h-2 rounded-full bg-ok/20">
        <div className="absolute -top-1 h-4 w-1 rounded bg-ok" style={{ left: `calc(${pos * 100}% - 2px)` }} />
      </div>
      <div className="num mt-2 flex justify-between text-[14px] text-fg-2">
        <span>{usd(lo)}</span>
        <span className="text-ok">actual {usd(actual)}</span>
        <span>{usd(hi)}</span>
      </div>
    </div>
  )
}
