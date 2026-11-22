// World polygons from world-atlas (Natural Earth, public domain), tagged with ISO-2 codes for the countries
// Argus watches. The 50m file is loaded lazily (its own chunk); rings that cross the antimeridian are split,
// because a ring jumping from +180° to −180° makes MapLibre's globe fill a band around the pole.
import { feature } from 'topojson-client'
import type { Feature, FeatureCollection, Geometry, MultiPolygon, Polygon, Position } from 'geojson'

const ISO2_TO_NUMERIC: Record<string, string> = {
  US: '840', CA: '124', MX: '484', BR: '076', GB: '826', DE: '276', FR: '250', NL: '528', ES: '724', IT: '380',
  PL: '616', SE: '752', TR: '792', ZA: '710', IN: '356', ID: '360', PH: '608', JP: '392', KR: '410', AU: '036',
}
const NUMERIC_TO_ISO2 = new Map(Object.entries(ISO2_TO_NUMERIC).map(([a, n]) => [n, a]))

export type CountryProps = { iso: string; name: string }
export type WorldGeoJson = FeatureCollection<Polygon | MultiPolygon, CountryProps>

// Make consecutive longitudes continuous (no jump larger than 180°).
function unwrap(ring: Position[]): Position[] {
  const out: Position[] = [ring[0]]
  for (let i = 1; i < ring.length; i++) {
    let [x, y] = ring[i]
    const px = out[i - 1][0]
    while (x - px > 180) x -= 360
    while (x - px < -180) x += 360
    out.push([x, y])
  }
  return out
}

// Sutherland–Hodgman clip of a closed ring against the vertical band xmin ≤ x ≤ xmax.
function clipBand(ring: Position[], xmin: number, xmax: number): Position[] {
  const clipEdge = (pts: Position[], inside: (p: Position) => boolean, cross: (a: Position, b: Position) => Position) => {
    const out: Position[] = []
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i]
      const b = pts[(i + 1) % pts.length]
      if (inside(b)) {
        if (!inside(a)) out.push(cross(a, b))
        out.push(b)
      } else if (inside(a)) out.push(cross(a, b))
    }
    return out
  }
  const at = (x: number) => (a: Position, b: Position): Position => [x, a[1] + ((b[1] - a[1]) * (x - a[0])) / (b[0] - a[0])]
  let pts = ring.slice(0, -1)
  pts = clipEdge(pts, (p) => p[0] >= xmin, at(xmin))
  pts = clipEdge(pts, (p) => p[0] <= xmax, at(xmax))
  return pts.length >= 3 ? [...pts, pts[0]] : []
}

// Split a polygon whose outer ring leaves [−180, 180] into pieces that each stay inside it.
function splitPolygon(rings: Position[][]): Position[][][] {
  const outer = unwrap(rings[0])
  const xs = outer.map((p) => p[0])
  const lo = Math.min(...xs)
  const hi = Math.max(...xs)
  if (lo >= -180 && hi <= 180) return [rings]
  const pieces: Position[][][] = []
  for (const shift of [-360, 0, 360]) {
    const shifted = outer.map(([x, y]) => [x + shift, y] as Position)
    const piece = clipBand(shifted, -180, 180)
    if (piece.length) pieces.push([piece]) // holes are dropped on the few split polygons (none matter at this scale)
  }
  return pieces
}

function fixGeometry(g: Polygon | MultiPolygon): MultiPolygon {
  const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates
  return { type: 'MultiPolygon', coordinates: polys.flatMap(splitPolygon) }
}

let cache: Promise<WorldGeoJson> | null = null

export function loadWorldGeoJson(): Promise<WorldGeoJson> {
  cache ??= import('world-atlas/countries-50m.json').then((mod) => {
    const topo = (mod as { default?: unknown }).default ?? mod
    const t = topo as any
    const fc = feature(t, t.objects.countries) as unknown as FeatureCollection<Geometry, { name?: string }>
    const features: Feature<Polygon | MultiPolygon, CountryProps>[] = fc.features
      .filter((f) => f.geometry && (f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon'))
      .filter((f) => f.properties?.name !== 'Antarctica')
      .map((f) => ({
        type: 'Feature',
        geometry: fixGeometry(f.geometry as Polygon | MultiPolygon),
        properties: {
          iso: NUMERIC_TO_ISO2.get(String(f.id ?? '').padStart(3, '0')) ?? '',
          name: String(f.properties?.name ?? ''),
        },
      }))
    return { type: 'FeatureCollection', features }
  })
  return cache
}

// Visual centre of a country: the area centroid of its largest polygon (area weighted by cos(latitude)), so the US is
// not pulled toward Alaska and France not toward its overseas territories.
export function visualCenter(g: Polygon | MultiPolygon): [number, number] {
  const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates
  let best: [number, number] = [0, 0]
  let bestArea = -1
  for (const poly of polys) {
    const ring = poly[0]
    let a = 0, cx = 0, cy = 0
    for (let i = 0; i < ring.length - 1; i++) {
      const [x0, y0] = ring[i]
      const [x1, y1] = ring[i + 1]
      const f = x0 * y1 - x1 * y0
      a += f
      cx += (x0 + x1) * f
      cy += (y0 + y1) * f
    }
    if (!a) continue
    const centre: [number, number] = [cx / (3 * a), cy / (3 * a)]
    const area = Math.abs(a / 2) * Math.cos((centre[1] * Math.PI) / 180)
    if (area > bestArea) (bestArea = area), (best = centre)
  }
  return best
}
