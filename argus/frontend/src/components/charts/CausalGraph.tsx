import { useMemo } from 'react'
import { useSvgFont } from '../../lib/useSvgFont'
import { AURORA, MINT, PINK, PINK_SOFT } from '../../lib/theme'
import type { GraphEdge, GraphNode } from '../../lib/types'

// What each node is in the story, shown as a word instead of a z-score.
export type NodeRole = 'broke' | 'symptom' | 'normal' | 'new'
export const ROLE_WORD: Record<NodeRole, string> = { broke: 'cause', symptom: 'affected', normal: 'fine', new: 'new' }

type Dims = { W: number; nodeW: number; nodeH: number; row: number; pad: number }
const DEFAULT_DIMS: Dims = { W: 640, nodeW: 196, nodeH: 52, row: 84, pad: 10 }

type Placed = { id: string; x: number; y: number; dummy: boolean; node?: GraphNode }

// Sugiyama-lite: longest-path layering, dummy nodes for long edges, one barycenter pass.
function layout(nodes: GraphNode[], edges: GraphEdge[], dims: Dims) {
  const { W, nodeW, nodeH, row, pad } = dims
  const ids = new Set(nodes.map((n) => n.id))
  const es = edges.filter((e) => ids.has(e.source) && ids.has(e.target))
  const layer = new Map<string, number>()
  const parents = (id: string) => es.filter((e) => e.target === id).map((e) => e.source)
  const depth = (id: string, seen = new Set<string>()): number => {
    if (layer.has(id)) return layer.get(id)!
    if (seen.has(id)) return 0
    seen.add(id)
    const ps = parents(id)
    const d = ps.length ? Math.max(...ps.map((p) => depth(p, seen) + 1)) : 0
    layer.set(id, d)
    return d
  }
  nodes.forEach((n) => depth(n.id))
  const nLayers = Math.max(0, ...layer.values()) + 1

  const rows: Placed[][] = Array.from({ length: nLayers }, () => [])
  const byId = new Map<string, Placed>()
  nodes.forEach((n) => {
    const p: Placed = { id: n.id, x: 0, y: 0, dummy: false, node: n }
    rows[layer.get(n.id)!].push(p)
    byId.set(n.id, p)
  })
  const chains: { edge: GraphEdge; path: string[] }[] = []
  es.forEach((e, i) => {
    const a = layer.get(e.source)!
    const b = layer.get(e.target)!
    const path = [e.source]
    for (let l = a + 1; l < b; l++) {
      const id = `__d${i}_${l}`
      const p: Placed = { id, x: 0, y: 0, dummy: true }
      rows[l].push(p)
      byId.set(id, p)
      path.push(id)
    }
    path.push(e.target)
    chains.push({ edge: e, path })
  })
  const preds = new Map<string, string[]>()
  chains.forEach((c) => c.path.slice(1).forEach((id, k) => preds.set(id, [...(preds.get(id) ?? []), c.path[k]])))

  rows.forEach((r, l) => {
    if (l > 0) {
      const bary = (p: Placed) => {
        const ps = preds.get(p.id) ?? []
        return ps.length ? ps.reduce((s, q) => s + byId.get(q)!.x, 0) / ps.length : W / 2
      }
      // Within a tie, dummies go to the middle so long edges pass between nodes.
      const groups = new Map<number, Placed[]>()
      r.forEach((p) => {
        const k = Math.round(bary(p))
        groups.set(k, [...(groups.get(k) ?? []), p])
      })
      const sorted = [...groups.entries()].sort((a, b) => a[0] - b[0]).flatMap(([, g]) => {
        const real = g.filter((p) => !p.dummy)
        const mid = Math.ceil(real.length / 2)
        return [...real.slice(0, mid), ...g.filter((p) => p.dummy), ...real.slice(mid)]
      })
      r.splice(0, r.length, ...sorted)
    }
    const units = r.reduce((s, p) => s + (p.dummy ? 0.2 : 1), 0)
    const unit = Math.min((W - 2 * pad) / units, nodeW + 36)
    let x = (W - unit * units) / 2
    r.forEach((p) => {
      const w = unit * (p.dummy ? 0.2 : 1)
      p.x = x + w / 2
      p.y = pad + l * row + nodeH / 2
      x += w
    })
  })
  return { byId, chains, height: pad * 2 + (nLayers - 1) * row + nodeH }
}

function pathFor(points: Placed[], nodeH: number) {
  const a = points[0]
  const b = points[points.length - 1]
  const start: [number, number] = [a.x, a.y + nodeH / 2]
  const end: [number, number] = [b.x, b.y - nodeH / 2]
  if (points.length === 2) {
    const my = (start[1] + end[1]) / 2
    return `M${start[0]},${start[1]} C${start[0]},${my} ${end[0]},${my} ${end[0]},${end[1]}`
  }
  const pts: [number, number][] = [start, ...points.slice(1, -1).map((p) => [p.x, p.y] as [number, number]), end]
  let d = `M${start[0]},${start[1]}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[Math.min(pts.length - 1, i + 2)]
    d += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`
  }
  return d
}

// Full names, wrapped onto at most two lines.
function wrap(label: string, max = 22): string[] {
  if (label.length <= max) return [label]
  const lines = ['']
  for (const w of label.split(' ')) {
    const cur = lines[lines.length - 1]
    if ((cur + ' ' + w).trim().length > max && cur) lines.push(w)
    else lines[lines.length - 1] = (cur + ' ' + w).trim()
  }
  return lines.length > 2 ? [lines[0], lines.slice(1).join(' ')] : lines
}

// Dark text on filled pink: white on #FF4D8D would fail the 4.5:1 contrast rule.
const ROLE_STYLE: Record<NodeRole, { fill: string; stroke: string; dash?: string; text: string; word: string }> = {
  broke: { fill: PINK, stroke: PINK, text: AURORA.bg, word: AURORA.bg },
  symptom: { fill: AURORA.panel, stroke: PINK, dash: '5 4', text: AURORA.fg, word: PINK_SOFT },
  normal: { fill: AURORA.panel, stroke: '#3A3F6B', text: AURORA.fg2, word: AURORA.fg2 },
  new: { fill: AURORA.panel, stroke: MINT, text: AURORA.fg, word: MINT },
}

type Props = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  roles?: Record<string, NodeRole> // defaults from node.state
  showWords?: boolean
  dims?: Partial<Dims>
  // 'story': propagation from the cause in pink. 'types': formula = solid line, possible cause = dashed line with arrow.
  edgeMode?: 'story' | 'types'
  pending?: string[] // not checked yet (replay): drawn faint, no verdict word
  pulse?: string // node that pulses (the cause, once found)
  minText?: number // smallest on-screen text size in px (default 14)
}

// Only the root is "the cause"; other off-pattern steps (broken mechanisms, symptoms) read as "affected".
export function defaultRole(n: GraphNode): NodeRole {
  return n.state === 'root' ? 'broke' : n.state === 'broken' || n.state === 'symptom' ? 'symptom' : 'normal'
}

export default function CausalGraph({ nodes, edges, roles = {}, showWords = true, dims: dimsIn, edgeMode = 'story', pending = [], pulse, minText = 14 }: Props) {
  const dims = { ...DEFAULT_DIMS, ...dimsIn }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const { byId, chains, height } = useMemo(() => layout(nodes, edges, dims), [nodes, edges, dims.W, dims.nodeW, dims.nodeH, dims.row])
  const roleOf = (id: string): NodeRole => roles[id] ?? (byId.get(id)?.node ? defaultRole(byId.get(id)!.node!) : 'normal')
  const hot = (e: GraphEdge) => roleOf(e.source) !== 'normal' && roleOf(e.target) !== 'normal' && roleOf(e.target) !== 'new'
  const { W, nodeW, nodeH } = dims
  const { ref: svgRef, px } = useSvgFont<SVGSVGElement>(W, minText)

  return (
    <svg ref={svgRef} viewBox={`0 0 ${W} ${height}`} className="w-full" style={{ height: 'auto' }} role="img" aria-label="How the business numbers drive each other">
      <defs>
        <marker id="cg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="#3A3F6B" />
        </marker>
        <marker id="cg-arrow-hot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill={PINK} />
        </marker>
        <marker id="cg-arrow-new" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill={MINT} />
        </marker>
      </defs>
      {chains
        .slice()
        .sort((a, b) => Number(hot(a.edge)) - Number(hot(b.edge)))
        .map(({ edge, path }) => {
          const d = pathFor(path.map((id) => byId.get(id)!), nodeH)
          const fresh = roleOf(edge.target) === 'new'
          const h = hot(edge)
          if (edgeMode === 'types') {
            const formula = edge.type === 'formula'
            return (
              <path
                key={`${edge.source}-${edge.target}`}
                d={d}
                fill="none"
                stroke={fresh ? MINT : formula ? '#6B6F9E' : '#4A4E78'}
                strokeWidth={fresh ? 2 : formula ? 1.6 : 1.2}
                strokeDasharray={formula ? undefined : edge.type === 'exogenous' ? '1.5 4' : '5 4'}
                markerEnd={formula && !fresh ? undefined : `url(#${fresh ? 'cg-arrow-new' : 'cg-arrow'})`}
                className={fresh ? 'draw' : undefined}
                pathLength={fresh ? 1 : undefined}
              />
            )
          }
          return (
            <path
              key={`${edge.source}-${edge.target}`}
              d={d}
              fill="none"
              stroke={fresh ? MINT : h ? PINK : '#2A2F5C'}
              strokeWidth={h ? 2 : 1.2}
              strokeDasharray={edge.type === 'exogenous' ? '2 4' : edge.type === 'formula' && !h && !fresh ? '3 3' : undefined}
              markerEnd={`url(#${fresh ? 'cg-arrow-new' : h ? 'cg-arrow-hot' : 'cg-arrow'})`}
              opacity={h || fresh ? 0.95 : 0.8}
            />
          )
        })}
      {[...byId.values()]
        .filter((p) => !p.dummy)
        .map((p, i) => {
          const n = p.node!
          const role = roleOf(n.id)
          const st = ROLE_STYLE[role]
          const lines = wrap(n.label.replace(/\s*\(.*\)$/, ''))
          const waiting = pending.includes(n.id)
          // Text positions follow the on-screen font size (never below 14 px), so name and verdict never overlap.
          const fsz = px(minText)
          const lh = fsz * 1.1
          const withWord = showWords && !waiting
          const block = lines.length * lh + (withWord ? fsz * 1.1 : 0)
          const textTop = (nodeH - block) / 2 + fsz * 0.85
          return (
            <g key={p.id} transform={`translate(${p.x - nodeW / 2},${p.y - nodeH / 2})`} style={{ opacity: waiting ? 0.28 : 1, transition: 'opacity 0.4s ease' }}>
              <g className={role === 'new' ? 'cg-new' : 'fade-in'} style={{ animationDelay: role === 'new' ? '0ms' : `${i * 40}ms` }}>
              {pulse === n.id && <rect width={nodeW} height={nodeH} rx={10} fill="none" stroke={PINK} strokeWidth={2} className="cg-pulse" />}
              <rect width={nodeW} height={nodeH} rx={10} fill={waiting ? 'transparent' : st.fill} stroke={waiting ? '#3A3F6B' : st.stroke} strokeWidth={role === 'normal' || waiting ? 1 : 1.6} strokeDasharray={waiting ? undefined : st.dash} style={{ transition: 'fill 0.35s ease, stroke 0.35s ease' }} />
              {lines.map((line, k) => (
                <text key={k} x={nodeW / 2} y={textTop + k * lh} fontSize={fsz} fontWeight={role === 'broke' ? 600 : 500} fill={st.text} textAnchor="middle">
                  {line}
                </text>
              ))}
              {showWords && !waiting && (
                <text x={nodeW / 2} y={textTop + lines.length * lh + fsz * 0.05} fontSize={fsz} fill={st.word} textAnchor="middle" fontWeight={role === 'broke' ? 600 : 400}>
                  {ROLE_WORD[role]}
                </text>
              )}
              </g>
            </g>
          )
        })}
    </svg>
  )
}
