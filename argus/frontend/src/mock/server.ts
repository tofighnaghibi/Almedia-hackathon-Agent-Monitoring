// Routes /api requests to the in-browser MockWorld when the backend is unreachable.
import type { DemoScene, InjectType } from '../lib/types'
import { MockWorld } from './world'

let world = new MockWorld()

class MockError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

function demo(scene: DemoScene) {
  let incident_id: string | undefined
  let metric: unknown
  const latest = () => world.incidents[0]?.id
  switch (scene) {
    case 'start':
      world = new MockWorld()
      break
    case 'promo':
      world.inject('promo', 'DE')
      world.step(3)
      break
    case 'bot_farm':
      world.inject('bot_farm', 'CA', 'android')
      world.step(4)
      incident_id = latest()
      break
    case 'investigate':
      world.step(2)
      incident_id = latest()
      break
    case 'approve': {
      const inc = world.incidents.find((i) => i.kind === 'bot_farm' && i.appliedAt == null)
      if (inc) world.approve(inc.id, 'block_cluster')
      world.step(1)
      incident_id = inc?.id
      break
    }
    case 'verify':
      world.step(24)
      incident_id = world.incidents.find((i) => i.kind === 'bot_farm')?.id
      break
    case 'new_metric':
      metric = world.addMetric('Reward cost per completed offer', 'rewards / completions')
      world.step(1)
      break
    case 'tracking_break':
      world.inject('tracking_break', 'GB', 'ios')
      world.step(3)
      incident_id = latest()
      break
    default:
      throw new MockError(404, `unknown scene ${scene}`)
  }
  return { scene, state: world.state(), ...(incident_id ? { incident_id } : {}), ...(metric ? { metric } : {}) }
}

function route(method: string, path: string, body: any): unknown {
  const p = path.split('?')[0]
  let m: RegExpMatchArray | null
  if (method === 'GET') {
    if (p === '/state') return world.state()
    if (p === '/incidents') return world.state().incidents
    if ((m = p.match(/^\/incidents\/([\w-]+)$/))) {
      const d = world.incident(m[1])
      if (!d) throw new MockError(404, 'unknown incident')
      return d
    }
    if ((m = p.match(/^\/country\/(\w+)$/))) {
      const d = world.country(m[1])
      if (!d) throw new MockError(404, 'unknown country')
      return d
    }
    if (p === '/potential') return world.potentialResponse()
    if (p === '/graph') return world.graph()
    if (p === '/graph/tree') return world.graphTree()
    if (p === '/timeline') return world.timeline()
  }
  if (method === 'POST') {
    if (p === '/step') {
      world.step(Math.max(1, Math.min(168, Number(body?.hours ?? 1))))
      return world.state()
    }
    if (p === '/reset') {
      world = new MockWorld()
      return world.state()
    }
    if (p === '/inject') {
      const ev = world.inject(body.type as InjectType, body.country, body.platform)
      return { ok: true, event: ev }
    }
    if ((m = p.match(/^\/incidents\/([\w-]+)\/approve$/))) {
      const d = world.approve(m[1], body?.fix_id)
      if (!d) throw new MockError(404, 'unknown incident')
      return d
    }
    if (p === '/metrics') {
      if (!body?.name) throw new MockError(400, 'name required')
      return world.addMetric(body.name, body.description ?? '', body.formula)
    }
    if ((m = p.match(/^\/demo\/(\w+)$/))) return demo(m[1] as DemoScene)
  }
  throw new MockError(404, `no mock for ${method} ${p}`)
}

export async function mockHandle(method: string, path: string, body?: unknown): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 60 + Math.random() * 80))
  return clone(route(method, path, body))
}
