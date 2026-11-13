import { Capacitor } from '@capacitor/core'
import { PushNotifications } from '@capacitor/push-notifications'
import { LocalNotifications } from '@capacitor/local-notifications'
import { api } from './lib/api'

export type NativePush = { title: string; body?: string; adId?: string }
type PushListener = (p: NativePush) => void

const listeners: PushListener[] = []
export function onNativePush(cb: PushListener): () => void {
  listeners.push(cb)
  return () => {
    const i = listeners.indexOf(cb)
    if (i >= 0) listeners.splice(i, 1)
  }
}

let started = false

export async function initNativePush(): Promise<void> {
  if (!Capacitor.isNativePlatform() || started) return
  started = true
  try {
    const perm = await PushNotifications.requestPermissions()
    if (perm.receive !== 'granted') return
  } catch {
    return
  }
  PushNotifications.addListener('registration', ({ value }) => {
    api.fcmRegister(value, 'android').catch(() => {})
  })
  PushNotifications.addListener('registrationError', (e) => {
    console.warn('FCM registration error:', e.error)
  })
  // Foreground: Android не показывает пуш системой — рисуем баннер в интерфейсе
  // И страхуемся системным уведомлением через LocalNotifications в любом случае.
  PushNotifications.addListener('pushNotificationReceived', async (e) => {
    const d = ((e.data || {}) as Record<string, unknown>) || {}
    const title = String(d.title ?? d['gcm.notification.title'] ?? 'flipper')
    const body = String(d.body ?? d['gcm.notification.body'] ?? '')
    const adId = String(d.ad_id ?? '') || undefined
    scheduleLocal(title, body, adId)
    listeners.forEach((cb) => cb({ title: title || 'flipper', body, adId }))
  })
  PushNotifications.addListener('pushNotificationActionPerformed', (e) => {
    const adId = e.notification.data?.ad_id
    if (adId) window.location.href = '/deal/' + adId
  })
  try {
    await PushNotifications.register()
  } catch (e) {
    console.warn('FCM register failed:', e)
  }
}

let _localId = Math.floor(Date.now() / 1000) % 1_000_000

async function scheduleLocal(title: string, body: string | undefined, adId?: string) {
  try {
    const lp = await LocalNotifications.requestPermissions()
    if (lp.display !== 'granted') return
    await LocalNotifications.schedule({
      notifications: [
        {
          id: ++_localId,
          title: title || 'flipper',
          body: body ?? '',
          smallIcon: 'ic_launcher',
          channelId: 'deals',
          extra: { ad_id: adId },
        },
      ],
    })
  } catch (e) {
    console.warn('local notify failed:', e)
  }
}