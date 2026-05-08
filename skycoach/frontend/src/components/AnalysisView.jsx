import TrackMap from './TrackMap.jsx'

function fmtDuration(s) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  return `${m}m ${String(ss).padStart(2, '0')}s`
}

function Metric({ label, value, unit }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
    </div>
  )
}

export default function AnalysisView({ analysis, onBack }) {
  const m = analysis.metrics
  return (
    <div>
      <div className="row between" style={{ marginBottom: 16 }}>
        <button className="btn btn-ghost" onClick={onBack}>← Zurück</button>
        <span className={`risk-badge risk-${analysis.risk_level}`}>
          Risiko-Score: {analysis.risk_score}/100 · {analysis.risk_level}
        </span>
      </div>

      <div className="card">
        <h2 style={{ marginBottom: 4 }}>{analysis.flight_date}</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Pilot: {analysis.pilot} · Schirm: {analysis.glider}
        </p>

        <h3>Flugmetriken</h3>
        <div className="grid grid-4">
          <Metric label="Dauer" value={fmtDuration(m.duration_s)} />
          <Metric label="Strecke" value={m.track_distance_km} unit="km" />
          <Metric label="Luftlinie" value={m.straight_distance_km} unit="km" />
          <Metric label="Max. Höhe" value={m.max_alt_m} unit="m" />
          <Metric label="Max. Steigen" value={m.max_climb_ms} unit="m/s" />
          <Metric label="Max. Sinken" value={m.max_sink_ms} unit="m/s" />
          <Metric label="Ø Speed" value={m.avg_ground_speed_kmh} unit="km/h" />
          <Metric label="Max. Speed" value={m.max_ground_speed_kmh} unit="km/h" />
        </div>
      </div>

      <div className="card">
        <h3>Flugspur</h3>
        <TrackMap points={analysis.track_preview} />
      </div>

      <div className="card">
        <h3>Thermiken ({m.thermals.length})</h3>
        {m.thermals.length === 0 ? (
          <p className="muted">Keine nachhaltigen Thermikphasen erkannt.</p>
        ) : (
          <>
            <div className="grid grid-3" style={{ marginBottom: 16 }}>
              <Metric label="Ø Steigen" value={m.avg_thermal_climb_ms} unit="m/s" />
              <Metric label="Bestes Steigen" value={m.best_thermal_climb_ms} unit="m/s" />
              <Metric label="Höhengewinn ges." value={m.altitude_gain_m} unit="m" />
            </div>
            <table style={{ width: '100%', fontSize: 14, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-dim)' }}>
                  <th style={{ padding: '8px 4px' }}>#</th>
                  <th>Dauer</th>
                  <th>Höhengewinn</th>
                  <th>Ø Steigen</th>
                  <th>Spitzensteigen</th>
                </tr>
              </thead>
              <tbody>
                {m.thermals.map((t, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 4px' }}>{i + 1}</td>
                    <td>{fmtDuration(t.duration_s)}</td>
                    <td>{t.gain_m} m</td>
                    <td>{t.avg_climb_ms.toFixed(1)} m/s</td>
                    <td>{t.peak_climb_ms.toFixed(1)} m/s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="card">
        <h3>KI-Coaching</h3>
        {analysis.coaching.map((h, i) => (
          <div key={i} className={`hint hint-${h.severity}`}>
            <div className="title">{h.title}</div>
            <div className="detail">{h.detail}</div>
          </div>
        ))}
      </div>

      <div className="legal">
        ⚠ Trainings- und Analysewerkzeug. Kein zertifiziertes Fluginstrument.
        Diese App ersetzt keine Flugausbildung und keine fachliche Beurteilung durch deinen Fluglehrer.
      </div>
    </div>
  )
}
