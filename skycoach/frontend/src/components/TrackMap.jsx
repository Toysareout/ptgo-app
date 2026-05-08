/**
 * Lightweight SVG track preview. Avoids pulling in a mapping library for the
 * MVP — projects lat/lon to a normalised viewport. We can swap this for
 * Leaflet/MapLibre in v2.
 */
export default function TrackMap({ points }) {
  if (!points || points.length < 2) {
    return <p className="muted">Keine Trackdaten verfügbar.</p>
  }

  const lats = points.map((p) => p[0])
  const lons = points.map((p) => p[1])
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  const minLon = Math.min(...lons)
  const maxLon = Math.max(...lons)

  const W = 800
  const H = 360
  const pad = 20

  // Equirectangular projection scaled so 1° lat equals 1° lon at the
  // flight's latitude — keeps visual proportions roughly correct.
  const latRange = Math.max(maxLat - minLat, 1e-6)
  const lonRange = Math.max(maxLon - minLon, 1e-6)
  const lonScale = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180))
  const lonRangeScaled = lonRange * lonScale

  const aspect = lonRangeScaled / latRange
  const targetAspect = (W - 2 * pad) / (H - 2 * pad)
  let scaleX, scaleY
  if (aspect > targetAspect) {
    scaleX = (W - 2 * pad) / lonRangeScaled
    scaleY = scaleX
  } else {
    scaleY = (H - 2 * pad) / latRange
    scaleX = scaleY
  }

  function project([lat, lon]) {
    const x = pad + (lon - minLon) * lonScale * scaleX
    const y = H - pad - (lat - minLat) * scaleY
    return [x, y]
  }

  const d = points
    .map((p, i) => {
      const [x, y] = project(p)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const [sx, sy] = project(points[0])
  const [ex, ey] = project(points[points.length - 1])

  return (
    <svg className="track-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <path d={d} fill="none" stroke="#4ea3ff" strokeWidth="2" strokeLinejoin="round" />
      <circle cx={sx} cy={sy} r="6" fill="#4ade80" />
      <circle cx={ex} cy={ey} r="6" fill="#f87171" />
      <text x={sx + 10} y={sy + 4} fill="#4ade80" fontSize="12">Start</text>
      <text x={ex + 10} y={ey + 4} fill="#f87171" fontSize="12">Landung</text>
    </svg>
  )
}
