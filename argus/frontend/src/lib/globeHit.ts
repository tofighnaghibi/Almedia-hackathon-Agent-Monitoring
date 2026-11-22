// Hit-testing helpers for the MapLibre globe. Near the limb, queryRenderedFeatures can return countries
// for pixels that are in empty space or on the far side; these checks keep hover/click on the surface.
import type { Map as MapLibreMap, PointLike } from 'maplibre-gl'
import { Point } from 'maplibre-gl'
import type { Geometry, Position } from 'geojson'

// True when the pixel lies on the visible globe surface.
export function isOnGlobe(map: MapLibreMap, p: { x: number; y: number }): boolean {
  const pt = new Point(p.x, p.y)
  const transform = (map as unknown as { transform?: { isPointOnMapSurface?: (p: Point) => boolean } }).transform
  if (typeof transform?.isPointOnMapSurface === 'function') return transform.isPointOnMapSurface(pt)
  // Fallback: off-globe pixels unproject to a clamped edge point that does not round-trip to the same pixel.
  const back = map.project(map.unproject(pt as PointLike))
  return Math.hypot(back.x - p.x, back.y - p.y) < 1.5
}

const normLng = (lng: number) => ((((lng + 180) % 360) + 360) % 360) - 180

function inRing(lng: number, lat: number, ring: Position[]): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function inPolygon(lng: number, lat: number, rings: Position[][]): boolean {
  if (!rings.length || !inRing(lng, lat, rings[0])) return false
  return !rings.slice(1).some((hole) => inRing(lng, lat, hole))
}

// True when the lng/lat lies inside the (Multi)Polygon. Checked at lng and lng ± 360, since world-atlas
// rings that cross the antimeridian (Russia, Fiji) use longitudes beyond ±180.
export function geometryContains(g: Geometry, lng: number, lat: number): boolean {
  const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : []
  const x = normLng(lng)
  return polys.some((rings) => [x, x - 360, x + 360].some((xx) => inPolygon(xx, lat, rings)))
}
