# Argus frontend

Vite + React + TypeScript + Tailwind v4 + MapLibre GL (globe projection). Charts are hand-written SVG.

```bash
npm install
npm run dev     # http://localhost:5173, proxies /api -> http://localhost:8000
npm run build   # outputs dist/ (served by the backend at /)
```

When the backend is unreachable, `src/lib/api.ts` switches to the in-browser mock world in `src/mock/`
(same shapes as `docs/API.md`) and re-probes the backend every 10 s.

Layout follows Argus' own levels (progressive disclosure):
- Home: `Globe` (status or opportunity fill, day/night, pulsing problem marker) with `TopBar` (clock, one status
  sentence, Problems | Opportunities) and a quiet `TimelineStrip`.
- Issue card: `IssueCard` (plain sentences from `incident.card`, normal-zone chart, Approve).
- Evidence: `EvidenceView`, full screen (What broke · Who · Proof · Options · Details).
- `CountrySheet` on country click; `MetricGraphPanel` after adding a metric.
- Operator controls live in `ScenarioMenu` ("Try a scenario"); keys: 1–8 demo scenes, Space play/pause,
  E evidence, Esc close. Charts are hand-written SVG in `components/charts/`; colours come from the Aurora tokens in
  `index.css` / `lib/theme.ts`.

## Earth imagery

The globe's surface uses NASA's **Blue Marble** (day) and **Black Marble / Earth at Night** (city lights), public domain
from [NASA Visible Earth](https://visibleearth.nasa.gov/). The 4096×2048 originals (redistributed in the
`three-globe` npm package's example assets, integrity-checked against unpkg's SHA-256) were graded to the Aurora palette
(day desaturated and darkened, city lights recoloured violet-blue), reprojected to Web Mercator 2048×2048 and saved in
`public/earth/`. In the browser they are blended per pixel by the sun's elevation for the simulated hour.
Country shapes: Natural Earth 1:50m via `world-atlas` (public domain).
