import { useCallback, useEffect, useRef, useState } from 'react'
import CountrySheet from './components/CountrySheet'
import EvidenceView, { type EvidenceTab } from './components/EvidenceView'
import Globe, { type GlobeMode, type LaunchView } from './components/Globe'
import IssueCard from './components/IssueCard'
import MetricGraphPanel from './components/MetricGraphPanel'
import ScenarioMenu, { SCENE_LABEL, STORY } from './components/ScenarioMenu'
import TimelineStrip from './components/TimelineStrip'
import TopBar from './components/TopBar'
import ErrorBoundary from './components/ErrorBoundary'
import WhatIfCard from './components/WhatIfCard'
import ReplayView from './components/ReplayView'
import { growthLevel, potentialColor, usd } from './lib/theme'
import { api, onBackendChange, type Backend } from './lib/api'
import { explainedLine, foundLine, growthLine, needsDecision, shortTitle, statusSentence } from './lib/incident'
import { PLATFORM_LABEL } from './lib/theme'
import { toOutcome, type MetricOutcome } from './lib/metrics'
import { DEMO_SCENES } from './lib/types'
import type { LaunchForecast, CountryDetail, DemoFrame, DemoScene, IncidentSummary, IncidentDetail, InjectType, MetricGraph, PotentialResponse, TimelineEvent, WorldState } from './lib/types'

// Default targets for the inject actions (the demo script), overridden by the selected country.
const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const markReplaySeen = () => {
  try {
    sessionStorage.setItem('argus-replay-seen', '1')
  } catch {
    /* storage unavailable */
  }
}

// One subtitle per story step (bottom centre, above the timeline).
// One big headline per story step (at most ~6 words); the presenter tells the rest.
const SUBTITLES = [
  'Argus watches 20 markets, every hour',
  'Planned promo: expected, no alarm',
  'Canada: fake growth, caught at 06:00',
  'Four futures tested. One click.',
  'A new metric, placed and watched',
  'What if we launch in Argentina?',
]

type Level = 'earth' | 'country' | 'segment' | 'users'

// The one market the what-if can launch (POST /api/what_if/launch); its place on the globe before the forecast arrives.
const LAUNCH = { iso: 'AR', name: 'Argentina', lng: -63.6, lat: -38.4 }

// Story step of each demo scene ("Play the demo"): 0 calm night · 1 promo · 2 fake accounts · 3 approve + next day ·
// 4 new metric · 5 Argentina launch (not a scene).
const SCENE_STORY: Partial<Record<DemoScene, number>> = { start: 0, promo: 1, bot_farm: 2, investigate: 2, approve: 3, verify: 3, new_metric: 4 }

const INJECT_DEFAULT: Record<InjectType, { country: string; platform?: string }> = {
  promo: { country: 'DE' },
  bot_farm: { country: 'CA', platform: 'android' },
  tracking_break: { country: 'GB', platform: 'ios' },
  data_delay: { country: 'FR' },
}

// Progressive disclosure, mirroring Argus' own abstraction levels:
// L1 home = the Earth and one status sentence; L2 = one issue card; L3 = the full-screen evidence.
export default function App() {
  const [state, setState] = useState<WorldState | null>(null)
  const [backend, setBackend] = useState<Backend>('unknown')
  const [mode, setMode] = useState<GlobeMode>('health')
  const [playing, setPlaying] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [lastScene, setLastScene] = useState<DemoScene | null>(null)
  // L2: the incident on the issue card, and its full detail (first fetch can take a few seconds).
  const [cardId, setCardId] = useState<string | null>(null)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)
  const [approving, setApproving] = useState(false)
  // L3 overlays.
  const [evidence, setEvidence] = useState<{ tab: EvidenceTab; play?: boolean } | null>(null)
  const [graphView, setGraphView] = useState<{ highlight: string[]; outcome?: MetricOutcome | null; openForm?: boolean; typeIn?: boolean } | null>(null)
  const [metricGraph, setMetricGraph] = useState<MetricGraph | null>(null)
  // Country side sheet.
  const [countryIso, setCountryIso] = useState<string | null>(null)
  const [countryDetail, setCountryDetail] = useState<CountryDetail | null>(null)
  const [potential, setPotential] = useState<PotentialResponse | null>(null)
  const [timeline, setTimeline] = useState<TimelineEvent[]>([])
  // What-if launch: 'flying' (dark country, eye investigating) → 'shown' (colour, arcs, card). Read-only.
  const [launch, setLaunch] = useState<{ phase: 'flying' | 'shown'; forecast: LaunchForecast | null } | null>(null)
  const launchRun = useRef(0)
  // Step 6: the card waits until the learning arcs have played (~1.6 s); at once with reduced motion.
  const [launchCardReady, setLaunchCardReady] = useState(false)
  // "Play the demo": the story step last run (0–5), or -1 before the first; its subtitle; whether a step is running.
  const [storyAt, setStoryAt] = useState(-1)
  const [subtitleAt, setSubtitleAt] = useState<number | null>(null)
  const [subtitleShown, setSubtitleShown] = useState(false)
  const [storyRunning, setStoryRunning] = useState(false)
  const storyBusy = useRef(false)
  const [pressing, setPressing] = useState(false)
  const cardIdRef = useRef<string | null>(null)
  // "Watch Argus think" replay; plays by itself the first time an issue opens in this browser session.
  const [replayId, setReplayId] = useState<string | null>(null)
  // Country view: the segment grid is open (the "Segment" level of the rail).
  const [segmentsOpen, setSegmentsOpen] = useState(false)
  // Camera-only focus (no country sheet): the demo's planned-promo step looks at Germany.
  const [lookIso, setLookIso] = useState<string | null>(null)

  const busyRef = useRef(false)
  const backendRef = useRef<Backend>('unknown')
  const dismissed = useRef(new Set<string>())

  const flash = useCallback((msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 3200)
  }, [])

  useEffect(
    () =>
      onBackendChange((b) => {
        const prev = backendRef.current
        backendRef.current = b
        setBackend(b)
        if (prev === 'live' && b === 'mock') flash('Simulation unreachable — showing sample data')
        if (prev === 'mock' && b === 'live') flash('Simulation connected')
      }),
    [flash],
  )

  const closeAll = () => {
    setCardId(null)
    setEvidence(null)
    setGraphView(null)
    setCountryIso(null)
  }

  // Every state update goes through here. A backend restart (in-memory sessions lost) shows up as the clock
  // jumping back; announce it instead of silently swapping the world. Clocks are tracked per data source so a
  // short outage that falls back to the mock world does not hide the restart.
  const lastSeen = useRef<Partial<Record<Backend, number>>>({})
  const expectReset = useRef(false)
  const applyState = useCallback(
    (next: WorldState) => {
      const src = backendRef.current
      const prevT = lastSeen.current[src]
      const restarted = prevT != null && !expectReset.current && next.clock.t < prevT
      lastSeen.current[src] = next.clock.t
      expectReset.current = false
      if (restarted) {
        setCardId(null)
        setEvidence(null)
        setGraphView(null)
        setCountryIso(null)
        setPlaying(false)
        setLastScene(null)
        dismissed.current.clear()
        flash(src === 'live' ? 'Simulation restarted — starting over' : 'Sample data reset')
      }
      setState(next)
    },
    [flash],
  )

  const run = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      try {
        return await fn()
      } catch (e) {
        flash(e instanceof Error ? e.message : String(e))
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [flash],
  )

  // Initial load + background polling while paused.
  useEffect(() => {
    api.state().then(applyState).catch(() => {})
    api.potential().then(setPotential).catch(() => {})
    const id = window.setInterval(() => {
      if (!busyRef.current) api.state().then(applyState).catch(() => {})
    }, 4000)
    return () => window.clearInterval(id)
  }, [applyState])

  // Play: one sim hour every 1.2 s.
  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => {
      if (!busyRef.current) run(() => api.step(1).then(applyState))
    }, 1200)
    return () => window.clearInterval(id)
  }, [playing, run, applyState])

  // A new incident that needs a decision gets the card automatically (unless the user closed it).
  useEffect(() => {
    if (!state || cardId) return
    const next = state.incidents.find((i) => needsDecision(i) && !dismissed.current.has(i.id))
    if (next) setCardId(next.id)
  }, [state, cardId])

  // Keep the card's detail, the country sheet and the metric graph in sync with the world clock.
  const t = state?.clock.t
  useEffect(() => {
    if (!cardId) return setDetail(null)
    let live = true
    api
      .incident(cardId)
      .then((d) => live && setDetail(d))
      .catch(() => live && setCardId(null))
    return () => {
      live = false
    }
  }, [cardId, t])
  useEffect(() => {
    if (countryIso) api.country(countryIso).then(setCountryDetail).catch(() => setCountryDetail(null))
    else setCountryDetail(null)
  }, [countryIso, t])
  useEffect(() => {
    if (graphView) api.graph().then(setMetricGraph).catch(() => setMetricGraph(null))
  }, [graphView, t])
  useEffect(() => {
    if (mode === 'potential') api.potential().then(setPotential).catch(() => {})
  }, [mode, t])
  useEffect(() => {
    if (t != null) api.timeline().then((r) => setTimeline(r.events)).catch(() => setTimeline([]))
  }, [t])

  // Timeline click: incident events open that market's card; others just say what happened.
  const openTimelineEvent = (e: TimelineEvent) => {
    const incs = (state?.incidents ?? []).filter((i) => i.country === e.country)
    const inc = incs.find((i) => i.status !== 'resolved') ?? incs[incs.length - 1]
    if (inc && ['anomaly', 'detected', 'approved', 'verified'].includes(e.kind)) openIncident(inc.id)
    else flash(e.label)
  }

  const step = (h: number) => run(() => api.step(h).then(applyState))

  const inject = (type: InjectType) =>
    run(async () => {
      const target = countryIso ? { country: countryIso, platform: INJECT_DEFAULT[type].platform } : INJECT_DEFAULT[type]
      await api.inject(type, target.country, target.platform)
      flash(`${type.replace('_', ' ')} scheduled in ${target.country}`)
      applyState(await api.state())
    })

  // Each demo scene leaves the screen on the view that tells its part of the story.
  // Time-lapse: repaint the globe, clock and eye hour by hour from the scene's frames (~ms in total).
  const playFrames = async (frames: DemoFrame[] | undefined, ms: number) => {
    if (!frames?.length || reducedMotion()) return
    for (const f of frames) {
      setState((s) =>
        s
          ? {
              ...s,
              clock: f.clock ?? s.clock,
              countries: s.countries.map((c) => ({ ...c, ...(f.countries.find((x) => x.iso === c.iso) ?? {}) })),
              incidents: f.incidents
                ? f.incidents.map((fi) => {
                    const prev = s.incidents.find((x) => x.id === fi.id)
                    return { ...(prev ?? {}), ...fi, title: fi.title ?? prev?.title ?? fi.short_title ?? '' } as IncidentSummary
                  })
                : s.incidents,
            }
          : s,
      )
      await sleep(ms / frames.length)
    }
  }

  const demo = (scene: DemoScene, opts: { lapseMs?: number; typeIn?: boolean; lookAt?: string } = {}) =>
    run(async () => {
      launchRun.current++
      setLaunch(null)
      setLookIso(opts.lookAt ?? null)
      if (scene !== 'approve' && scene !== 'verify') setPending(null)
      if (scene === 'start') expectReset.current = true
      const r = await api.demo(scene, !!opts.lapseMs)
      // The issue's fresh detail is fetched while the time-lapse plays, so the card is up to date when it reappears.
      const freshP = (scene === 'approve' || scene === 'verify') && r.incident_id ? api.incident(r.incident_id).catch(() => null) : null
      if (opts.lapseMs) {
        setCardId(null)
        setEvidence(null)
        await playFrames(r.frames, opts.lapseMs)
      }
      const storyIndex = SCENE_STORY[scene]
      if (storyIndex != null) setStoryAt(storyIndex)
      applyState(r.state)
      setLastScene(scene)
      setEvidence(null)
      setGraphView(null)
      setCountryIso(null)
      switch (scene) {
        case 'start':
          dismissed.current.clear()
          setCardId(null)
          setMode('health')
          break
        case 'promo':
          setCardId(null)
          // Same fly-in as a country click, so the grey country and the status line are on screen together.
          setLookIso(r.state.explained[0]?.country ?? r.state.countries.find((c) => c.status === 'explained')?.iso ?? 'DE')
          break
        case 'investigate':
          if (r.incident_id) setCardId(r.incident_id)
          setEvidence({ tab: 'why' })
          break
        case 'new_metric': {
          // Play what Argus did by itself with the metric the scene just added.
          const node = r.metric?.node as { id?: string; expr?: string } | null | undefined
          const outcome = r.metric ? toOutcome(r.metric as never, '', []) : null
          setGraphView({ highlight: node?.id ? [node.id] : [], outcome, typeIn: opts.typeIn && !reducedMotion() })
          api.graph().then(setMetricGraph).catch(() => {})
          break
        }
        default: // bot_farm, approve, verify, tracking_break: the issue card
          if (r.incident_id) {
            dismissed.current.delete(r.incident_id)
            setCardId(r.incident_id)
            // After approve / verify the card must show the new outcome right away, not a stale one.
            if (freshP) {
              const fresh = await freshP
              if (fresh) setDetail(fresh)
            }
          }
      }
      flash(SCENE_STORY[scene] != null ? STORY[SCENE_STORY[scene]!] : SCENE_LABEL[scene])
      return r
    })

  const approve = async (fixId: string) => {
    if (pending.current?.kind === 'approve') return approveAndVerify()
    if (!cardId) return
    setApproving(true)
    try {
      setDetail(await api.approve(cardId, fixId))
      applyState(await api.state())
      setEvidence(null)
      flash('Fix approved and applied')
    } catch (e) {
      flash(e instanceof Error ? e.message : String(e))
    } finally {
      setApproving(false)
    }
  }

  const selectCountry = (iso: string | null) => {
    setLookIso(null)
    setSegmentsOpen(false)
    setEvidence(null)
    setGraphView(null)
    setCountryIso(iso)
  }
  const openIncident = (id: string) => {
    setCountryIso(null)
    dismissed.current.delete(id)
    setCardId(id)
  }
  const closeCard = () => {
    if (cardId) dismissed.current.add(cardId)
    setCardId(null)
    setEvidence(null)
  }
  const openMetricGraph = () => {
    closeAll()
    setGraphView({ highlight: [] })
  }

  // Keyboard: 1–8 demo scenes, Space play / pause, E evidence, Esc closes the top layer.
  async function whatIfLaunch() {
    const run = ++launchRun.current
    setStoryAt(5)
    setLookIso(null)
    setCountryIso(null)
    setCardId(null)
    setEvidence(null)
    setGraphView(null)
    setLaunch({ phase: 'flying', forecast: null })
    try {
      const [f] = await Promise.all([api.whatIfLaunch(LAUNCH.iso), new Promise((r) => window.setTimeout(r, 2000))])
      if (run === launchRun.current) setLaunch({ phase: 'shown', forecast: f })
    } catch (e) {
      if (run !== launchRun.current) return
      setLaunch(null)
      flash(backend === 'live' ? `The launch forecast failed: ${(e as Error).message}` : 'The launch forecast needs the live simulation.')
    }
  }
  const closeLaunch = () => {
    launchRun.current++
    setLaunch(null)
  }

  useEffect(() => {
    if (!cardId || detail?.id !== cardId || evidence || launch) return
    let seen = false
    try {
      seen = sessionStorage.getItem('argus-replay-seen') === '1'
      sessionStorage.setItem('argus-replay-seen', '1')
    } catch {
      /* storage unavailable: play once per page load */
    }
    if (!seen) setReplayId(cardId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardId, detail?.id])

  // "Play the demo": each step tells its story with a subtitle and its own small choreography.
  // Presenter-paced waiting points: step 3 waits on the card (next → plays the replay); step 4 waits on the four
  // futures (Approve, Next or → approves, then the 24 h time-lapse and the verified card).
  const pending = useRef<{ kind: 'replay'; id: string } | { kind: 'approve' } | null>(null)
  const [pendingKind, setPendingKind] = useState<'replay' | 'approve' | null>(null)
  const setPending = (p: typeof pending.current) => {
    pending.current = p
    setPendingKind(p?.kind ?? null)
  }
  async function approveAndVerify() {
    setPending(null)
    storyBusy.current = true
    setStoryRunning(true)
    try {
      setEvidence(null)
      await demo('approve')
      await sleep(900)
      await demo('verify', { lapseMs: 4000 })
    } finally {
      storyBusy.current = false
      setStoryRunning(false)
    }
  }
  async function runStory(i: number) {
    if (storyBusy.current) return
    const p = pending.current
    if (p?.kind === 'replay') {
      setPending(null)
      setReplayId(p.id)
      return
    }
    if (p?.kind === 'approve') return approveAndVerify()
    storyBusy.current = true
    setStoryRunning(true)
    setStoryAt(i)
    setSubtitleAt(i)
    setSubtitleShown(true)
    try {
      if (i === 0) await demo('start')
      else if (i === 1) {
        const r = await demo('promo')
        await sleep(1900) // after the fly-in, open Germany: its chart shows the line inside the band, with the marker
        const iso = r?.state.explained[0]?.country ?? 'DE'
        selectCountry(iso)
      } else if (i === 2) {
        markReplaySeen() // the presenter starts the replay with the next click
        setCountryIso(null) // leave Germany: the time-lapse plays over North America
        const r = await demo('bot_farm', { lapseMs: 4000, lookAt: 'CA' })
        if (r?.incident_id) setPending({ kind: 'replay', id: r.incident_id })
      } else if (i === 3) {
        if (cardIdRef.current) {
          setPressing(true) // the card's "See fixes & approve ›" is pressed, visibly
          await sleep(900)
          setPressing(false)
          setEvidence({ tab: 'whatif', play: true }) // the four futures, best one glowing
          setPending({ kind: 'approve' }) // wait for the presenter's click to approve
        } else await approveAndVerify()
      } else if (i === 4) await demo('new_metric', { typeIn: true })
      else if (i === 5) {
        flash(STORY[5])
        await whatIfLaunch()
      }
    } finally {
      storyBusy.current = false
      setStoryRunning(false)
    }
  }

  useEffect(() => {
    if (launch?.phase !== 'shown') return setLaunchCardReady(false)
    const t = window.setTimeout(() => setLaunchCardReady(true), reducedMotion() ? 0 : 1700)
    return () => window.clearTimeout(t)
  }, [launch?.phase])

  // Subtitles fade ~6 s after a step starts.
  useEffect(() => {
    if (!subtitleShown) return
    const t = window.setTimeout(() => setSubtitleShown(false), 6000)
    return () => window.clearTimeout(t)
  }, [subtitleShown, subtitleAt])
  // Switching Risks ↔ Growth closes any open issue card or evidence.
  const changeMode = (m: GlobeMode) => {
    if (cardId) closeCard()
    if (launch) closeLaunch()
    setEvidence(null)
    setMode(m)
  }

  const keys = useRef<{ demo: typeof demo; closeTop: () => void; openEvidence: () => void; storyNext?: () => void }>({ demo, closeTop: () => {}, openEvidence: () => {} })
  keys.current = {
    demo,
    closeTop: () => (launch ? closeLaunch() : evidence ? setEvidence(null) : graphView ? setGraphView(null) : countryIso ? setCountryIso(null) : cardId ? closeCard() : undefined),
    openEvidence: () => cardId && setEvidence({ tab: 'why' }),
    storyNext: storyAt >= 0 && !storyRunning ? () => runStory(pending.current ? storyAt : (storyAt + 1) % STORY.length) : undefined,
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const n = Number(e.key)
      if (Number.isInteger(n) && n >= 1 && n <= DEMO_SCENES.length) {
        e.preventDefault()
        keys.current.demo(DEMO_SCENES[n - 1])
      } else if (e.key === 'ArrowRight' && keys.current.storyNext) {
        e.preventDefault()
        keys.current.storyNext()
      } else if (e.key === ' ') {
        e.preventDefault()
        setPlaying((p) => !p)
      } else if (e.key === 'e' || e.key === 'E') keys.current.openEvidence()
      else if (e.key === 'Escape') keys.current.closeTop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  cardIdRef.current = cardId
  const card = state?.incidents.find((i) => i.id === cardId)
  const country = state?.countries.find((c) => c.iso === countryIso) ?? null
  const cardCountry = state?.countries.find((c) => c.iso === card?.country)
  const focusIso = countryIso ?? card?.country ?? null
  // Growth mode: one line from GET /api/potential.
  const pots = potential?.countries ?? []
  const biggest = pots.length ? pots.reduce((a, b) => (b.potential_usd_day > a.potential_usd_day ? b : a)) : null
  const growthTotal = pots.reduce((s, c) => s + c.potential_usd_day, 0)
  const biggestName = biggest ? (state?.countries.find((c) => c.iso === biggest.iso)?.name ?? biggest.iso) : ''
  const growthSummary = biggest
    ? {
        value: `+$${(growthTotal / 1000).toFixed(1)}k`,
        caption: `if users saw more offers they like · biggest: ${biggestName}`,
        tooltip: potential?.summary_line ?? `Showing each country more of what its users like: about +${usd(growthTotal)} a day in total · biggest in ${biggestName}`,
      }
    : null
  // Need for attention per market (0..1): the backend's score, else the open issue's money at stake (cap $250k/week).
  // The globe follows the same fresh state as the card: once an issue is resolved or its fix verified (state or the
  // card's fresh detail), its country is calm right away (no leftover pink or attention), unless another issue is open.
  const settled = new Set<string>()
  for (const i of state?.incidents ?? []) if (i.status === 'resolved' || i.status === 'verified') settled.add(i.country)
  if (detail && (detail.status === 'resolved' || detail.status === 'verified' || detail.verification?.status === 'verified')) settled.add(detail.country)
  for (const i of state?.incidents ?? []) if (i.status === 'open' || i.status === 'persisting') settled.delete(i.country)
  const globeCountries = (state?.countries ?? []).map((c) =>
    settled.has(c.iso) && (c.status === 'incident' || c.status === 'watch') ? { ...c, status: 'ok' as const, attention: 0 } : c,
  )
  const attention: Record<string, number> = Object.fromEntries(
    (state?.countries ?? []).map((c) => {
      if (settled.has(c.iso)) return [c.iso, 0]
      if (c.attention != null) return [c.iso, c.attention]
      const inc = state?.incidents.find((i) => i.country === c.iso && i.status !== 'resolved' && i.status !== 'verified')
      return [c.iso, inc?.risk_7d_usd != null ? Math.min(1, inc.risk_7d_usd / 250_000) : c.status === 'watch' ? 0.4 : 0]
    }),
  )
  // Zoom level for the rail and the eye: Earth › Country › Segment › Users.
  const level: Level = replayId || evidence?.tab === 'who' ? 'users' : evidence || cardId || (segmentsOpen && countryIso) ? 'segment' : countryIso || launch ? 'country' : 'earth'
  const zoomOut = (to: Level) => {
    if (to === 'earth') {
      setEvidence(null)
      setGraphView(null)
      setSegmentsOpen(false)
      setCountryIso(null)
      if (cardId) closeCard()
      if (launch) closeLaunch()
    } else if (to === 'country') {
      const iso = countryIso ?? state?.incidents.find((i) => i.id === cardId)?.country ?? null
      setEvidence(null)
      setSegmentsOpen(false)
      if (cardId && !countryIso) {
        closeCard()
        if (iso) setCountryIso(iso)
      }
    } else if (to === 'segment') {
      if (evidence) setEvidence({ tab: 'why' })
      else setSegmentsOpen(true)
    }
  }
  // "Mon 06:00" for a simulated hour.
  const timeLabel = (t: number) => {
    const c = state?.clock
    if (!c) return `hour ${t}`
    const hNow = c.utc_hour ?? c.t % 24
    const back = c.t - t
    const h = (((hNow - back) % 24) + 24) % 24
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    const today = Math.max(0, days.indexOf(c.day_of_week ?? 'Mon'))
    const shift = Math.floor((hNow - back) / 24)
    return `${days[(((today + shift) % 7) + 7) % 7]} ${String(h).padStart(2, '0')}:00`
  }
  const nameOf = (iso: string) => state?.countries.find((c) => c.iso === iso)?.name ?? iso
  const growthNotes = Object.fromEntries(pots.map((p) => [p.iso, growthLine(p, nameOf(p.iso))]))
  // Planned events: one neutral line per country, for the globe tooltip (and the status sentence).
  const notes = Object.fromEntries((state?.explained ?? []).map((e) => [e.country, explainedLine(e, nameOf(e.country), state?.clock.day_of_week)]))
  // The path at the top-left (persistent breadcrumb), shown below Earth level; each part zooms back to its level.
  const pathIso = countryIso ?? card?.country ?? null
  const pathDetail = card && detail?.id === card.id ? detail : null
  const pathPlatform = pathDetail?.platform ?? (card?.title ?? card?.short_title ?? '').match(/\b(iOS|Android)\b/i)?.[1]
  const pathParts: { label: string; to: Level }[] =
    level === 'earth' || !pathIso || evidence || graphView || replayId || launch
      ? []
      : [
          { label: 'Earth', to: 'earth' },
          { label: nameOf(pathIso), to: 'country' },
          ...((level === 'segment' || level === 'users') && pathPlatform ? [{ label: PLATFORM_LABEL[pathPlatform] ?? pathPlatform, to: 'segment' as Level }] : []),
          ...(level === 'users' && pathDetail?.l3?.summary.n_users ? [{ label: `${pathDetail.l3.summary.n_users.toLocaleString('en-US')} users`, to: 'users' as Level }] : []),
        ]
  const sideOpen = !!(country || card || launch?.phase === 'shown')
  const status = state ? statusSentence(state) : { text: 'Connecting…', tone: 'neutral' as const, mood: 'calm' as const }
  const lf = launch?.forecast
  const launchView: LaunchView | null = launch && state
    ? {
        name: lf?.country.name ?? LAUNCH.name,
        iso: lf?.country.iso ?? LAUNCH.iso,
        lng: lf?.country.lng ?? LAUNCH.lng,
        lat: lf?.country.lat ?? LAUNCH.lat,
        // Predicted colour on the Growth scale: where its daily margin would rank among the watched countries.
        // Same scale as Growth mode: where its predicted margin a day would rank among the markets' opportunities.
        color: potentialColor(lf ? growthLevel(lf.margin_per_day_usd.p50, state.countries.map((c) => c.potential_usd_day)) : 0.5),
        sources: (lf?.learned_from ?? []).map((s) => state.countries.find((c) => c.iso === s.iso)).filter(Boolean).map((c) => [c!.lng, c!.lat] as [number, number]),
        revealed: launch.phase === 'shown',
      }
    : null

  return (
    <div className="relative h-full overflow-hidden bg-bg">
      {state && <Globe countries={globeCountries} mode={mode} selectedIso={launch ? null : focusIso} launch={launchView} lookAt={lookIso} notes={notes} growthNotes={growthNotes} paused={!!(replayId || evidence || graphView)} attention={attention} insetRight={sideOpen ? 460 : 0} utcHour={state.clock.utc_hour ?? state.clock.t % 24} onSelect={selectCountry} />}

      <ErrorBoundary name="header">
      <TopBar status={launch?.phase === 'flying' ? { ...status, mood: 'investigating' } : status} mode={mode} onMode={changeMode} level={level} onMetrics={openMetricGraph} growth={growthSummary ? { ...growthSummary, launchLabel: `New market: ${LAUNCH.name}`, onLaunch: whatIfLaunch } : undefined} />
      </ErrorBoundary>

      {/* Right column: an explained change as a quiet note, then the issue card or the country sheet. */}
      <div className="pointer-events-none absolute bottom-8 right-8 top-24 z-20 flex flex-col items-end gap-4 overflow-y-auto">
        <ErrorBoundary name="panel">
        {launch?.phase === 'shown' && lf && launchCardReady ? (
          <WhatIfCard f={lf} onClose={closeLaunch} presenter={storyAt >= 0} />
        ) : null}
        {country ? (
          <CountrySheet
            country={country}
            detail={countryDetail}
            incident={state?.incidents.find((i) => i.country === country.iso && i.status !== 'resolved')}
            potential={potential}
            growth={mode === 'potential'}
            presenter={storyAt >= 0}
            explainedAt={state?.explained.find((e) => e.country === country.iso)?.t}
            explainedText={(() => { const e = state?.explained.find((x) => x.country === country.iso); return e ? explainedLine(e, null, state?.clock.day_of_week) : undefined })()}
            segments={segmentsOpen}
            onSegments={setSegmentsOpen}
            onWhy={(id) => {
              openIncident(id)
              setEvidence({ tab: 'why' })
            }}
            onUsers={(id) => {
              openIncident(id)
              setEvidence({ tab: 'who' })
            }}
            onClose={() => {
              setCountryIso(null)
              setSegmentsOpen(false)
            }}
          />
        ) : card ? (
          <IssueCard
            key={card.id}
            summary={card}
            detail={detail?.id === card.id ? detail : null}
            countryName={cardCountry?.name ?? card.country}
            foundLine={state ? foundLine(card.found_line ?? (detail?.id === card.id ? detail.found_line : undefined), card.detected_at ?? (detail?.id === card.id ? detail.detected_at : undefined), card.reaction_h ?? (detail?.id === card.id ? detail.reaction_h : undefined), state.clock) : undefined}
            approving={approving}
            onApprove={approve}
            onEvidence={(tab) => setEvidence({ tab })}
            onReplay={() => setReplayId(card.id)}
            pressing={pressing}
            presenter={storyAt >= 0}
            checkedLine={(() => {
              const a = state?.attention
              if (a?.segments_total == null) return undefined
              const n = (x: number | undefined) => (x ?? 0).toLocaleString('en-US')
              const big = (x: number) => (x >= 1e6 ? `${(x / 1e6).toFixed(1).replace(/\.0$/, '')}M` : x >= 1e4 ? `${Math.round(x / 1e3)}k` : n(x))
              return `Argus checked ${n(a.segments_opened)} of ${n(a.segments_total)} segments${a.users_total != null ? ` and ${n(a.users_checked)} of ${big(a.users_total)} users` : ''} to find this.`
            })()}
            onClose={closeCard}
          />
        ) : null}
        </ErrorBoundary>
      </div>

      <ErrorBoundary name="timeline" quiet>
      {state && (state.incidents.some((i) => i.status !== 'resolved') || timeline.some((e) => e.t >= state.clock.t - 72)) && (
        <div className="frost pointer-events-none absolute bottom-4 left-48 z-20 rounded-2xl px-4 pb-2 pt-2 transition-[right] duration-300 ease-out" style={{ right: sideOpen ? 480 : 40 }}>
          <TimelineStrip
            events={timeline}
            now={state.clock.t}
            clock={shortClock(state.clock)}
            countryName={(iso) => state.countries.find((c) => c.iso === iso)?.name ?? iso}
            onOpen={openTimelineEvent}
          />
        </div>
      )}
      </ErrorBoundary>


      <ErrorBoundary name="subtitle" quiet>
      {subtitleAt != null && storyAt >= 0 && subtitleShown && !sideOpen && !evidence && !graphView && !replayId && (
        <div className="pointer-events-none absolute inset-x-0 bottom-[68px] z-[44] flex justify-center px-8">
          <div key={subtitleAt} className="frost fade-up whitespace-nowrap rounded-full px-7 py-3 text-[28px] font-semibold text-fg ring-1 ring-line/40">
            {SUBTITLES[subtitleAt]}
          </div>
        </div>
      )}
      </ErrorBoundary>

      <ErrorBoundary name="demo panel">
      <ScenarioMenu
        state={state}
        backend={backend}
        playing={playing}
        busy={busy || storyRunning}
        lastScene={lastScene}
        injectTarget={country?.name ?? 'the demo markets'}
        onPlay={() => setPlaying((p) => !p)}
        onStep={step}
        onDemo={demo}
        onInject={inject}
        onAddMetric={openMetricGraph}
        launchName={LAUNCH.name}
        onLaunch={whatIfLaunch}
        storyAt={storyAt}
        pendingLabel={pendingKind === 'replay' ? 'Show how ▶' : pendingKind === 'approve' ? 'Approve ▶' : undefined}
        onStory={runStory}
      />
      </ErrorBoundary>


      <ErrorBoundary name="path" quiet>
      {pathParts.length > 0 && (
        <nav aria-label="Where you are" className={`frost pointer-events-auto absolute left-8 z-[45] flex ${mode === 'potential' ? 'top-[176px]' : 'top-[92px]'} items-center gap-1.5 rounded-full px-4 py-1.5 text-[16px]`}>
          {pathParts.map((p, i) => {
            const last = i === pathParts.length - 1
            return (
              <span key={`${p.to}-${p.label}`} className="crumb-part flex items-center gap-1.5" style={{ animationDelay: `${i * 120}ms` }}>
                {i > 0 && <span className="text-fg-2">›</span>}
                {last ? (
                  <span className="font-semibold text-fg" aria-current="location">{p.label}</span>
                ) : (
                  <button onClick={() => zoomOut(p.to)} className="text-fg-2 hover:text-fg">
                    {p.label}
                  </button>
                )}
              </span>
            )
          })}
        </nav>
      )}
      </ErrorBoundary>

      {toast && (
        <div className="frost fade-up pointer-events-none absolute left-1/2 top-28 z-50 -translate-x-1/2 rounded-full px-5 py-2 text-[16px] text-fg ring-1 ring-line/50">
          {toast}
        </div>
      )}

      <ErrorBoundary name="replay">
      {replayId && detail?.id === replayId && (
        <ReplayView detail={detail} countryName={nameOf(detail.country)} onClose={() => setReplayId(null)} />
      )}
      </ErrorBoundary>
      <ErrorBoundary name="evidence">
      {evidence && card && (
        <EvidenceView
          detail={detail?.id === card.id ? detail : null}
          title={card.short_title ?? shortTitle(card.card?.title ?? card.title)}
          initialTab={evidence.tab}
          timeLabel={timeLabel}
          onTab={(tab) => setEvidence((e) => (e ? { ...e, tab, play: false } : e))}
          play={evidence.play}
          approving={approving}
          onApprove={approve}
          onClose={() => setEvidence(null)}
        />
      )}
      </ErrorBoundary>
      <ErrorBoundary name="metrics">
      {graphView && (
        <MetricGraphPanel
          key={graphView.outcome?.kind === 'added' ? graphView.outcome.id : 'graph'}
          graph={metricGraph}
          highlight={graphView.highlight}
          outcome={graphView.outcome}
          openForm={graphView.openForm}
          typeIn={graphView.typeIn}
          onAdded={() => {
            api.graph().then(setMetricGraph).catch(() => {})
            api.state().then(applyState).catch(() => {})
          }}
          onClose={() => setGraphView(null)}
        />
      )}
      </ErrorBoundary>

    </div>
  )
}

// "Mon 12:00": the clock without the day counter or the time zone.
function shortClock(c: WorldState['clock']) {
  if (c.day_of_week != null && c.utc_hour != null) return `${c.day_of_week} ${String(c.utc_hour).padStart(2, '0')}:00`
  const m = c.label.match(/([A-Z][a-z]{2}) (\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : c.label
}
