import { Capacitor } from '@capacitor/core'
import { PushNotifications } from '@capacitor/push-notifications'
import { api } from './lib/api'

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