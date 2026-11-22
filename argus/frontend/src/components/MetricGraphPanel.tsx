import { useEffect, useMemo, useState } from 'react'
import CausalGraph from './charts/CausalGraph'
import { api } from '../lib/api'
import { plainMetric, replaceWord, splitNameFormula, toOutcome, type ChoiceOption, type MetricOutcome } from '../lib/metrics'
import type { EdgeType, GraphEdge, GraphNode, GraphTreeRow, MetricGraph } from '../lib/types'

const EXAMPLES = [
  { name: 'margin_per_signup', formula: 'margin / signups' },
  { name: 'revenue_per_user', formula: 'revenue / users' }, // ambiguous on purpose: Argus asks
]

type Props = {
  graph: MetricGraph | null
  highlight: string[]
  outcome?: MetricOutcome | null // from the "add a metric" demo scene
  openForm?: boolean
  typeIn?: boolean // demo: type the scene's name and formula character by character, then show what Argus did
  onAdded: (id: string) => void
  onClose: () => void
}

export default function MetricGraphPanel({ graph, highlight, outcome: initial, openForm, typeIn, onAdded, onClose }: Props) {
  const [formOpen, setFormOpen] = useState(!!openForm || !!initial)
  const typing = !!typeIn && initial?.kind === 'added'
  const [name, setName] = useState(initial?.kind === 'added' && !typing ? initial.id : '')
  const [formula, setFormula] = useState(initial?.kind === 'added' && !typing ? initial.typed : '')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<MetricOutcome | null>(typing ? null : (initial ?? null))
  const [stage, setStage] = useState(initial?.kind === 'added' && !typing ? 0 : -1) // number of steps shown (0..3)

  // Demo: type the name, then the formula, a character at a time; then "Add" and what Argus did by itself.
  useEffect(() => {
    if (!typing || initial?.kind !== 'added') return
    const n = initial.id
    const f = initial.typed
    const timers: number[] = []
    let at = 300
    for (let k = 1; k <= n.length; k++) timers.push(window.setTimeout(() => setName(n.slice(0, k)), (at += 55)))
    at += 250
    for (let k = 1; k <= f.length; k++) timers.push(window.setTimeout(() => setFormula(f.slice(0, k)), (at += 70)))
    timers.push(window.setTimeout(() => setBusy(true), (at += 350)))
    timers.push(
      window.setTimeout(() => {
        setBusy(false)
        setOutcome(initial)
        setStage(0)
      }, (at += 600)),
    )
    return () => timers.forEach(window.clearTimeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typing])
  const [pointTo, setPointTo] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const known = useMemo(() => graph?.nodes.map((n) => n.id) ?? [], [graph])
  const addedId = outcome?.kind === 'added' ? outcome.id : null
  const inGraph = !!addedId && known.includes(addedId)

  // Step through what Argus does by itself once the new node is in the graph.
  useEffect(() => {
    if (outcome?.kind !== 'added') return
    if (!inGraph) {
      const fallback = window.setTimeout(() => setStage((s) => Math.max(s, 1)), 1500)
      return () => window.clearTimeout(fallback)
    }
    // The node flies in with "Found its place"; then the checks tick one after another.
    const timers = [
      window.setTimeout(() => setStage((s) => Math.max(s, 1)), 150),
      window.setTimeout(() => setStage((s) => Math.max(s, 2)), 1500),
      window.setTimeout(() => setStage(3), 2700),
    ]
    return () => timers.forEach(window.clearTimeout)
  }, [outcome, inGraph])

  const submit = async (n = name, f = formula) => {
    const split = splitNameFormula(n, f)
    if (!split.name || !split.formula) return
    setName(split.name)
    setFormula(split.formula)
    setBusy(true)
    setStage(-1)
    setPointTo(null)
    try {
      const raw = await api.addMetric(split.name, '', split.formula)
      const o = toOutcome(raw as never, split.formula, known)
      setOutcome(o)
      if (o.kind === 'added') {
        setStage(0)
        onAdded(o.id)
      }
    } catch (e) {
      setOutcome({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] }
    const show = (id: string) => !(id === addedId && stage < 1) // the new node appears with "Found its place"
    const nodes: GraphNode[] = [
      ...graph.exogenous.map((x) => ({ id: x.id, label: plainMetric(x.id, x.label), z: 0, state: 'normal' as const })),
      ...graph.nodes.filter((n) => show(n.id)).map((n) => ({ id: n.id, label: plainMetric(n.id, n.label), z: 0, state: 'normal' as const })),
    ]
    const ids = new Set(nodes.map((n) => n.id))
    return { nodes, edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) }
  }, [graph, addedId, stage])
  const lit = pointTo ? [pointTo] : addedId ? [addedId] : highlight

  return (
    <div className="fade-in absolute inset-x-0 bottom-0 top-[88px] z-40 overflow-y-auto bg-bg/85 backdrop-blur-md">
      <div className="mx-auto max-w-[1000px] px-10 pb-16 pt-8">
        <button onClick={onClose} className="text-[14px] text-fg-2 hover:text-fg">
          ← Back to the Earth
        </button>
        <div className="mt-6 flex items-end justify-between">
          <div>
            <h1 className="text-[24px] font-semibold text-fg">How the numbers connect</h1>
            <div className="text-[14px] text-fg-2">{graph ? `${graph.nodes.length} metrics${graph.exogenous.length ? ' + planned events' : ''}, watched in every country` : 'Loading…'}</div>
          </div>
          {!formOpen && (
            <button onClick={() => setFormOpen(true)} className="rounded-full px-4 py-2 text-[14px] text-fg ring-1 ring-line hover:ring-accent">
              + Add a metric
            </button>
          )}
        </div>

        <div className="mt-8 grid grid-cols-[1.45fr_1fr] gap-8">
          <div className="rounded-2xl bg-panel/70 p-6 ring-1 ring-line">
            {graph ? (
              <CausalGraph
                nodes={nodes}
                edges={edges}
                roles={Object.fromEntries(lit.map((id) => [id, 'new' as const]))}
                showWords={false}
                edgeMode="types"
                dims={{ W: 640, nodeW: 140, nodeH: 44, row: 66 }}
              />
            ) : (
              <div className="h-96 animate-pulse rounded-xl bg-line/50" />
            )}
            <Legend types={graph?.edge_types} />
          </div>

          <div>
            {formOpen ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  submit()
                }}
                className="rounded-2xl bg-panel/70 p-6 ring-1 ring-line"
              >
                <label className="block text-[14px] text-fg-2" htmlFor="m-name">
                  Name
                </label>
                <input id="m-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="margin_per_signup" autoComplete="off" className="mt-1 w-full rounded-lg bg-bg px-3 py-2 text-[16px] text-fg outline-none ring-1 ring-line focus:ring-accent" />
                <label className="mt-4 block text-[14px] text-fg-2" htmlFor="m-formula">
                  Formula
                </label>
                <input id="m-formula" value={formula} onChange={(e) => setFormula(e.target.value)} placeholder="margin / signups" autoComplete="off" className="num mt-1 w-full rounded-lg bg-bg px-3 py-2 text-[16px] text-fg outline-none ring-1 ring-line focus:ring-accent" />
                <div className="mt-3 flex flex-wrap gap-2">
                  {EXAMPLES.map((ex) => (
                    <button
                      type="button"
                      key={ex.name}
                      onClick={() => {
                        setName(ex.name)
                        setFormula(ex.formula)
                        setOutcome(null)
                        setStage(-1)
                      }}
                      className="num rounded-full bg-line/70 px-3 py-1 text-[14px] text-fg-2 hover:text-fg"
                    >
                      {ex.name} = {ex.formula}
                    </button>
                  ))}
                </div>
                <button type="submit" disabled={busy || !name.trim() || (!formula.trim() && !name.includes('='))} className="mt-5 w-full rounded-xl bg-ok py-2.5 text-[16px] font-semibold text-bg hover:brightness-110 disabled:bg-line disabled:text-fg-2">
                  {busy ? 'Adding…' : 'Add'}
                </button>

                <Outcome
                  outcome={outcome}
                  stage={stage}
                  onShow={setPointTo}
                  onChoose={(word, opt) => {
                    const f = opt.formula ?? replaceWord(formula, word, opt.id)
                    setFormula(f)
                    submit(name, f)
                  }}
                />
              </form>
            ) : (
              <p className="text-[16px] leading-relaxed text-fg-2">Each box is a number Argus watches. Add your own and Argus places it, checks it and starts watching it.</p>
            )}
          </div>
        </div>

        {graph && <MetricTree graph={graph} fresh={addedId} />}
      </div>
    </div>
  )
}

function Outcome({ outcome, stage, onChoose, onShow }: { outcome: MetricOutcome | null; stage: number; onChoose: (word: string, option: ChoiceOption) => void; onShow: (id: string) => void }) {
  if (!outcome) return null
  if (outcome.kind === 'exists')
    return (
      <div className="fade-up mt-5 flex items-center justify-between gap-4 rounded-xl bg-bg/60 p-4 ring-1 ring-line">
        <p className="text-[14px] text-fg">{outcome.message}</p>
        <button type="button" onClick={() => onShow(outcome.id)} className="shrink-0 rounded-lg bg-line px-3 py-1.5 text-[14px] text-fg hover:ring-1 hover:ring-accent">
          Show it
        </button>
      </div>
    )
  if (outcome.kind === 'choose')
    return (
      <div className="fade-up mt-5 rounded-xl bg-bg/60 p-4 ring-1 ring-line">
        <p className="text-[14px] text-fg">{outcome.message}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {outcome.options.map((o) => (
            <button type="button" key={o.id} onClick={() => onChoose(outcome.word, o)} className="rounded-lg bg-line px-3 py-1.5 text-[14px] text-fg hover:ring-1 hover:ring-accent">
              {o.label}
            </button>
          ))}
        </div>
      </div>
    )
  if (outcome.kind === 'error')
    return (
      <div className="fade-up mt-5 rounded-xl bg-problem/15 p-4 text-[14px] text-problem" role="alert">
        {outcome.message}
      </div>
    )
  const parents = outcome.parents.map((p) => plainMetric(p)).join(' and ')
  const by = outcome.breakdown.filter((b) => b !== 'global').join(', ')
  const steps = [
    {
      title: 'Found its place',
      detail: `${outcome.readAs.map(([w, id]) => `“${w}” = ${plainMetric(id)} · `).join('')}${parents ? `Below ${parents}` : 'In the graph'}${by ? ` · by ${by}` : ''}`,
    },
    { title: 'Checked it on the data', detail: outcome.fit.charAt(0).toUpperCase() + outcome.fit.slice(1) },
    { title: 'Watching it now', detail: `In all ${outcome.countries} countries` },
  ]
  return (
    <ol className="mt-5 space-y-3" aria-live="polite">
      {steps.map((s, i) =>
        stage > i ? (
          <li key={s.title} className="fade-up flex gap-3">
            <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-ok ${i === 2 ? 'animate-pulse' : ''}`} />
            <div>
              <div className="text-[16px] text-fg">{s.title}</div>
              <div className="text-[14px] text-fg-2">{s.detail}</div>
            </div>
          </li>
        ) : null,
      )}
      {stage > 2 && outcome.note && (
        <li className="fade-up pl-5 text-[14px] text-fg-2">
          <details>
            <summary className="cursor-pointer list-none hover:text-fg" title={outcome.note}>
              Added because you asked · watching in {outcome.countries} markets <span aria-hidden>▸</span>
            </summary>
            <p className="mt-1.5 leading-relaxed">{outcome.note}</p>
          </details>
        </li>
      )}
    </ol>
  )
}

const LEGEND_FALLBACK: EdgeType[] = [
  { type: 'formula', label: 'Formula' },
  { type: 'mechanism', label: 'Possible cause' },
  { type: 'exogenous', label: 'Known event' },
  { type: 'breakdown', label: 'Breakdown' },
]

function Legend({ types }: { types?: EdgeType[] }) {
  const items = types?.length ? types : LEGEND_FALLBACK
  const glyph = (t: string) =>
    t === 'formula' ? (
      <line x1="1" y1="4" x2="25" y2="4" stroke="#6B6F9E" strokeWidth="1.6" />
    ) : t === 'breakdown' ? null : (
      <>
        <line x1="1" y1="4" x2="20" y2="4" stroke="#4A4E78" strokeWidth="1.2" strokeDasharray={t === 'exogenous' ? '1.5 3' : '4 3'} />
        <path d="M19,1 L25,4 L19,7 z" fill="#4A4E78" />
      </>
    )
  return (
    <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-[14px] text-fg-2">
      {items.map((t) => (
        <span key={t.type} className="flex items-center gap-2" title={t.meaning}>
          {t.type === 'breakdown' ? <span>└</span> : <svg width="26" height="8" aria-hidden>{glyph(t.type)}</svg>}
          {t.label}
          {t.type === 'breakdown' && ' (list below)'}
        </span>
      ))}
    </div>
  )
}

// Browsable tree from GET /api/graph/tree (depth-first rows).
function MetricTree({ graph, fresh }: { graph: MetricGraph; fresh: string | null }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<GraphTreeRow[] | null>(null)
  useEffect(() => {
    let live = true
    api
      .graphTree()
      .then((r) => live && setRows(Array.isArray(r?.rows) ? r.rows : null))
      .catch(() => live && setRows(null))
    return () => {
      live = false
    }
  }, [graph])
  return (
    <section className="mt-8 rounded-2xl bg-panel/50 ring-1 ring-line">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center justify-between px-6 py-4 text-left text-[16px] text-fg">
        All metrics
        <span className="text-[14px] text-fg-2">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (rows ? <ServerTree key={rows.length} rows={rows} fresh={fresh} /> : <p className="px-6 pb-5 text-[14px] text-fg-2">The metric list is not available yet.</p>)}
    </section>
  )
}

function ServerTree({ rows, fresh }: { rows: GraphTreeRow[]; fresh: string | null }) {
  // Expanded rows by index; roots start collapsed except a freshly added metric.
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set(rows.flatMap((r, i) => (r.depth === 0 && r.id === fresh ? [i] : []))))
  const hasKids = (i: number) => rows[i + 1]?.depth > rows[i].depth
  const visible: number[] = []
  const stack: { depth: number; open: boolean }[] = []
  rows.forEach((r, i) => {
    while (stack.length && stack[stack.length - 1].depth >= r.depth) stack.pop()
    if (stack.every((a) => a.open)) visible.push(i)
    stack.push({ depth: r.depth, open: expanded.has(i) })
  })
  const toggle = (i: number) => setExpanded((s) => (s.has(i) ? (s.delete(i), new Set(s)) : new Set(s).add(i)))
  return (
    <ul className="px-6 pb-5 text-[14px]">
      {visible.map((i) => {
        const r = rows[i]
        const isOpen = expanded.has(i)
        const by = r.breakdown?.replace(/^global\s*→\s*/, '').replace(/\s*→\s*/g, ' › ')
        return (
          <li key={i}>
            <button onClick={() => toggle(i)} className="flex w-full items-center gap-2 py-1.5 text-left hover:text-fg" style={{ paddingLeft: r.depth * 20 }}>
              <span className="w-3 text-fg-2">{hasKids(i) || by ? (isOpen ? '▾' : '▸') : ''}</span>
              <span className={r.id === fresh ? 'text-ok' : 'text-fg'}>{plainMetric(r.id, r.label)}</span>
              {r.expr && <span className="num text-fg-2">= {r.expr}</span>}
              {r.relation && r.relation !== 'formula' && <span className="text-fg-2">· {(r.relation_label ?? 'possible cause').toLowerCase()}</span>}
              {r.repeat && <span className="text-fg-2">· see above</span>}
            </button>
            {isOpen && by && !r.repeat && (
              <div className="py-1 text-fg-2" style={{ paddingLeft: (r.depth + 1) * 20 + 20 }}>
                └ by {by}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
