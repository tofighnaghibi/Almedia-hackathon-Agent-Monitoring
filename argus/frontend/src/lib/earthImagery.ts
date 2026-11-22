// NASA Blue Marble (day) and Black Marble / Earth at Night (city lights), public domain, NASA Visible Earth.
// Both are pre-graded to the Aurora palette and reprojected to Web Mercator (2048²) in frontend/public/earth.
// Each simulated hour the two are blended per pixel by the sun's elevation into one texture for MapLibre.
import { subsolar } from './daynight'

const RAD = Math.PI / 180
const SIZE = 2048

type Pixels = Uint8ClampedArray

async function loadPixels(url: string): Promise<Pixels> {
  const img = new Image()
  img.decoding = 'async'
  img.src = url
  await img.decode()
  const c = document.createElement('canvas')
  c.width = SIZE
  c.height = SIZE
  const ctx = c.getContext('2d', { willReadFrequently: true })!
  ctx.drawImage(img, 0, 0, SIZE, SIZE)
  return ctx.getImageData(0, 0, SIZE, SIZE).data
}

export class EarthImagery {
  private day: Pixels
  private night: Pixels
  private canvas = document.createElement('canvas')
  private sinLat = new Float32Array(SIZE)
  private cosLat = new Float32Array(SIZE)
  private lng = new Float32Array(SIZE)
  private lastUrl: string | null = null

  private constructor(day: Pixels, night: Pixels) {
    this.day = day
    this.night = night
    this.canvas.width = SIZE
    this.canvas.height = SIZE
    for (let j = 0; j < SIZE; j++) {
      const lat = Math.atan(Math.sinh(Math.PI * (1 - (2 * (j + 0.5)) / SIZE)))
      this.sinLat[j] = Math.sin(lat)
      this.cosLat[j] = Math.cos(lat)
    }
    for (let i = 0; i < SIZE; i++) this.lng[i] = -180 + ((i + 0.5) / SIZE) * 360
  }

  static async load(base = '/earth'): Promise<EarthImagery> {
    const [day, night] = await Promise.all([loadPixels(`${base}/day.jpg`), loadPixels(`${base}/night.jpg`)])
    return new EarthImagery(day, night)
  }

  // Blend for one UTC hour; resolves to an object URL of the texture (previous one is revoked) and the average colour
  // of its top and bottom rows. MapLibre paints both polar caps (beyond ±85°) with the background colour, so the
  // caller sets it to the pole facing the camera (otherwise a dark disc shows over the Arctic or Antarctica).
  async render(utcHour: number): Promise<{ url: string; poles: { north: [number, number, number]; south: [number, number, number] } }> {
    const sun = subsolar(utcHour)
    const sS = Math.sin(sun.lat * RAD)
    const cS = Math.cos(sun.lat * RAD)
    const cosDl = new Float32Array(SIZE)
    for (let i = 0; i < SIZE; i++) cosDl[i] = Math.cos((this.lng[i] - sun.lng) * RAD)
    const ctx = this.canvas.getContext('2d')!
    const out = ctx.createImageData(SIZE, SIZE)
    const o = out.data
    const d = this.day
    const n = this.night
    for (let j = 0; j < SIZE; j++) {
      const a = this.sinLat[j] * sS
      const b = this.cosLat[j] * cS
      let k = j * SIZE * 4
      for (let i = 0; i < SIZE; i++, k += 4) {
        // sin(elevation) → night weight: day above +2°, night below −12°, smooth twilight between.
        const se = a + b * cosDl[i]
        let w = (0.035 - se) / 0.24 // ≈ (2° − elev) / 14° in sine space
        w = w < 0 ? 0 : w > 1 ? 1 : w * w * (3 - 2 * w)
        const v = 1 - w
        o[k] = d[k] * v + n[k] * w
        o[k + 1] = d[k + 1] * v + n[k + 1] * w
        o[k + 2] = d[k + 2] * v + n[k + 2] * w
        o[k + 3] = 255
      }
    }
    const rowsMean = (from: number): [number, number, number] => {
      let r = 0, g = 0, b = 0
      for (let k = from * SIZE * 4; k < (from + 4) * SIZE * 4; k += 4) (r += o[k]), (g += o[k + 1]), (b += o[k + 2])
      const n = SIZE * 4
      return [r / n, g / n, b / n]
    }
    const poles = { north: rowsMean(0), south: rowsMean(SIZE - 4) }
    ctx.putImageData(out, 0, 0)
    const blob: Blob = await new Promise((res) => this.canvas.toBlob((b) => res(b!), 'image/jpeg', 0.9))
    if (this.lastUrl) URL.revokeObjectURL(this.lastUrl)
    this.lastUrl = URL.createObjectURL(blob)
    return { url: this.lastUrl, poles }
  }
}

// Faint graticule: meridians and parallels every 30°.
export function graticule(): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  const features: GeoJSON.Feature<GeoJSON.LineString>[] = []
  for (let lng = -180; lng < 180; lng += 30) {
    const coords: [number, number][] = []
    for (let lat = -80; lat <= 80; lat += 2) coords.push([lng, lat])
    features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } })
  }
  for (const lat of [-60, -30, 0, 30, 60]) {
    const coords: [number, number][] = []
    for (let lng = -180; lng <= 180; lng += 2) coords.push([lng, lat])
    features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } })
  }
  return { type: 'FeatureCollection', features }
}
