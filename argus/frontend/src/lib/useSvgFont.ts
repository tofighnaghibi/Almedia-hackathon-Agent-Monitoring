import { useLayoutEffect, useRef, useState } from 'react'

// SVG text scales with its chart. This keeps chart text at least `min` px on screen (and never below the size asked
// for): it measures the drawn width of the <svg> and returns font sizes in viewBox units.
export function useSvgFont<T extends SVGSVGElement>(viewBoxWidth: number, min = 14) {
  const ref = useRef<T>(null)
  const [k, setK] = useState(1)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const w = el.getBoundingClientRect().width
      if (w > 0) setK(viewBoxWidth / w)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [viewBoxWidth])
  return { ref, px: (size: number) => Math.max(size, min) * Math.max(k, 0.0001) }
}
