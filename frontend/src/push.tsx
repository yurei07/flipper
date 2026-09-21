import { useEffect, useState } from 'react'
import { api, getKey } from './lib/api'

/* Переключатель push-уведомлений: подписка Web Push (SET) + флаг send_maybe. */
export function PushGate() {
  const [on, setOn] = useState(false)
  const [maybe, setMaybe] = useState(false)
  const [state, setState] = useState<NotificationPermission | ''>('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return
    navigator.serviceWorker.ready.then(async () => {
      const sub = await navigator.serviceWorker.getRegistration().then((r) => r?.pushManager.getSubscription())
      setOn(Boolean(sub))
      setMaybe(localStorage.getItem('flipper_maybe') === '1')
    })
  }, [])

  const toggle = async () => {
    setBusy(true)
    try {
      const reg = await navigator.serviceWorker.ready
      if (on) {
        const sub = await reg.pushManager.getSubscription()
        if (sub) {
          try { await api.pushUnsubscribe(sub.endpoint) } catch {}
          await sub.unsubscribe()
        }
        setOn(false)
      } else {
        let perm: NotificationPermission = state as NotificationPermission
        if (perm !== 'granted') {
          perm = await Notification.requestPermission()
          setState(perm)
        }
        if (perm !== 'granted') return
        const { public_key } = await api.pushKeys()
        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(public_key),
        })
        const j = sub.toJSON()
        await api.pushSubscribe(sub.endpoint, (j as any).keys!.p256dh, (j as any).keys!.auth, maybe)
        setOn(true)
      }
    } finally {
      setBusy(false)
    }
  }

  const toggleMaybe = async () => {
    const v = !maybe
    setMaybe(v)
    localStorage.setItem('flipper_maybe', v ? '1' : '0')
    try {
      const sub = await navigator.serviceWorker.ready.then((r) => r.pushManager.getSubscription())
      if (sub) {
        const j = sub.toJSON()
        await api.pushSubscribe(sub.endpoint, (j as any).keys!.p256dh, (j as any).keys!.auth, v)
      }
    } catch {}
  }

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return null

  return (
    <div className="push">
      <label className="switch">
        <input type="checkbox" checked={on} onChange={toggle} disabled={busy || !getKey()} />
        <span>push</span>
      </label>
      {on && (
        <label className="switch slim">
          <input type="checkbox" checked={maybe} onChange={toggleMaybe} />
          <span>и MAYBE</span>
        </label>
      )}
    </div>
  )
}

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(b64)
  const buf = new ArrayBuffer(raw.length)
  const out = new Uint8Array(buf)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}