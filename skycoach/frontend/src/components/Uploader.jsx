import { useRef, useState } from 'react'
import { api } from '../api.js'

export default function Uploader({ onAnalyzed }) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function handleFile(file) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.igc')) {
      setError('Bitte eine .igc-Datei auswählen.')
      return
    }
    setError('')
    setBusy(true)
    try {
      const result = await api.uploadFlight(file)
      onAnalyzed(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Neuer Flug</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Lade deine IGC-Datei hoch. Die Analyse läuft in wenigen Sekunden.
      </p>
      {error && <div className="error">{error}</div>}
      <div
        className={`dropzone ${drag ? 'active' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDrag(false)
          handleFile(e.dataTransfer.files[0])
        }}
      >
        {busy ? (
          <>
            <div className="spinner" />
            <p style={{ marginTop: 12 }}>Flug wird analysiert…</p>
          </>
        ) : (
          <>
            <div style={{ fontSize: 36, marginBottom: 8 }}>↑</div>
            <p style={{ margin: 0, fontWeight: 600 }}>IGC-Datei hier ablegen</p>
            <p className="muted" style={{ marginTop: 4 }}>oder klicken, um auszuwählen</p>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".igc"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>
    </div>
  )
}
