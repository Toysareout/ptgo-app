import { useEffect, useState } from 'react'
import { api, auth } from './api.js'
import Auth from './components/Auth.jsx'
import Uploader from './components/Uploader.jsx'
import AnalysisView from './components/AnalysisView.jsx'
import FlightLog from './components/FlightLog.jsx'
import Profile from './components/Profile.jsx'

export default function App() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState('flights') // 'flights' | 'profile'
  const [analysis, setAnalysis] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    const token = auth.get()
    if (!token) {
      setLoading(false)
      return
    }
    api.me()
      .then(setUser)
      .catch(() => auth.clear())
      .finally(() => setLoading(false))
  }, [])

  function logout() {
    auth.clear()
    setUser(null)
    setAnalysis(null)
  }

  async function openFlight(id) {
    try {
      const a = await api.getFlight(id)
      setAnalysis(a)
    } catch (err) {
      alert(err.message)
    }
  }

  function onAnalyzed(a) {
    setAnalysis(a)
    setRefreshKey((k) => k + 1)
  }

  if (loading) {
    return (
      <div className="shell">
        <p className="muted">Lade…</p>
      </div>
    )
  }

  if (!user) return <Auth onAuth={setUser} />

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">
          <span className="brand-logo">↑</span>
          <span>SkyCoach AI</span>
        </div>
        <div className="nav-actions">
          <button
            className={`btn btn-ghost ${view === 'flights' && !analysis ? 'active' : ''}`}
            onClick={() => {
              setView('flights')
              setAnalysis(null)
            }}
          >
            Flüge
          </button>
          <button
            className={`btn btn-ghost ${view === 'profile' && !analysis ? 'active' : ''}`}
            onClick={() => {
              setView('profile')
              setAnalysis(null)
            }}
          >
            Profil
          </button>
          <span className="muted" style={{ fontSize: 13, marginLeft: 8 }}>
            {user.email}
          </span>
          <button className="btn" onClick={logout}>Abmelden</button>
        </div>
      </nav>

      {analysis ? (
        <AnalysisView analysis={analysis} onBack={() => setAnalysis(null)} />
      ) : view === 'profile' ? (
        <Profile user={user} onSaved={setUser} />
      ) : (
        <>
          <Uploader onAnalyzed={onAnalyzed} />
          <FlightLog refreshKey={refreshKey} onOpen={openFlight} />
          <div className="legal">
            ⚠ Trainings- und Analysewerkzeug. Kein zertifiziertes Fluginstrument.
          </div>
        </>
      )}
    </div>
  )
}
