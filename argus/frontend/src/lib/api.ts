// API client. Talks to the FastAPI backend via the Vite proxy (/api) and falls
// back to the in-browser mock world (src/mock) whenever the backend is unreachable.
import { mockHandle } from '../mock/server'
import type {
  CountryDetail,
  DemoResponse,
  DemoScene,
  IncidentDetail,
  IncidentSummary,
  InjectType,
  GraphTreeRow,
  MetricGraph,
  MetricResponse,
  PotentialResponse,
  TimelineEvent,
  WorldState,
  LaunchForecast,
  OfferMixWhatIf,
} from './types'

export type Backend = 'live' | 'mock' | 'unknown'

const SESSION = (() => {
  try {
    const existing = sessionStorage.getItem('argus-session')
    if (existing) return existing
    const id = Math.random().toString(36).slice(2, 10)
    sessionStorage.setItem('argus-session', id)
    return id
  } catch {
    return 'default'
  }
})()

let backend: Backend = 'unknown'
let lastProbe = 0
const RETRY_MS = 10_000
const listeners = new Set<(b: Backend) => void>()

function setBackend(b: Backend) {
  if (b === backend) return
  backend = b
  listeners.forEach((fn) => fn(b))
}

export function onBackendChange(fn: (b: Backend) => void) {
  listeners.add(fn)
  fn(backend)
  return () => {
    listeners.delete(fn)
  }
}

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const useMock = backend === 'mock' && Date.now() - lastProbe < RETRY_MS
  if (!useMock) {
    try {
      const res = await fetch(`/api${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-Session': SESSION },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const isJson = (res.headers.get('content-type') ?? '').includes('application/json')
      // The Vite proxy answers 500/502/504 with a non-JSON body when the backend is down.
      if (!isJson && res.status >= 500) throw new TypeError('backend unreachable')
      if (!isJson && res.status === 404) throw new TypeError('no backend')
      const data = await res.json()
      setBackend('live')
      if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText)
      return data as T
    } catch (err) {
      if (err instanceof ApiError) throw err
      lastProbe = Date.now()
      setBackend('mock')
    }
  }
  return mockHandle(method, path, body) as T
}

// Live-only request (no sample-data fallback), retried once after a short wait. The what-if launch has no offline
// version, so a brief backend blip (e.g. a restart) must not route it into the sample data and silently drop it.
async function liveRequest<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(`/api${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-Session': SESSION },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText)
      setBackend('live')
      return data as T
    } catch (err) {
      if (err instanceof ApiError || attempt >= 1) throw err
      await new Promise((r) => setTimeout(r, 1500))
    }
  }
}

export const api = {
  state: () => request<WorldState>('GET', '/state'),
  incidents: () => request<IncidentSummary[]>('GET', '/incidents'),
  incident: (id: string) => request<IncidentDetail>('GET', `/incidents/${id}`),
  country: (iso: string) => request<CountryDetail>('GET', `/country/${iso}`),
  potential: () => request<PotentialResponse>('GET', '/potential'),
  graph: () => request<MetricGraph>('GET', '/graph'),
  offerMix: (country: string) => liveRequest<OfferMixWhatIf>('POST', '/what_if/offer_mix', { country }),
  whatIfLaunch: (country = 'AR') => liveRequest<LaunchForecast>('POST', '/what_if/launch', { country }),
  graphTree: () => request<{ roots: string[]; rows: GraphTreeRow[] }>('GET', '/graph/tree'),
  timeline: () => request<{ events: TimelineEvent[] }>('GET', '/timeline'),
  step: (hours: number) => request<WorldState>('POST', '/step', { hours }),
  reset: () => request<WorldState>('POST', '/reset', {}),
  inject: (type: InjectType, country: string, platform?: string) =>
    request<{ ok: boolean; event: unknown }>('POST', '/inject', { type, country, ...(platform ? { platform } : {}) }),
  approve: (id: string, fix_id: string) => request<IncidentDetail>('POST', `/incidents/${id}/approve`, { fix_id }),
  // `formula` is optional: the backend also accepts "name = formula" in `name`, or the formula in `description`.
  addMetric: (name: string, description: string, formula?: string) =>
    request<MetricResponse>('POST', '/metrics', { name, description: description || formula || '', ...(formula ? { formula } : {}) }),
  demo: (scene: DemoScene, frames = false) => request<DemoResponse>('POST', `/demo/${scene}${frames ? '?frames=1' : ''}`),
}
