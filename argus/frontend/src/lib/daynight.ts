// Night-side shading for the globe, rendered as one smooth image (no grid seams). The image is laid out in
// Web-Mercator rows, because MapLibre maps image sources linearly in Mercator space.
const RAD = Math.PI / 180
export const MERCATOR_LAT = 85.0511287798
export const WORLD_COORDS: [[number, number], [number, number], [number, number], [number, number]] = [
  [-180, MERCATOR_LAT],
  [180, MERCATOR_LAT],
  [180, -MERCATOR_LAT],
  [-180, -MERCATOR_LAT],
]

// Sub-solar point for a UTC hour; declination fixed to late November (about −20°), when this demo runs.
export function subsolar(utcHour: number, declination = -20) {
  return { lng: (12 - utcHour) * 15, lat: declination }
}

// Returns a PNG data URL: transparent by day, a soft dark veil by night, twilight in between.
// Veil colour = the ocean colour, so the ocean (and the polar cap above ±85°, which MapLibre fills with it)
// looks the same by night and only land darkens.
export function nightImage(utcHour: number, rgb: [number, number, number] = [6, 6, 26], maxOpacity = 0.6, size = 512): string {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const img = ctx.createImageData(size, size)
  const sun = subsolar(utcHour)
  const sLat = Math.sin(sun.lat * RAD)
  const cLat = Math.cos(sun.lat * RAD)
  for (let j = 0; j < size; j++) {
    const lat = Math.atan(Math.sinh(Math.PI * (1 - (2 * (j + 0.5)) / size))) // Mercator row → latitude (radians)
    const sl = Math.sin(lat)
    const cl = Math.cos(lat)
    for (let i = 0; i < size; i++) {
      const lng = -180 + ((i + 0.5) / size) * 360
      const elev = Math.asin(Math.max(-1, Math.min(1, sl * sLat + cl * cLat * Math.cos((lng - sun.lng) * RAD)))) / RAD
      const a = Math.max(0, Math.min(1, (2 - elev) / 14)) * maxOpacity // dark below −12°, clear above +2°
      const k = (j * size + i) * 4
      img.data[k] = rgb[0]
      img.data[k + 1] = rgb[1]
      img.data[k + 2] = rgb[2]
      img.data[k + 3] = Math.round(a * 255)
    }
  }
  ctx.putImageData(img, 0, 0)
  return canvas.toDataURL('image/png')
}
