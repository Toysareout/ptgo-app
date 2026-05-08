import { useState } from 'react'
import { api, auth } from '../api.js'

export default function Auth({ onAuth }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { access_token } =
        mode === 'login'
          ? await api.login(email, password)
          : await api.register(email, password, name)
      auth.set(access_token)
      const me = await api.me()
      onAuth(me)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center-stack">
      <div className="card">
        <div className="brand" style={{ marginBottom: 24, justifyContent: 'center' }}>
          <span className="brand-logo">↑</span>
          <span>SkyCoach AI</span>
        </div>
        <div className="tabs">
          <button
            className={`tab ${mode === 'login' ? 'active' : ''}`}
            onClick={() => setMode('login')}
          >
            Anmelden
          </button>
          <button
            className={`tab ${mode === 'register' ? 'active' : ''}`}
            onClick={() => setMode('register')}
          >
            Registrieren
          </button>
        </div>
        <form onSubmit={submit}>
          {error && <div className="error">{error}</div>}
          {mode === 'register' && (
            <div className="field">
              <label>Name</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Max Mustermann"
              />
            </div>
          )}
          <div className="field">
            <label>E-Mail</label>
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label>Passwort {mode === 'register' && <span className="muted">(mind. 8 Zeichen)</span>}</label>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === 'register' ? 8 : undefined}
            />
          </div>
          <button className="btn btn-primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? 'Bitte warten…' : mode === 'login' ? 'Anmelden' : 'Konto erstellen'}
          </button>
        </form>
      </div>
      <p className="muted" style={{ textAlign: 'center', fontSize: 13 }}>
        Trainings- und Analysewerkzeug. Kein zertifiziertes Fluginstrument.
      </p>
    </div>
  )
}
