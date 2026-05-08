import { useState } from 'react'
import { api } from '../api.js'

export default function Profile({ user, onSaved }) {
  const [form, setForm] = useState({
    name: user.name,
    pilot_level: user.pilot_level,
    license_type: user.license_type,
    wing_class: user.wing_class,
    flight_hours: user.flight_hours,
    region: user.region,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [okMsg, setOkMsg] = useState('')

  function set(k, v) {
    setForm((f) => ({ ...f, [k]: v }))
  }

  async function save(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setOkMsg('')
    try {
      const updated = await api.updateMe({
        ...form,
        flight_hours: parseInt(form.flight_hours, 10) || 0,
      })
      onSaved(updated)
      setOkMsg('Profil gespeichert.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card">
      <h2>Pilotenprofil</h2>
      <p className="muted">Diese Angaben helfen, Coaching-Hinweise besser zu personalisieren.</p>
      {error && <div className="error">{error}</div>}
      {okMsg && <div className="error" style={{ background: 'rgba(74,222,128,0.1)', color: '#86efac', borderColor: 'rgba(74,222,128,0.4)' }}>{okMsg}</div>}
      <form onSubmit={save}>
        <div className="grid grid-3">
          <div className="field">
            <label>Name</label>
            <input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} />
          </div>
          <div className="field">
            <label>Pilot-Level</label>
            <select className="select" value={form.pilot_level} onChange={(e) => set('pilot_level', e.target.value)}>
              <option value="beginner">Flugschüler</option>
              <option value="advanced">Fortgeschritten</option>
              <option value="xc">XC-Pilot</option>
              <option value="instructor">Fluglehrer</option>
            </select>
          </div>
          <div className="field">
            <label>Schein</label>
            <input className="input" value={form.license_type} onChange={(e) => set('license_type', e.target.value)} placeholder="A-Schein, B-Schein…" />
          </div>
          <div className="field">
            <label>Schirmklasse</label>
            <select className="select" value={form.wing_class} onChange={(e) => set('wing_class', e.target.value)}>
              <option value="">—</option>
              <option value="EN-A">EN-A</option>
              <option value="EN-B">EN-B</option>
              <option value="EN-C">EN-C</option>
              <option value="EN-D">EN-D</option>
              <option value="CCC">CCC</option>
            </select>
          </div>
          <div className="field">
            <label>Flugstunden</label>
            <input className="input" type="number" min="0" value={form.flight_hours} onChange={(e) => set('flight_hours', e.target.value)} />
          </div>
          <div className="field">
            <label>Region</label>
            <input className="input" value={form.region} onChange={(e) => set('region', e.target.value)} placeholder="Allgäu, Stubai…" />
          </div>
        </div>
        <button className="btn btn-primary" disabled={saving}>
          {saving ? 'Speichere…' : 'Speichern'}
        </button>
      </form>
    </div>
  )
}
