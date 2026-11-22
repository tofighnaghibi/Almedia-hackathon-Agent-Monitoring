import { useEffect, useId, useRef, useState } from 'react'

export type EyeMood = 'calm' | 'problem' | 'investigating'

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// "Levels of focus": an almond outline with three dashed rings around the pupil, for Argus' levels
// (business → segments → users). Pure SVG + CSS, driven by the same state as the status sentence.
// Calm: the rings turn slowly, a blink every 6–8 s. Investigating: the rings turn ~3× faster and the pupil narrows.
// Problem: outline and pupil pink, the eye widens a little and looks toward the issue card (right).
// Back to calm (resolved): mint with one blink. Reduced motion: colour only.
const ALMOND = 'M2 13 Q22 -3.5 42 13 Q22 29.5 2 13 Z'

export default function ArgusEye({ mood, height = 26, level = 'earth' }: { mood: EyeMood; height?: number; level?: 'earth' | 'country' | 'segment' | 'users' }) {
  const lit = level === 'users' ? 'inner' : level === 'segment' ? 'middle' : 'outer'
  const ring = (r: 'outer' | 'middle' | 'inner') => ({ opacity: lit === r ? 1 : 0.42, strokeWidth: lit === r ? 2 : undefined, transition: 'opacity 0.4s ease' })
  const clip = useId()
  const [blink, setBlink] = useState(false)
  const prev = useRef(mood)
  const still = reducedMotion()

  const doBlink = () => {
    setBlink(true)
    window.setTimeout(() => setBlink(false), 200)
  }

  // Idle blink with jitter while calm.
  useEffect(() => {
    if (still || mood !== 'calm') return
    let t: number
    const next = () => (t = window.setTimeout(() => (doBlink(), next()), 6000 + Math.random() * 2000))
    next()
    return () => window.clearTimeout(t)
  }, [mood, still])

  // Resolved: one blink when coming back to calm.
  useEffect(() => {
    if (prev.current !== 'calm' && mood === 'calm' && !still) doBlink()
    prev.current = mood
  }, [mood, still])

  const problem = mood === 'problem'
  const accent = problem ? 'var(--color-problem)' : 'var(--color-ok)'
  const look = problem && !still ? 'translate(2.4px, 0.4px)' : 'translate(0, 0)'
  return (
    <svg
      className={`argus-eye argus-eye--${mood}${blink ? ' argus-eye--blink' : ''}`}
      viewBox="0 0 44 26"
      width={(height * 44) / 26}
      height={height}
      role="img"
      aria-label={problem ? 'Argus: needs attention' : mood === 'investigating' ? 'Argus: looking closer' : 'Argus: all calm'}
    >
      <defs>
        <clipPath id={clip}>
          <path d={ALMOND} />
        </clipPath>
      </defs>
      <g className="argus-eye__lid">
        <g clipPath={`url(#${clip})`}>
          <g className="argus-eye__iris" style={{ transform: look }}>
            <circle className="argus-eye__ring argus-eye__ring--outer" cx="22" cy="13" r="8.2" fill="none" stroke="var(--color-ok)" strokeWidth="1.5" strokeDasharray="14 3" style={ring('outer')} />
            <circle className="argus-eye__ring argus-eye__ring--middle" cx="22" cy="13" r="5.7" fill="none" stroke="#8B7CFF" strokeWidth="1.4" strokeDasharray="9 2.5" style={ring('middle')} />
            <circle className="argus-eye__ring argus-eye__ring--inner" cx="22" cy="13" r="3.4" fill="none" stroke="var(--color-ok)" strokeWidth="1.3" strokeDasharray="5 2" style={ring('inner')} />
            <circle className="argus-eye__pupil" cx="22" cy="13" r="1.5" style={{ fill: accent }} />
          </g>
        </g>
        <path className="argus-eye__outline" d={ALMOND} fill="none" strokeWidth="1.4" strokeLinejoin="round" style={{ stroke: problem ? accent : 'var(--color-fg-2)' }} />
      </g>
    </svg>
  )
}
