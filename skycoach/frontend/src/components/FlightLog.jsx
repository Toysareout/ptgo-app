import { useEffect, useState } from 'react'
import { api } from '../api.js'

function fmtDuration(s) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

export default function FlightLog({ refreshKey, onOpen }) {
  const [flights, setFlights] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      setFlights(await api.listFlights())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [refreshKey])

  async function remove(id, e) {
    e.stopPropagation()
    if (!confirm('Diesen Flug wirklich löschen?')) return
    try {
      await api.deleteFlight(id)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="card">
      <div className="row between" style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Flugtagebuch</h2>
        <span className="muted">{flights.length} Flüge</span>
      </div>
      {error && <div className="error">{error}</div>}
      {loading ? (
        <p className="muted">Lade Flüge…</p>
      ) : flights.length === 0 ? (
        <p className="muted">Noch keine Flüge gespeichert. Lade oben deine erste IGC-Datei hoch.</p>
      ) : (
        flights.map((f) => (
          <div key={f.id} className="flight-row" onClick={() => onOpen(f.id)}>
            <div className="col-meta">
              <strong>{f.flight_date}</strong>
              <small>{f.filename} · {f.glider || '—'}</small>
            </div>
            <div className="muted" style={{ fontSize: 13 }}>{fmtDuration(f.duration_s)}</div>
            <div className="muted" style={{ fontSize: 13 }}>{f.track_distance_km} km</div>
            <span className={`risk-badge risk-${f.risk_level}`}>{f.risk_score}</span>
            <button className="btn btn-danger" onClick={(e) => remove(f.id, e)}>Löschen</button>
          </div>
        ))
      )}
    </div>
  )
}
