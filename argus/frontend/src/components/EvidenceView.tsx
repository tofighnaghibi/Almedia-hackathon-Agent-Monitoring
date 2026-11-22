import { useEffect, useMemo, useState, type ReactNode } from 'react'
import CausalGraph, { defaultRole, type NodeRole } from './charts/CausalGraph'
import FakeAccountPicture from './charts/FakeAccountPicture'
import L3Scatter from './charts/L3Scatter'
import NormalZoneChart from './charts/NormalZoneChart'
import ShapleyBars from './charts/ShapleyBars'
import TwinFutures, { TwinButton, type TwinLine } from './charts/TwinFutures'
import { Walk, deriveWalk } from './ReplayView'
import { bestHypothesis, isBaseline, metricLabel, shortHypothesis } from '../lib/incident'
import { VERDICT_COLOR, usd } from '../lib/theme'
import type { GraphEdge, IncidentDetail, Level } from '../lib/types'

// Three questions, one signature visual each; the trail and reports sit behind "For experts".
export type EvidenceTab = 'why' | 'who' | 'whatif' | 'details'
const TABS: { id: EvidenceTab; label: string }[] = [
  { id: 'why', label: 'Why?' },
  { id: 'who', label: 'Who?' },
  { id: 'whatif', label: 'What if?' },
]
export const RECOMMENDER_CAPTION = 'Argus knows what real people like.'

// Plain names for the metric nodes used in captions.
const PLAIN: Record<string, string> = {
  new_accounts: 'new sign-ups',
  active_users: 'active users',
  impressions: 'offer views',
  starts: 'offer starts',
  completions: 'completions',
  revenue: 'revenue',
  rewards: 'user rewards',
  quality: 'user quality',
  margin: 'margin',
  quality_per_completion: 'quality per completion',
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
const joinAnd = (xs: string[]) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

function graphCaption(d: IncidentDetail) {
  const root = d.graph.nodes.find((n) => n.state === 'root')
  if (!root) return 'No single cause was found.'
  const rootName = PLAIN[root.id] ?? root.label.toLowerCase()
  // The money metrics a dashboard shows, downstream of the root: they only move because the root broke.
  const downstream = new Set<string>()
  const queue = [root.id]
  while (queue.length) {
    const id = queue.shift()!
    for (const e of d.graph.edges) if (e.source === id && !downstream.has(e.target)) downstream.add(e.target), queue.push(e.target)
  }
  const symptoms = ['revenue', 'margin'].filter((id) => downstream.has(id)).map((id) => PLAIN[id])
  if (!symptoms.length) return `${cap(rootName)} ${rootName.endsWith('s') ? 'are' : 'is'} the cause; everything downstream follows from it.`
  return `${cap(rootName)} ${rootName.endsWith('s') ? 'are' : 'is'} the cause. ${cap(joinAnd(symptoms))} ${symptoms.length === 1 ? 'is' : 'are'} only affected.`
}

function usersCaption(d: IncidentDetail) {
  if (!d.l3) return `No need to look at individual accounts: the cause is ${PLAIN[d.root ?? ''] ?? 'a business process'}, not people.`
  const s = d.l3.summary
  const devices = d.l3.groups?.filter((g) => g.count >= 2).length ?? 0
  if (devices > 0) return `${s.n_flagged} new accounts on ${devices} shared ${devices === 1 ? 'phone' : 'phones'}; their interests don't match what they do.`
  const emu = s.top_devices.some((x) => /emu|emulator|sdk|generic/i.test(x))
  return `${s.n_flagged} new accounts whose interests make no sense, ${emu ? 'on shared emulators' : `sharing devices like ${s.top_devices[0]}`}.`
}

function fixesCaption(d: IncidentDetail) {
  const best = d.fixes.find((f) => f.id === d.recommended)
  if (!best) return 'No fixes tested.'
  return `Played forward a week: ${best.name.toLowerCase()} keeps ${usd(best.delta_p50_usd)} more margin than doing nothing.`
}

type Props = {
  detail: IncidentDetail | null
  title: string
  initialTab?: EvidenceTab
  timeLabel?: (t: number) => string // "Mon 06:00" for a simulated hour
  onTab?: (tab: EvidenceTab) => void // reports tab changes (the level rail follows Why / Who)
  play?: boolean // start the twin rollout on the What if? tab by itself (demo)
  approving: boolean
  onApprove: (fixId: string) => void
  onClose: () => void
}

// L3 / engineer: one large visual per tab with one sentence saying what it proves.
export default function EvidenceView({ detail: d, title, initialTab = 'why', timeLabel = (t) => `hour ${t}`, onTab, play, approving, onApprove, onClose }: Props) {
  const [tab, setTabState] = useState<EvidenceTab>(initialTab)
  const setTab = (t: EvidenceTab) => {
    setTabState(t)
    onTab?.(t)
  }
  useEffect(() => setTabState(initialTab), [initialTab])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const caption = !d
    ? 'Loading the evidence…'
    : tab === 'why'
      ? graphCaption(d)
      : tab === 'who'
        ? usersCaption(d)
        : tab === 'whatif'
          ? fixesCaption(d)
          : 'How Argus got here, and what it told each audience.'

  return (
    <div className="fade-in absolute inset-0 z-40 overflow-y-auto bg-bg/[0.97] backdrop-blur-md">
      <div className="mx-auto max-w-[1000px] pb-16 pl-[236px] pr-10 pt-8 xl:pl-10">
        <button onClick={onClose} className="text-[14px] text-fg-2 hover:text-fg">
          ← Back to the Earth
        </button>
        <h1 className="mt-4 text-[24px] font-semibold text-fg">{title}</h1>

        <nav className="mt-8 flex items-end gap-2 border-b border-line">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`-mb-px min-w-[140px] border-b-2 px-5 pb-3 text-[22px] font-semibold transition ${
                tab === t.id ? 'border-ok text-fg' : 'border-transparent text-fg-2 hover:text-fg'
              }`}
            >
              {t.label}
            </button>
          ))}
          <button onClick={() => setTab('details')} className={`ml-auto pb-3 text-[14px] ${tab === 'details' ? 'text-fg' : 'text-fg-2 hover:text-fg'}`}>
            For experts
          </button>
        </nav>

        <p key={`${tab}-${caption}`} className="fade-in mt-6 max-w-[760px] text-[22px] font-medium leading-snug text-fg">
          {caption}
        </p>

        <div key={tab} className="fade-in mt-6">
          {!d ? (
            <div className="h-[420px] animate-pulse rounded-2xl bg-panel/60" />
          ) : tab === 'why' ? (
            <div className="space-y-6">
              <div className="grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-start gap-6">
                <WhyWalk d={d} />
                <Proof d={d} part="chart" />
              </div>
              <Proof d={d} part="list" />
            </div>
          ) : tab === 'who' ? (
            d.l3 ? (
              <Frame>
                <FakeAccountPicture l3={d.l3} />
                <p className="mt-4 text-[16px] text-fg-2">{RECOMMENDER_CAPTION}</p>
              </Frame>
            ) : (
              <Empty text="This problem was found without looking at individual accounts." />
            )
          ) : tab === 'whatif' ? (
            <Fixes d={d} approving={approving} onApprove={onApprove} play={play} />
          ) : (
            <Details d={d} timeLabel={timeLabel} />
          )}
        </div>
      </div>
    </div>
  )
}

// Why?: Argus walks the graph from margin backwards; each number gets its verdict, the cause pulses.
function WhyWalk({ d }: { d: IncidentDetail }) {
  const walk = useMemo(() => d.replay?.walk?.steps ?? deriveWalk(d), [d])
  const [at, setAt] = useState(0)
  const [all, setAll] = useState(false)
  useEffect(() => {
    setAt(0)
    const id = window.setInterval(() => setAt((n) => (n >= walk.length ? (window.clearInterval(id), n) : n + 1)), 420)
    return () => window.clearInterval(id)
  }, [walk])
  return (
    <Frame>
      {all ? <StoryGraph d={d} /> : <Walk d={d} walk={walk} at={at} big />}
      <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-[14px] text-fg-2">
        <span className="flex items-center gap-2"><span className="h-3 w-5 rounded bg-problem" /> cause</span>
        <span className="flex items-center gap-2"><span className="h-3 w-5 rounded border border-dashed border-problem" /> affected</span>
        <span className="flex items-center gap-2"><span className="h-3 w-5 rounded border border-line" /> fine</span>
      </div>
      <button onClick={() => setAll((a) => !a)} className="mt-2 text-[14px] text-fg-2 hover:text-fg">{all ? 'Back to the walk' : 'Show all metrics'}</button>
    </Frame>
  )
}

// The causal story: by default only the path from the broken link to margin; everything else on request.
const STORY = ['new_accounts', 'active_users', 'starts', 'completions', 'revenue', 'rewards', 'margin']
const NODE_PLAIN: Record<string, string> = {
  new_accounts: 'New sign-ups',
  active_users: 'Active users',
  impressions: 'Offer views',
  starts: 'Offer starts',
  completions: 'Completions',
  revenue: 'Revenue',
  rewards: 'Rewards paid',
  quality: 'Valuable users',
  margin: 'Margin',
  quality_per_completion: 'Quality per completion',
  calendar: 'Planned events',
}

function StoryGraph({ d }: { d: IncidentDetail }) {
  const [all, setAll] = useState(false)
  const { nodes, edges, roles } = useMemo(() => {
    const root = d.graph.nodes.find((n) => n.state === 'root')
    const down = new Set<string>()
    if (root) {
      const q = [root.id]
      while (q.length) {
        const id = q.shift()!
        for (const e of d.graph.edges) if (e.source === id && !down.has(e.target)) down.add(e.target), q.push(e.target)
      }
    }
    const roles: Record<string, NodeRole> = {}
    for (const n of d.graph.nodes) {
      roles[n.id] = defaultRole(n)
      // Revenue and margin only move because of the root: say so explicitly, matching the caption.
      if ((n.id === 'revenue' || n.id === 'margin') && down.has(n.id) && roles[n.id] === 'normal') roles[n.id] = 'symptom'
    }
    const plainNodes = d.graph.nodes.map((n) => ({ ...n, label: NODE_PLAIN[n.id] ?? n.label }))
    if (all) return { nodes: plainNodes, edges: d.graph.edges, roles }
    // Story subset: path nodes from the root's parents downwards (plus the root itself).
    const rootParents = new Set(d.graph.edges.filter((e) => e.target === root?.id).map((e) => e.source))
    const keep = new Set(
      d.graph.nodes.filter((n) => n.id === root?.id || (STORY.includes(n.id) && (down.has(n.id) || rootParents.has(n.id)))).map((n) => n.id),
    )
    // Bridge hidden nodes so the path stays connected (e.g. active users → offer starts).
    const edges: GraphEdge[] = []
    const seen = new Set<string>()
    for (const src of keep) {
      const stack = d.graph.edges.filter((e) => e.source === src).map((e) => ({ to: e.target, type: e.type }))
      const visited = new Set<string>()
      while (stack.length) {
        const { to, type } = stack.pop()!
        if (visited.has(to)) continue
        visited.add(to)
        if (keep.has(to)) {
          const k = `${src}>${to}`
          if (!seen.has(k)) seen.add(k), edges.push({ source: src, target: to, type })
        } else for (const e of d.graph.edges) if (e.source === to) stack.push({ to: e.target, type: e.type })
      }
    }
    return { nodes: plainNodes.filter((n) => keep.has(n.id)), edges, roles }
  }, [d, all])
  return (
    <Frame narrow>
      <CausalGraph nodes={nodes} edges={edges} roles={roles} />
      <div className="mt-6 flex items-center justify-between text-[14px] text-fg-2">
        <span className="flex gap-5">
          <span className="flex items-center gap-2"><span className="h-3 w-5 rounded bg-problem" /> cause</span>
          <span className="flex items-center gap-2"><span className="h-3 w-5 rounded border border-dashed border-problem" /> affected</span>
          <span className="flex items-center gap-2"><span className="h-3 w-5 rounded border border-line" /> fine</span>
        </span>
        <button onClick={() => setAll((a) => !a)} className="text-fg-2 hover:text-fg">
          {all ? 'Show the story only' : 'Show all metrics'}
        </button>
      </div>
    </Frame>
  )
}

function Frame({ children, narrow }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`mx-auto w-full rounded-2xl bg-panel/60 p-8 ring-1 ring-line ${narrow ? 'max-w-[720px]' : ''}`}>{children}</div>
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-2xl bg-panel/60 p-10 text-center text-[16px] text-fg-2 ring-1 ring-line">{text}</div>
}

// Proof: replay the twin without the cause. The big toggle slides the observed line onto the counterfactual,
// back into the normal band; the other explanations tested are listed below.
function Proof({ d, part }: { d: IncidentDetail; part?: 'chart' | 'list' }) {
  const [without, setWithout] = useState(false)
  const cf = d.counterfactual
  const series = (d.series ?? []).slice(-24) // last day: the excursion stays readable
  const cfByT = new Map((cf?.series ?? []).map((p) => [p.t, p.value]))
  const cfValues = series.map((p) => cfByT.get(p.t) ?? p.actual)
  const share = cf?.explained_share ?? bestHypothesis(d)?.explained ?? 0
  const cfLabel = cf?.label.replace(/\s*\(.*\)\s*$/, '') ?? ''
  const question = cf ? `What would it look like ${cfLabel.charAt(0).toLowerCase()}${cfLabel.slice(1)}?` : null
  const hyps = [...d.hypotheses].sort((a, b) => b.explained - a.explained)
  return (
    <div className="space-y-6">
      {part !== 'list' && series.length > 1 && (
        <Frame>
          {question && (
            <button
              onClick={() => setWithout((w) => !w)}
              aria-pressed={without}
              className={`flex w-full items-center gap-4 rounded-2xl px-5 py-4 text-left text-[16px] ring-1 transition ${without ? 'bg-ok/10 text-fg ring-ok/50' : 'bg-bg/40 text-fg ring-line hover:ring-accent/60'}`}
            >
              <span className={`relative h-7 w-12 shrink-0 rounded-full transition ${without ? 'bg-ok' : 'bg-line'}`}>
                <span className={`absolute top-1 h-5 w-5 rounded-full bg-fg transition-all duration-300 ${without ? 'left-6' : 'left-1'}`} />
              </span>
              {question}
            </button>
          )}
          <div className="mt-6">
            <NormalZoneChart series={series} leftAt={d.left_zone_at} values={without ? cfValues : undefined} width={520} height={300} fontSize={14} showStart={false} />
          </div>
          <div className="mt-3 flex flex-col gap-1 text-[14px] text-fg-2">
            <span>{metricLabel(d.series_meta)}</span>
            <span className={`text-[16px] transition-opacity duration-300 ${without ? 'text-ok opacity-100' : 'opacity-0'}`}>
              {Math.round(share * 100)}% of the change disappears{share >= 0.8 ? ' — cause proven' : ''}
            </span>
          </div>
        </Frame>
      )}
      {part !== 'chart' && <Frame>
        <h3 className="mb-4 text-[16px] text-fg">Every explanation Argus tested</h3>
        <div className="space-y-4">
          {hyps.map((h) => (
            <div key={h.name} className="grid grid-cols-[1fr_180px_56px_80px] items-center gap-4 text-[14px]">
              <span className={h.verdict === 'supported' ? 'text-fg' : 'text-fg-2'}>{shortHypothesis(h.name)}</span>
              <div className="h-2 overflow-hidden rounded-full bg-line">
                <div className="h-full rounded-full transition-[width] duration-300 ease-out" style={{ width: `${Math.max(1, h.explained * 100)}%`, background: VERDICT_COLOR[h.verdict] }} />
              </div>
              <span className="num text-right text-fg">{Math.round(h.explained * 100)}%</span>
              <span className="text-right" style={{ color: h.verdict === 'supported' ? VERDICT_COLOR.supported : undefined }}>
                {h.verdict === 'supported' ? 'Proven' : h.verdict === 'partial' ? 'Partial' : 'Ruled out'}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-5 text-[14px] text-fg-2">How much of the change disappears when the week is replayed without that explanation. Counted as proven at 80% or more.</p>
      </Frame>}
    </div>
  )
}

function Fixes({ d, approving, onApprove, play }: { d: IncidentDetail; approving: boolean; onApprove: (id: string) => void; play?: boolean }) {
  const v = d.verification
  const [run, setRun] = useState(play ? 1 : 0)
  const best = d.fixes.find((f) => f.id === d.recommended)
  const others = d.fixes.filter((f) => f.id !== d.recommended && !isBaseline(f))
  return (
    <div className="space-y-6">
      {d.applied && (
        <div className={`rounded-2xl p-5 text-[16px] ring-1 ${v.status === 'verified' ? 'bg-ok/10 text-ok ring-ok/40' : v.status === 'missed' ? 'bg-problem/15 text-problem ring-problem/40' : 'text-fg ring-line'}`}>
          {v.status === 'verified'
            ? `Prediction verified: actual first-day margin ${usd(v.actual ?? 0)} landed inside the band.`
            : v.status === 'missed'
              ? 'Prediction missed: Argus adjusted its expectations.'
              : `${d.fixes.find((f) => f.id === d.applied)?.name ?? 'Fix'} applied. Verifying the prediction after 24 h.`}
        </div>
      )}
      <Frame>
        <div className="mb-4">
          <TwinButton onClick={() => setRun((r) => r + 1)} done={run > 0} />
        </div>
        {run > 0 && <FixFutures d={d} playKey={run} />}
        {best && !d.applied && (
          <div className="mt-6 flex items-center gap-6">
            <button
              disabled={approving}
              onClick={() => onApprove(best.id)}
              className="rounded-xl bg-ok px-6 py-3 text-[16px] font-semibold text-bg hover:brightness-110 disabled:opacity-50"
            >
              {approving ? 'Applying…' : `Approve: ${best.name.toLowerCase()}`}
            </button>
            <span className="text-[14px] text-fg-2">{best.description}</span>
          </div>
        )}
      </Frame>
      {others.length > 0 && (
        <div>
          <h3 className="mb-3 text-[14px] text-fg-2">Other options</h3>
          <ul className="divide-y divide-line rounded-2xl bg-panel/60 ring-1 ring-line">
            {others.map((f) => (
              <li key={f.id} className="flex items-center gap-6 px-6 py-4 text-[14px]">
                <span className="w-44 shrink-0 text-fg">{f.name}</span>
                <span className="num w-20 shrink-0 text-fg">{f.delta_p50_usd >= 0 ? '+' : ''}{usd(f.delta_p50_usd)}</span>
                <span className="flex-1 text-fg-2">{f.advertiser_risk}</span>
                {!d.applied && (
                  <button disabled={approving} onClick={() => onApprove(f.id)} className="rounded-lg bg-line px-3 py-1.5 text-fg hover:ring-1 hover:ring-accent/60 disabled:opacity-50">
                    Approve
                  </button>
                )}
                {d.applied === f.id && <span className="text-ok">Applied</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// The four futures in the twin: each option's cumulative margin over 7 days against today's pace, likely ranges
// shaded, the best option strong and the gap to doing nothing labelled.
function FixFutures({ d, playKey }: { d: IncidentDetail; playKey: number }) {
  const base = d.fixes.find(isBaseline) ?? d.fixes[0]
  const pace = base.p50[0]
  const rel = (arr: number[]) => [0, ...arr.map((v, i) => v - pace * (i + 1))]
  const highlight = d.applied ?? d.recommended
  const best = d.fixes.find((f) => f.id === highlight)
  const lines: TwinLine[] = d.fixes.map((f) => ({
    id: f.id,
    label: isBaseline(f) ? 'Doing nothing' : f.name,
    color: f.id === highlight ? '#3CF2C4' : isBaseline(f) ? '#FF8FB7' : '#9A9CC4',
    strong: f.id === highlight,
    p10: rel(f.p10),
    p50: rel(f.p50),
    p90: rel(f.p90),
  }))
  return (
    <TwinFutures
      lines={lines}
      xLabels={['now', ...base.days.map((n) => `day ${n}`)]}
      gap={best && !isBaseline(best) ? { from: base.id, to: best.id, label: `+${usd(best.delta_p50_usd)} over the week` } : undefined}
      events={(base.events ?? []).map((e) => ({ x: e.after_h / 24, label: e.label }))}
      playKey={playKey}
    />
  )
}

const LEVEL_TEXT: Record<Level, string> = { L1: 'Business', L2: 'Segment', L3: 'Users' }

function Details({ d, timeLabel }: { d: IncidentDetail; timeLabel: (t: number) => string }) {
  const [aud, setAud] = useState<'ceo' | 'manager' | 'engineer'>('ceo')
  return (
    <div className="grid grid-cols-[1.1fr_1fr] gap-8">
      <section>
        <h3 className="mb-4 text-[16px] font-semibold text-fg">Decision trail</h3>
        <ol className="relative ml-2 border-l border-line">
          {d.trail.map((s, i) => (
            <li key={i} className="fade-up relative pb-6 pl-6 last:pb-0" style={{ animationDelay: `${i * 70}ms` }}>
              <span className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full bg-fg-2" />
              <div className="flex items-baseline gap-3 text-[14px] text-fg-2">
                <span>
                  {s.level} · {LEVEL_TEXT[s.level]}
                </span>
                <span className="num">{timeLabel(s.t)}</span>
              </div>
              <div className="mt-0.5 text-[14px] font-medium text-fg">{s.action}</div>
              <div className="text-[16px] leading-relaxed text-fg-2">{s.finding}</div>
            </li>
          ))}
        </ol>
      </section>
      <section className="space-y-8">
        <div>
          <div className="mb-4 flex items-baseline gap-4">
            <h3 className="text-[16px] font-semibold text-fg">Reports</h3>
            {(['ceo', 'manager', 'engineer'] as const).map((a) => (
              <button key={a} onClick={() => setAud(a)} className={`text-[14px] ${aud === a ? 'text-ok' : 'text-fg-2 hover:text-fg'}`}>
                {a === 'ceo' ? 'CEO' : a === 'manager' ? 'Manager' : 'Engineer'}
              </button>
            ))}
          </div>
          <p key={aud} className="fade-in text-[16px] leading-relaxed text-fg">
            {d.reports[aud]}
          </p>
          {d.reports.source && <div className="mt-2 text-[14px] text-fg-2">Written by {d.reports.source === 'llm' ? 'the language model' : 'templates'}.</div>}
        </div>
        <div>
          <h3 className="mb-4 text-[16px] font-semibold text-fg">What each step added to the margin change</h3>
          <p className="mb-3 text-[16px] text-fg">The cause starts small; its effects spread downstream.</p>
          <ShapleyBars items={d.contributions} />
        </div>
        {d.l3 && (
          <div>
            <h3 className="mb-4 text-[16px] font-semibold text-fg">Do interests fit what they do? vs payout focus</h3>
            <L3Scatter l3={d.l3} />
          </div>
        )}
      </section>
    </div>
  )
}
