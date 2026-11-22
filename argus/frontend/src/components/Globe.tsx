import { useEffect, useRef } from 'react'
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { loadWorldGeoJson, visualCenter } from '../lib/countryGeometry'
import { geometryContains, isOnGlobe } from '../lib/globeHit'
import { WORLD_COORDS, nightImage } from '../lib/daynight'
import { EarthImagery, graticule } from '../lib/earthImagery'
import type { GeoJSONSource, ImageSource } from 'maplibre-gl'
import { AURORA, STATUS_COLOR, STATUS_LABEL, growthLevel, potentialColor, usd } from '../lib/theme'
import type { CountryState, CountryStatus } from '../lib/types'

export type GlobeMode = 'health' | 'potential'

// "What if we launch in …?": a country Argus does not watch yet. It starts dark, then fades to its predicted colour
// while soft violet arcs travel in from the countries the prediction learned from.
export type LaunchView = {
  name: string // world-atlas name, used when the country is not one of the watched ones
  iso?: string
  lng: number
  lat: number
  color: string
  sources: [number, number][]
  revealed: boolean
}

type Props = {
  countries: CountryState[]
  mode: GlobeMode
  selectedIso: string | null
  insetRight?: number // px covered by the right-hand card; the globe centres in the remaining space
  utcHour?: number // drives the day/night shading
  launch?: LaunchView | null
  notes?: Record<string, string> // per-country tooltip line, e.g. "Germany +31% · planned promo, no alarm"
  attention?: Record<string, number> // 0..1 per country: money at stake × confidence
  growthNotes?: Record<string, string> // Growth mode tooltip: what users like vs see, and what it's worth
  lookAt?: string | null // fly the camera to this country without selecting it (demo: the planned promo)
  paused?: boolean // something covers the globe (replay, evidence, metric view): no idle spin
  onSelect: (iso: string | null) => void
}

const BASE_LAND = AURORA.land
const HOME_VIEW = { center: [-30, 28] as [number, number], zoom: 1.95 }

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  projection: { type: 'globe' },
  sources: {},
  layers: [{ id: 'ocean', type: 'background', paint: { 'background-color': AURORA.oceanOut } }],
}

function fillColors(countries: CountryState[], mode: GlobeMode): Record<string, string> {
  if (mode === 'health') return Object.fromEntries(countries.map((c) => [c.iso, STATUS_COLOR[c.status]]))
  // Growth: one hue, dark → bright mint by opportunity $/day on a log scale (colour and opacity both follow it).
  const values = countries.map((c) => c.potential_usd_day)
  return Object.fromEntries(countries.map((c) => [c.iso, potentialColor(growthLevel(c.potential_usd_day, values))]))
}

function growthOpacity(countries: CountryState[]): unknown {
  const values = countries.map((c) => c.potential_usd_day)
  return ['match', ['get', 'iso'], ...countries.flatMap((c) => [c.iso, Math.round((0.22 + 0.7 * growthLevel(c.potential_usd_day, values)) * 100) / 100]), 0.3]
}

function matchExpr(colors: Record<string, string>, fallback: string): any {
  const entries = Object.entries(colors)
  if (!entries.length) return fallback
  return ['match', ['get', 'iso'], ...entries.flat(), fallback]
}

// Health-mode fill opacity. Healthy markets: intensity by revenue share (US strongest), kept faint.
// During an incident every other market dims and the affected one carries `pulse`.
// Risks: one ordered scale, quiet → loud, carried by brightness and motion as well as hue (colour-blind safe).
// fine = dim teal (a little brighter for big markets), explained = soft grey-violet, still; watching = soft pink with a
// slow breathe; problem = bright pink with a glow. Within pink, brightness follows attention (money at stake ×
// confidence), never the raw deviation, so a planned promo can't look louder than a real problem.
function opacityExpr(countries: CountryState[], breathe: number, attention: Record<string, number> = {}): unknown {
  const maxRev = Math.max(1, ...countries.map((c) => c.kpis.revenue))
  const anyIncident = countries.some((c) => c.status === 'incident')
  const entries = countries.map((c) => {
    const att = Math.min(1, Math.max(0, attention[c.iso] ?? 0.5))
    let o: number
    if (c.status === 'incident') o = 0.8 + 0.2 * att
    else if (c.status === 'watch') o = 0.32 + 0.2 * att + 0.16 * breathe
    else if (c.status === 'explained') o = 0.45
    else if (c.status === 'waiting') o = 0.4
    else o = 0.18 + 0.2 * Math.sqrt(c.kpis.revenue / maxRev)
    if (anyIncident && c.status !== 'incident') o *= 0.6
    return [c.iso, Math.round(o * 100) / 100]
  })
  return ['match', ['get', 'iso'], ...entries.flat(), 0.85]
}

// Only issues get a marker; explained / waiting markets are already told apart by their fill.
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
// One camera for every fly-in (country click, issue, what-if launch): the same on-screen globe size, centred on the
// country's visual centre, never tilted or rotated. MapLibre's globe draws a given zoom larger away from the equator
// (by 1/cos(latitude), to match Web Mercator), so the zoom is corrected by log2(cos(latitude)) to keep the globe the
// same size everywhere. FLY_ZOOM is that size expressed as the zoom at the equator; it suits a mid-size country.
export const FLY_ZOOM = 3
const flyZoom = (lat: number) => FLY_ZOOM + Math.log2(Math.cos((Math.min(80, Math.abs(lat)) * Math.PI) / 180))
// True while the map can be painted: created, loaded and not removed (style is gone after map.remove()).
const alive = (map: MapLibreMap | null): map is MapLibreMap => !!map && !!(map as unknown as { style?: unknown }).style
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// Great-circle path between two points (lng/lat), with continuous longitudes.
function arc(a: [number, number], b: [number, number], n = 64): [number, number][] {
  const r = Math.PI / 180
  const v = ([lng, lat]: [number, number]) => [Math.cos(lat * r) * Math.cos(lng * r), Math.cos(lat * r) * Math.sin(lng * r), Math.sin(lat * r)]
  const p = v(a), q = v(b)
  const w = Math.acos(Math.min(1, Math.max(-1, p[0] * q[0] + p[1] * q[1] + p[2] * q[2])))
  const out: [number, number][] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    const s1 = w ? Math.sin((1 - t) * w) / Math.sin(w) : 1 - t
    const s2 = w ? Math.sin(t * w) / Math.sin(w) : t
    const x = s1 * p[0] + s2 * q[0], y = s1 * p[1] + s2 * q[1], z = s1 * p[2] + s2 * q[2]
    let lng = Math.atan2(y, x) / r
    const lat = Math.atan2(z, Math.hypot(x, y)) / r
    if (out.length) {
      const prev = out[out.length - 1][0]
      while (lng - prev > 180) lng -= 360
      while (lng - prev < -180) lng += 360
    }
    out.push([lng, lat])
  }
  return out
}

// A bright head travelling along each arc toward the new country, with a soft tail over a faint trail: a shimmer.
function shimmer(head: number, fade: number): any {
  const stops: (number | string)[] = []
  for (let i = 0; i <= 24; i++) {
    const p = i / 24
    const a = p <= head ? Math.max(0, 1 - (head - p) / 0.5) : Math.max(0, 1 - (p - head) / 0.05)
    stops.push(p, `rgba(176, 164, 255, ${Math.min(1, (0.16 + 0.84 * a) * fade).toFixed(3)})`)
  }
  return ['interpolate', ['linear'], ['line-progress'], ...stops]
}

const MARKER_STATUSES: CountryStatus[] = ['incident', 'watch']

export default function Globe({ countries, mode, selectedIso, insetRight = 0, utcHour, launch = null, notes = {}, growthNotes = {}, attention = {}, lookAt = null, paused = false, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const loadedRef = useRef(false)
  const markersRef = useRef(new Map<string, { marker: maplibregl.Marker; status: CountryStatus }>())
  const countriesRef = useRef(countries)
  const modeRef = useRef(mode)
  const selectedRef = useRef(selectedIso)
  const onSelectRef = useRef(onSelect)
  const hourRef = useRef(utcHour ?? 12)
  const earthRef = useRef<EarthImagery | null>(null)
  const launchRef = useRef(launch)
  const pausedRef = useRef(paused)
  const lookAtRef = useRef(lookAt)
  lookAtRef.current = lookAt
  pausedRef.current = paused
  const centersRef = useRef(new Map<string, [number, number]>()) // visual centre by ISO-2 code and by name
  // Polar caps: MapLibre fills both with the background colour. Use the colour of the pole facing the camera, blended
  // across the equator (where both caps sit edge-on at the limb), so neither pole shows a dark disc at any angle.
  const polesRef = useRef<{ north: [number, number, number]; south: [number, number, number] } | null>(null)
  const poleKey = useRef('')
  const paintPoles = () => {
    const map = mapRef.current
    const p = polesRef.current
    if (!map || !p) return
    const t = Math.min(1, Math.max(0, 0.5 + map.getCenter().lat / 16)) // 0 = south faces the camera, 1 = north
    const c = p.north.map((n, i) => Math.round(p.south[i] + (n - p.south[i]) * t))
    const key = c.join(', ')
    if (key === poleKey.current) return
    poleKey.current = key
    map.setPaintProperty('ocean', 'background-color', `rgb(${key})`)
  }
  launchRef.current = launch
  const notesRef = useRef(notes)
  notesRef.current = notes
  const growthRef = useRef(growthNotes)
  growthRef.current = growthNotes
  const attentionRef = useRef(attention)
  attentionRef.current = attention
  countriesRef.current = countries
  modeRef.current = mode
  selectedRef.current = selectedIso
  onSelectRef.current = onSelect

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE,
      center: HOME_VIEW.center,
      zoom: HOME_VIEW.zoom,
      attributionControl: false,
      renderWorldCopies: false,
      maxZoom: 5,
      minZoom: 1,
      pixelRatio: Math.min(window.devicePixelRatio || 1, 2), // crisp on Retina without 3× GPU cost
    })
    mapRef.current = map
    if (import.meta.env.DEV) (window as unknown as { __argusMap?: MapLibreMap }).__argusMap = map // for edge hit-tests
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, className: 'argus-popup' })

    let lastInteraction = -Infinity
    let prev = performance.now()
    let frame = 0
    let lastPulse = 0
    let lastHide = 0
    const note = () => (lastInteraction = performance.now())
    // Idle spin: resumes ~4 s after the last pointer activity on the globe when nothing is open and no fly-in runs,
    // easing its speed up over ~1 s. Hover, drag or click stop it again. Reduced motion: no auto-spin.
    let rampStart: number | null = null
    const spin = (now: number) => {
      const dt = Math.min(now - prev, 100)
      prev = now
      const idle = !reducedMotion() && !selectedRef.current && !lookAtRef.current && !launchRef.current && !pausedRef.current && now - lastInteraction > 4000 && !map.isMoving()
      if (!idle) rampStart = null
      else {
        rampStart ??= now
        const k = Math.min(1, (now - rampStart) / 1000)
        const c = map.getCenter()
        map.jumpTo({ center: [c.lng + dt * 0.0035 * k * k * (3 - 2 * k), c.lat] })
      }
      // Pulse the affected country (≈15 fps is plenty for a slow breathe).
      if (loadedRef.current && now - lastPulse > 66 && countriesRef.current.some((c) => c.status === 'watch') && modeRef.current === 'health' && !launchRef.current) {
        lastPulse = now
        map.setPaintProperty('watched', 'fill-opacity', opacityExpr(countriesRef.current, 0.5 + 0.5 * Math.sin(now / 900), attentionRef.current) as any)
      }
      if (now - lastHide > 120) {
        lastHide = now
        hideFarMarkers()
      }
      frame = requestAnimationFrame(spin)
    }
    // Markers on the hidden hemisphere (or right on the horizon) are hidden outright.
    const hideFarMarkers = () => {
      const c = map.getCenter()
      const toRad = Math.PI / 180
      for (const { marker } of markersRef.current.values()) {
        const p = marker.getLngLat()
        const cos = Math.sin(c.lat * toRad) * Math.sin(p.lat * toRad) + Math.cos(c.lat * toRad) * Math.cos(p.lat * toRad) * Math.cos((p.lng - c.lng) * toRad)
        marker.getElement().style.visibility = cos > 0.15 ? 'visible' : 'hidden'
      }
    }
    const canvas = map.getCanvas()
    canvas.addEventListener('pointerdown', note)
    canvas.addEventListener('pointermove', note)
    canvas.addEventListener('wheel', note, { passive: true })
    frame = requestAnimationFrame(spin)

    map.on('load', () => {
      map.setSky({
        'sky-color': AURORA.bg,
        'horizon-color': '#2B2470',
        'fog-color': AURORA.oceanIn,
        'fog-ground-blend': 0.6,
        'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.8, 4, 0.35], // violet glow around the globe
      } as any)
      // Country polygons arrive lazily (50m Natural Earth); until then the ocean shows alone.
      map.addSource('world', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      loadWorldGeoJson().then((world) => {
        geometryByIso = new Map(world.features.filter((f) => f.properties.iso).map((f) => [f.properties.iso, f.geometry]))
        for (const f of world.features) {
          const c = visualCenter(f.geometry)
          if (f.properties.iso) centersRef.current.set(f.properties.iso, c)
          if (f.properties.name) centersRef.current.set(f.properties.name, c)
        }
        if (alive(map)) (map.getSource('world') as GeoJSONSource | undefined)?.setData(world as any)
      })
      map.addLayer({
        id: 'land',
        type: 'fill',
        source: 'world',
        paint: { 'fill-color': BASE_LAND, 'fill-opacity': 1 },
      })
      map.addLayer({
        id: 'watched',
        type: 'fill',
        source: 'world',
        filter: ['!=', ['get', 'iso'], ''],
        paint: { 'fill-color': BASE_LAND, 'fill-opacity': 0.82, 'fill-color-transition': { duration: 800 } as any },
      })
      // Problem glow: a soft pink halo along the border of a market with an open problem (Risks mode only).
      map.addLayer({
        id: 'problem-glow',
        type: 'line',
        source: 'world',
        filter: ['==', ['get', 'iso'], '__none__'],
        paint: { 'line-color': AURORA.problem, 'line-width': 9, 'line-blur': 7, 'line-opacity': 0.6 },
      })
      map.addLayer({
        id: 'borders',
        type: 'line',
        source: 'world',
        paint: { 'line-color': AURORA.border, 'line-width': 0.5 },
      })
      map.addLayer({
        id: 'watched-outline',
        type: 'line',
        source: 'world',
        filter: ['!=', ['get', 'iso'], ''],
        paint: { 'line-color': AURORA.fg2, 'line-opacity': 0.25, 'line-width': 0.5 },
      })
      // Night side: one smooth image from the sun's elevation (updated when the hour changes).
      map.addSource('night', { type: 'image', url: nightImage(hourRef.current), coordinates: WORLD_COORDS })
      map.addLayer({ id: 'night', type: 'raster', source: 'night', paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } })
      // Faint graticule.
      map.addSource('graticule', { type: 'geojson', data: graticule() })
      map.addLayer(
        { id: 'graticule', type: 'line', source: 'graticule', paint: { 'line-color': AURORA.fg2, 'line-opacity': 0.07, 'line-width': 0.5 } },
        'borders',
      )
      // NASA day / night imagery, loaded lazily; until then the flat ocean and land show.
      EarthImagery.load()
        .then(async (earth) => {
          earthRef.current = earth
          const { url, poles } = await earth.render(hourRef.current)
          if (!mapRef.current) return
          polesRef.current = poles
          paintPoles()
          map.addSource('earth', { type: 'image', url, coordinates: WORLD_COORDS })
          map.addLayer({ id: 'earth', type: 'raster', source: 'earth', paint: { 'raster-fade-duration': 0, 'raster-opacity': 1 } }, 'land')
          map.setPaintProperty('land', 'fill-opacity-transition', { duration: 800 } as any)
          map.setPaintProperty('land', 'fill-opacity', 0)
          map.setLayoutProperty('night', 'visibility', 'none') // night is part of the blended texture now
        })
        .catch(() => {}) // keep the flat look if the images can't load
      // What-if launch: the new country (dark, then its predicted colour) and the learning arcs.
      map.addLayer({
        id: 'launch-fill',
        type: 'fill',
        source: 'world',
        filter: ['==', ['get', 'name'], '__none__'],
        paint: { 'fill-color': AURORA.bg, 'fill-opacity': 0, 'fill-color-transition': { duration: 1400 } as any, 'fill-opacity-transition': { duration: 1400 } as any },
      })
      map.addLayer({
        id: 'launch-outline',
        type: 'line',
        source: 'world',
        filter: ['==', ['get', 'name'], '__none__'],
        paint: { 'line-color': AURORA.fg2, 'line-opacity': 0.6, 'line-width': 1, 'line-dasharray': [2, 2] },
      })
      map.addSource('launch-arcs', { type: 'geojson', lineMetrics: true, data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'launch-arcs',
        type: 'line',
        source: 'launch-arcs',
        layout: { 'line-cap': 'round' },
        paint: { 'line-width': 4, 'line-blur': 1.5, 'line-gradient': shimmer(0, 0) },
      })
      map.addLayer({
        id: 'hover-outline',
        type: 'line',
        source: 'world',
        filter: ['==', ['get', 'iso'], '__none__'],
        paint: { 'line-color': AURORA.fg, 'line-opacity': 0.7, 'line-width': 1.2 },
      })
      map.addLayer({
        id: 'selected-outline',
        type: 'line',
        source: 'world',
        filter: ['==', ['get', 'iso'], '__none__'],
        paint: { 'line-color': AURORA.fg, 'line-width': 1.6 },
      })
      loadedRef.current = true
      paint()
    })

    // Hover and click only react to watched countries under the pointer on the visible globe surface.
    let geometryByIso = new Map<string, import('geojson').Geometry>()
    const countryAt = (point: { x: number; y: number }): string | null => {
      if (!loadedRef.current || !isOnGlobe(map, point)) return null
      const { lng, lat } = map.unproject([point.x, point.y])
      for (const f of map.queryRenderedFeatures([point.x, point.y], { layers: ['watched'] })) {
        const iso = f.properties?.iso as string | undefined
        const g = iso ? geometryByIso.get(iso) : undefined
        if (iso && g && geometryContains(g, lng, lat)) return iso
      }
      return null
    }
    let hovered: string | null = null
    const clearHover = () => {
      if (hovered === null && !popup.isOpen()) return
      hovered = null
      canvas.style.cursor = ''
      popup.remove()
      if (loadedRef.current) map.setFilter('hover-outline', ['==', ['get', 'iso'], '__none__'])
    }
    map.on('mousemove', (e) => {
      const iso = countryAt(e.point)
      const c = iso ? countriesRef.current.find((x) => x.iso === iso) : undefined
      if (!c) return clearHover()
      note() // hovering a country pauses auto-rotation
      if (hovered !== c.iso) {
        hovered = c.iso
        map.setFilter('hover-outline', ['==', ['get', 'iso'], c.iso])
      }
      canvas.style.cursor = 'pointer'
      const line =
        modeRef.current === 'health' && c.status === 'ok'
          ? ''
          : modeRef.current === 'health'
          ? notesRef.current[c.iso]
            ? `<span style="color:${AURORA.fg2}">● ${notesRef.current[c.iso].startsWith(c.name) ? notesRef.current[c.iso].slice(c.name.length).trim() : notesRef.current[c.iso]}</span>`
            : `<span style="color:${STATUS_COLOR[c.status]}">● ${STATUS_LABEL[c.status]}</span>`
          : `<div style="max-width:260px;white-space:normal">${(growthRef.current[c.iso] ?? `Showing more of what users like: about +${usd(c.potential_usd_day)} a day.`).replace(/^[^:]{1,40}:\s*/, '')}</div>`
      popup.setLngLat(e.lngLat).setHTML(`<div class="font-semibold">${c.name}</div>${line ? `<div>${line}</div>` : ''}`).addTo(map)
    })
    // The globe moves under a still pointer (auto-rotation, fly-to), so a stale hover is cleared on movement too.
    map.on('movestart', clearHover)
    map.on('move', paintPoles)
    canvas.addEventListener('mouseleave', clearHover)
    map.on('click', (e) => {
      const iso = countryAt(e.point)
      if (iso) onSelectRef.current(iso)
    })

    return () => {
      cancelAnimationFrame(frame)
      canvas.removeEventListener('pointerdown', note)
      canvas.removeEventListener('wheel', note)
      canvas.removeEventListener('pointermove', note)
      canvas.removeEventListener('mouseleave', clearHover)
      map.remove()
      mapRef.current = null
      loadedRef.current = false
    }
  }, [])

  function paint() {
    const map = mapRef.current
    if (!alive(map) || !loadedRef.current) return
    map.setPaintProperty('watched', 'fill-color', matchExpr(fillColors(countriesRef.current, modeRef.current), BASE_LAND))
    map.setPaintProperty('watched', 'fill-opacity', launchRef.current ? 0.15 : ((modeRef.current === 'health' ? opacityExpr(countriesRef.current, 0.5, attentionRef.current) : growthOpacity(countriesRef.current)) as any))
    const problems = modeRef.current === 'health' && !launchRef.current ? countriesRef.current.filter((c) => c.status === 'incident') : []
    map.setFilter('problem-glow', problems.length ? ['in', ['get', 'iso'], ['literal', problems.map((c) => c.iso)]] : ['==', ['get', 'iso'], '__none__'])
    const att = problems.length ? Math.max(...problems.map((c) => attentionRef.current[c.iso] ?? 0.5)) : 0
    map.setPaintProperty('problem-glow', 'line-opacity', 0.35 + 0.4 * att)
    syncMarkers()
  }

  function syncMarkers() {
    const map = mapRef.current
    if (!map) return
    const want = new Map(
      countriesRef.current
        .filter((c) => modeRef.current === 'health' && MARKER_STATUSES.includes(c.status))
        .map((c) => [c.iso, c]),
    )
    for (const [iso, m] of markersRef.current) {
      const c = want.get(iso)
      if (!c || c.status !== m.status) {
        m.marker.remove()
        markersRef.current.delete(iso)
      }
    }
    for (const [iso, c] of want) {
      if (markersRef.current.has(iso)) continue
      const el = document.createElement('div')
      el.className = `argus-marker argus-marker--${c.status}`
      el.innerHTML = '<span></span><span></span><i></i>'
      el.addEventListener('click', (ev) => {
        ev.stopPropagation()
        onSelectRef.current(iso)
      })
      const marker = new maplibregl.Marker({ element: el, opacityWhenCovered: '0' } as any).setLngLat([c.lng, c.lat]).addTo(map)
      markersRef.current.set(iso, { marker, status: c.status })
    }
  }

  useEffect(paint, [countries, mode, JSON.stringify(attention)])

  // Day/night: recompute the night overlay when the simulated hour changes.
  useEffect(() => {
    if (utcHour == null || utcHour === hourRef.current) return
    hourRef.current = utcHour
    const map = mapRef.current
    const earth = earthRef.current
    if (!alive(map)) return
    if (earth && map.getSource('earth')) {
      const hour = utcHour
      earth.render(hour).then(({ url, poles }) => {
        if (hour !== hourRef.current || !alive(map)) return
        ;(map.getSource('earth') as ImageSource | undefined)?.updateImage({ url, coordinates: WORLD_COORDS })
        polesRef.current = poles
        paintPoles()
      })
    } else {
      const src = map?.getSource('night') as ImageSource | undefined
      src?.updateImage({ url: nightImage(utcHour), coordinates: WORLD_COORDS })
    }
  }, [utcHour])

  // Fly to the selected country, or back to the whole globe, keeping clear of the right-hand card.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const run = () => {
      if (!alive(map)) return
      map.setFilter('selected-outline', ['==', ['get', 'iso'], selectedIso ?? '__none__'])
      if (launchRef.current) return
      const padding = { top: 0, bottom: 0, left: 0, right: insetRight }
      const c = countriesRef.current.find((x) => x.iso === (selectedIso ?? lookAt))
      if (c)
        map.flyTo({
          center: centersRef.current.get(c.iso) ?? [c.lng, c.lat],
          zoom: flyZoom((centersRef.current.get(c.iso) ?? [c.lng, c.lat])[1]),
          pitch: 0,
          bearing: 0,
          padding,
          duration: 1800,
          curve: 1.3,
          easing: easeOutCubic,
          essential: true,
        })
      else map.flyTo({ center: [map.getCenter().lng, HOME_VIEW.center[1]], zoom: HOME_VIEW.zoom, padding, duration: 1600, easing: easeOutCubic, essential: true })
    }
    if (loadedRef.current) run()
    else map.once('load', run)
  }, [selectedIso, lookAt, insetRight])

  // What-if launch: fly to the country (dark), then reveal its colour with the learning arcs; closing flies home.
  const launchKey = launch ? `${launch.name}|${launch.revealed}` : ''
  const prevLaunch = useRef('')
  useEffect(() => {
    const map = mapRef.current
    const was = prevLaunch.current
    prevLaunch.current = launchKey
    if (!map || (!launchKey && !was)) return
    let raf = 0
    const run = () => {
      if (!alive(map)) return
      const filter: any = launch ? (launch.iso ? ['any', ['==', ['get', 'name'], launch.name], ['==', ['get', 'iso'], launch.iso]] : ['==', ['get', 'name'], launch.name]) : ['==', ['get', 'name'], '__none__']
      map.setFilter('launch-fill', filter)
      map.setFilter('launch-outline', filter)
      if (!alive(map)) return
      const arcs = map.getSource('launch-arcs') as GeoJSONSource
      // Reduced motion: the camera jumps instead of flying; the country only fades.
      const launchCenter = (): [number, number] =>
        launch ? (centersRef.current.get(launch.iso ?? '') ?? centersRef.current.get(launch.name) ?? [launch.lng, launch.lat]) : [0, 0]
      const move = (o: maplibregl.FlyToOptions) => (reducedMotion() ? map.jumpTo({ center: o.center, zoom: o.zoom, pitch: o.pitch, bearing: o.bearing, padding: o.padding as maplibregl.PaddingOptions }) : map.flyTo(o))
      if (!launch) {
        map.setPaintProperty('launch-fill', 'fill-opacity', 0)
        arcs.setData({ type: 'FeatureCollection', features: [] })
        map.setPaintProperty('watched', 'fill-opacity', (modeRef.current === 'health' ? opacityExpr(countriesRef.current, 0.5, attentionRef.current) : growthOpacity(countriesRef.current)) as any)
        move({ center: [map.getCenter().lng, HOME_VIEW.center[1]], zoom: HOME_VIEW.zoom, padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: 1600, easing: easeOutCubic, essential: true })
        return
      }
      if (!launch.revealed) {
        // Unwatched: dark, outlined, everything else dimmed; fly there.
        map.setPaintProperty('launch-fill', 'fill-color', AURORA.bg)
        map.setPaintProperty('launch-fill', 'fill-opacity', 0.75)
        map.setPaintProperty('launch-outline', 'line-opacity', 0.6)
        map.setPaintProperty('watched', 'fill-opacity', 0.15)
        move({ center: launchCenter(), zoom: flyZoom(launchCenter()[1]), pitch: 0, bearing: 0, padding: { top: 0, bottom: 0, left: 0, right: insetRight }, duration: 1800, curve: 1.3, easing: easeOutCubic, essential: true })
        return
      }
      map.setPaintProperty('launch-fill', 'fill-color', launch.color)
      map.setPaintProperty('launch-fill', 'fill-opacity', 0.85)
      map.setPaintProperty('launch-outline', 'line-opacity', 0)
      move({ center: launchCenter(), zoom: flyZoom(launchCenter()[1]), pitch: 0, bearing: 0, padding: { top: 0, bottom: 0, left: 0, right: insetRight }, duration: 900, easing: easeOutCubic, essential: true })
      if (reducedMotion() || !launch.sources.length) return
      const to: [number, number] = [launch.lng, launch.lat]
      arcs.setData({ type: 'FeatureCollection', features: launch.sources.map((s) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: arc(s, to) } })) })
      const t0 = performance.now()
      const step = (now: number) => {
        const t = (now - t0) / 1600
        if (!alive(map)) return
        if (t >= 1) return map.setPaintProperty('launch-arcs', 'line-gradient', shimmer(0, 0))
        const fade = Math.min(1, t / 0.12, (1 - t) / 0.3)
        map.setPaintProperty('launch-arcs', 'line-gradient', shimmer(-0.05 + t * 1.3, fade))
        raf = requestAnimationFrame(step)
      }
      raf = requestAnimationFrame(step)
    }
    if (loadedRef.current) run()
    else map.once('load', run)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [launchKey])

  // maplibre-gl.css forces position: relative on the container, so size it from a wrapper.
  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
}
