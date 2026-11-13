import { useEffect, useState } from 'react'
import { Link, Route, Routes, useLocation, NavLink } from 'react-router-dom'
import { api, clearAuth, getKey, getServer, setKey, setServer } from './lib/api'
import { DealPage } from './pages/Deal'
import { Feed } from './pages/Feed'
import { Profile } from './pages/Profile'
import { Settings } from './pages/Settings'
import { PushGate } from './push'
import { initNativePush, onNativePush, type NativePush } from './push-native'

export default function App() {
  const hostOk = typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost'
  const [authed, setAuthed] = useState(Boolean(getKey()))
  const [serverInp, setServerInp] = useState(getServer() || (hostOk ? window.location.origin : ''))
  const [keyInp, setKeyInp] = useState(getKey())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [nativePush, setNativePush] = useState<NativePush | null>(null)
  const loc = useLocation()

  useEffect(() => {
    if (authed) {
      initNativePush()
      return onNativePush(setNativePush)
    }
  }, [authed])

  useEffect(() => {
    if (!nativePush) return
    const t = setTimeout(() => setNativePush(null), 7000)
    return () => clearTimeout(t)
  }, [nativePush])

  if (!authed) {
    return (
      <div className="login-wrap">
        <form
          className="login"
          onSubmit={async (e) => {
            e.preventDefault()
            setBusy(true)
            setErr('')
            setServer(serverInp)
            setKey(keyInp.trim())
            try {
              await api.stats()
              setAuthed(true)
            } catch (ex: any) {
              setErr(ex.message || 'не удалось подключиться к серверу')
            } finally {
              setBusy(false)
            }
          }}
        >
          <div className="login-brand">
            <span className="logo">f</span>
            <h1>flipper</h1>
          </div>
          <p>Умный сканер выгодных лотов с Kleinanzeigen и Vinted</p>
          <input
            placeholder="Сервер (https://…, пусто = этот адрес)"
            value={serverInp}
            onChange={(e) => setServerInp(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
          />
          <input name="key" type="password" placeholder="API-ключ" value={keyInp} onChange={(e) => setKeyInp(e.target.value)} autoFocus />
          {err && <div className="err">{err}</div>}
          <button disabled={busy || !keyInp.trim()}>{busy ? 'Проверяю…' : 'Войти'}</button>
          <div className="dim login-hint">Ключ из .env на твоём сервере (FLIPPER_API_KEY)</div>
        </form>
      </div>
    )
  }

  return (
    <div className="app">
      {nativePush && (
        <Link
          className="push-banner"
          to={nativePush.adId ? '/deal/' + nativePush.adId : '/'}
          onClick={() => setNativePush(null)}
        >
          <div className="push-banner-title">{nativePush.title}</div>
          {nativePush.body && <div className="push-banner-body">{nativePush.body}</div>}
        </Link>
      )}
      <header>
        <Link to="/" className="brand" onClick={() => window.dispatchEvent(new Event('flipper-refresh'))}>
          flipper
        </Link>
        <PushGate />
      </header>
      <div className="content">
        <Routes location={loc}>
          <Route path="/" element={<Feed savedOnly={false} />} />
          <Route path="/saved" element={<Feed savedOnly />} />
          <Route path="/deal/:id" element={<DealPage />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Feed savedOnly={false} />} />
        </Routes>
      </div>
      <nav className="tabbar">
        <NavLink to="/" end className={({ isActive }) => 'tab' + (isActive ? ' on' : '')}>
          <svg viewBox="0 0 24 24"><path d="M3 4h7v7H3V4zm11 0h7v7h-7V4zM3 13h7v7H3v-7zm11 0h7v7h-7v-7z" /></svg>
          <span>Лента</span>
        </NavLink>
        <NavLink to="/saved" className={({ isActive }) => 'tab' + (isActive ? ' on' : '')}>
          <svg viewBox="0 0 24 24"><path d="M12 21l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.18L12 21z" /></svg>
          <span>Избранное</span>
        </NavLink>
        <NavLink to="/profile" className={({ isActive }) => 'tab' + (isActive ? ' on' : '')}>
          <svg viewBox="0 0 24 24"><path d="M3 13h4v8H3v-8zm7-6h4v14h-4V7zm7-4h4v18h-4V3z" /></svg>
          <span>Профиль</span>
        </NavLink>
        <button className="tab logout" onClick={() => { clearAuth(); setAuthed(false) }}>
          <svg viewBox="0 0 24 24"><path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12 7 7 0 0 1 5 12c0-1.9.72-3.63 1.59-4.99L5.17 5.59A9 9 0 1 0 17.83 5.17zM14 3a9 9 0 0 0-4 2.05l1.46 1.46A7 7 0 0 1 14 5z" /></svg>
          <span>Выйти</span>
        </button>
      </nav>
    </div>
  )
}