import { useEffect, useState } from 'react'
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { getKey, setKey } from './lib/api'
import { DealPage } from './pages/Deal'
import { Feed } from './pages/Feed'
import { PushGate } from './push'
import { Settings } from './pages/Settings'

export default function App() {
  const [key, setK] = useState(getKey())
  const navigate = useNavigate()
  const loc = useLocation()

  useEffect(() => {
    if (key) navigate('/')
  }, [])

  if (!key) {
    return (
      <div className="login-wrap">
        <form
          className="login"
          onSubmit={(e) => {
            e.preventDefault()
            const v = new FormData(e.currentTarget).get('key') as string
            if (v.trim()) {
              setKey(v.trim())
              setK(v.trim())
              navigate('/')
            }
          }}
        >
          <h1>flipper</h1>
          <p>Введите api-ключ (из .env на сервере)</p>
          <input name="key" type="password" autoFocus placeholder="FLIPPER_API_KEY" />
          <button>Войти</button>
        </form>
      </div>
    )
  }

  return (
    <div className="app">
      <header>
        <Link to="/" className="brand" onClick={() => window.dispatchEvent(new Event('flipper-refresh'))}>
          flipper
        </Link>
        <span className="push">
          <Link className="settings-link" to="/settings">настройки</Link>
          <PushGate />
        </span>
      </header>
      <Routes location={loc}>
        <Route path="/" element={<Feed />} />
        <Route path="/deal/:id" element={<DealPage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Feed />} />
      </Routes>
    </div>
  )
}