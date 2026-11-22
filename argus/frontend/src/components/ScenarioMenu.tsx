import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Backend } from '../lib/api'
import { type DemoScene, type InjectType, type WorldState } from '../lib/types'

export const SCENE_LABEL: Record<DemoScene, string> = {
  start: 'Fresh world',
  promo: 'Planned promo in Germany',
  bot_farm: 'Bot farm in Canada',
  investigate: 'Investigate',
  approve: 'Approve the fix',
  verify: 'Verify after 24 h',
  new_metric: 'Add a new metric',
  tracking_break: 'Tracking break in the UK',
}

const INJECTS: { type: InjectType; label: string }[] = [
  { type: 'promo', label: 'Promo' },
  { type: 'bot_farm', label: 'Bot farm' },
  { type: 'tracking_break', label: 'Tracking break' },
  { type: 'data_delay', label: 'Data delay' },
]

type Props = {
  state: WorldState | null
  backend: Backend
  playing: boolean
  busy: boolean
  lastScene: DemoScene | null
  injectTarget: string
  onPlay: () => void
  onStep: (h: number) => void
  onDemo: (s: DemoScene) => void
  onInject: (t: InjectType) => void
  onAddMetric: () => void
  launchName: string
  onLaunch: () => void
  storyAt: number // story step last run (0–5), -1 before the first
  pendingLabel?: string // the step waits for the presenter: what Next does now (e.g. "Show how ▶")
  onOpenChange?: (open: boolean) => void // the level rail hides while the panel is expanded
  onStory: (i: number) => void
}

// The demo as six story steps, one short line each.
export const STORY = [
  'A calm night',
  'A planned promo: no alarm',
  'Fake accounts in Canada',
  'Approve the fix, check next day',
  'Add a new metric',
  'Launch in Argentina?',
]

// Every operator control lives behind one quiet button, so the recorded screen stays clean.
// Shortcuts work with the menu closed: 1–8 run the demo scenes, Space plays / pauses.
export default function ScenarioMenu(p: Props) {
  const [open, setOpen] = useState(false)
  useEffect(() => p.onOpenChange?.(open), [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const [more, setMore] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [])
  const act = (fn: () => void) => () => {
    fn()
    setOpen(false)
  }
  // Running a story step collapses the panel to a small pill so the globe stays clear for the video.
  const runStep = (i: number) => {
    setOpen(false)
    p.onStory(p.pendingLabel ? p.storyAt : i) // a waiting step continues instead of advancing
  }
  const next = p.storyAt + 1 < STORY.length ? p.storyAt + 1 : 0
  return (
    <div ref={ref} className="absolute bottom-6 left-8 z-[45]">
      {open && (
        <div className="fade-up absolute bottom-12 left-0 max-h-[calc(100vh-9rem)] w-[320px] overflow-y-auto rounded-2xl bg-panel/95 p-5 text-[14px] text-fg shadow-2xl ring-1 ring-line/50">
          <div className="mb-3 text-[16px] font-semibold text-fg">Play the demo</div>
          <ol className="-mx-2">
            {STORY.map((line, i) => {
              const current = i === p.storyAt
              return (
                <li key={line}>
                  <button
                    onClick={() => runStep(i)}
                    disabled={p.busy}
                    className={`flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-[16px] disabled:opacity-40 ${current ? 'bg-line/70 text-fg' : i < p.storyAt ? 'text-fg-2 hover:bg-line/50' : 'text-fg hover:bg-line/50'}`}
                    aria-current={current ? 'step' : undefined}
                  >
                    <span className={`num flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[14px] ${current ? 'bg-ok text-bg' : 'text-fg-2 ring-1 ring-line'}`}>{i + 1}</span>
                    {line}
                  </button>
                </li>
              )
            })}
          </ol>
          <button
            onClick={() => runStep(next)}
            disabled={p.busy}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-ok py-3 text-[16px] font-semibold text-bg hover:brightness-110 disabled:opacity-50"
          >
            {p.busy ? 'Running…' : p.pendingLabel ?? (next === 0 && p.storyAt >= 0 ? 'Start over' : p.storyAt < 0 ? 'Start ▶' : 'Next ▶')}
          </button>

          <button onClick={() => setMore((m) => !m)} className="mt-4 text-fg-2 hover:text-fg" aria-expanded={more}>
            More {more ? '▾' : '▸'}
          </button>
          {more && (
            <div className="fade-in mt-3 border-t border-line pt-3">
              <Group title="Time">
                <div className="flex gap-2">
                  <Btn onClick={p.onPlay} hint="(Space)">{p.playing ? '❚❚ Pause' : '▶ Play'}</Btn>
                  <Btn onClick={() => p.onStep(1)} disabled={p.busy}>+1 h</Btn>
                  <Btn onClick={() => p.onStep(6)} disabled={p.busy}>+6 h</Btn>
                </div>
              </Group>
              <Group title={`Inject a problem into ${p.injectTarget}`}>
                <div className="flex flex-wrap gap-2">
                  {INJECTS.map((x) => (
                    <Btn key={x.type} onClick={act(() => p.onInject(x.type))} disabled={p.busy}>
                      {x.label}
                    </Btn>
                  ))}
                </div>
              </Group>
              <div className="mt-1 text-[14px] text-fg-2">Earth imagery: NASA Visible Earth</div>
            </div>
          )}
        </div>
      )}
      {p.storyAt >= 0 && !open ? (
        <div className="frost flex items-center overflow-hidden rounded-full text-[16px] ring-1 ring-line">
          <button onClick={() => setOpen(true)} className="num px-4 py-2 text-fg-2 hover:text-fg" title={STORY[p.storyAt]}>
            {p.storyAt + 1}/{STORY.length}
          </button>
          <button onClick={() => runStep(next)} disabled={p.busy} className="border-l border-line px-4 py-2 font-semibold text-ok hover:bg-ok/10 disabled:opacity-50">
            {p.busy ? 'Running…' : p.pendingLabel ?? (next === 0 ? 'Start over' : 'Next ▶')}
          </button>
        </div>
      ) : (
        <button
          onClick={() => setOpen((o) => !o)}
          className={`frost rounded-full px-4 py-2 text-[16px] ring-1 transition ${open ? 'text-fg ring-fg-2' : 'text-fg ring-line hover:ring-fg-2'}`}
        >
          {open ? 'Close' : 'Play the demo'}
        </button>
      )}
    </div>
  )
}

function Group({ title, children, last }: { title: string; children: ReactNode; last?: boolean }) {
  return (
    <div className={last ? '' : 'mb-3'}>
      <div className="mb-2 text-fg-2">{title}</div>
      {children}
    </div>
  )
}

function Btn({ children, onClick, disabled, hint }: { children: ReactNode; onClick: () => void; disabled?: boolean; hint?: string }) {
  return (
    <button onClick={onClick} disabled={disabled} className="rounded-lg bg-line/80 px-3 py-1.5 text-fg hover:bg-line disabled:opacity-40">
      {children}
      {hint && <span className="ml-2 text-fg-2">{hint}</span>}
    </button>
  )
}
