export interface FeedItem {
  ad_id: number
  title: string
  price_cents: number
  image_url: string | null
  verdict: 'TAKE' | 'MAYBE' | 'SKIP'
  margin_eur: number
  margin_pct: number
  confidence: number
  market_estimated: boolean
  location_city: string | null
  saved: boolean
  ignored: boolean
  source: 'kleinanzeigen' | 'vinted'
  created_at: string | null
}

export interface Deal {
  ad_id: number
  title: string
  source: 'kleinanzeigen' | 'vinted'
  price_cents: number
  description: string
  url: string
  location_city: string | null
  image_urls: string[]
  created_at: string | null
  saved: boolean
  ignored: boolean
  analysis: {
    flip_type: 'repair' | 'resale'
    verdict: 'TAKE' | 'MAYBE' | 'SKIP'
    margin_eur: number
    margin_pct: number
    parts_cost_eur: number
    market_working_eur: number | null
    market_estimated: boolean
    pros_ru: string[]
    cons_ru: string[]
    action_ru: string
    confidence: number
  } | null
}

const KEY = 'flipper_api_key'

export function getKey(): string {
  return localStorage.getItem(KEY) || ''
}
export function setKey(k: string) {
  localStorage.setItem(KEY, k)
}

async function req(path: string, init: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getKey()}`,
    ...((init.headers as Record<string, string>) || {}),
  }
  if (init.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) throw new ApiError('unauthorized')
  if (!res.ok) throw new ApiError(`${path}: ${res.status}`)
  return res.json()
}

export class ApiError extends Error {}

export interface SearchRow {
  id: number
  source: 'kleinanzeigen' | 'vinted'
  query: string
  filters: {
    max_price_eur?: number
    radius_km?: number
    location?: string
    sort_price?: string
    catalog_ids?: number[]
    brand_ids?: number[]
    status_ids?: number[]
    order?: string
  }
  active: boolean
}

export interface SearchDraft {
  query: string
  source: 'kleinanzeigen' | 'vinted'
  max_price_eur?: number
  radius_km?: number
  location?: string
  sort_price?: string
  catalog_ids?: number[]
  brand_ids?: number[]
  status_ids?: number[]
  order?: string
  active: boolean
}

export interface AppSettings {
  exclude_keywords: string[]
  exclude_title_keywords: string[]
  vinted_enabled: boolean
  vinted_default_catalog_ids: number[]
  vinted_domain: string
}

export interface FeedParams {
  verdict?: string
  min_margin?: number
  created_after_hours?: number
  limit?: number
  offset?: number
}

export const api = {
  feed: (params: FeedParams) => {
    const p = new URLSearchParams()
    if (params.verdict) p.set('verdict', params.verdict)
    if (params.min_margin) p.set('min_margin', String(params.min_margin))
    if (params.created_after_hours) p.set('created_after_hours', String(params.created_after_hours))
    if (params.limit) p.set('limit', String(params.limit))
    if (params.offset) p.set('offset', String(params.offset))
    return req(`/api/v1/listings?${p}`) as Promise<FeedItem[]>
  },
  deal: (id: number) => req(`/api/v1/listings/${id}`) as Promise<Deal>,
  act: (id: number, action: 'saved' | 'ignored' | 'dismissed') =>
    req(`/api/v1/listings/${id}/${action}`, { method: 'POST' }),
  reanalyze: (id: number) => req(`/api/v1/analyze/${id}`, { method: 'POST' }),
  stats: () => req('/api/v1/stats') as Promise<{ total_listings: number; verdicts: Record<string, number> }>,
  pushKeys: () => req('/api/v1/push/keys') as Promise<{ public_key: string }>,
  pushSubscribe: (endpoint: string, p256dh: string, auth: string, send_maybe: boolean) =>
    req('/api/v1/push/subscribe', {
      method: 'POST',
      body: JSON.stringify({ endpoint, p256dh, auth, send_maybe }),
    }),
  pushUnsubscribe: (endpoint: string) =>
    req(`/api/v1/push/unsubscribe?endpoint=${encodeURIComponent(endpoint)}`, { method: 'DELETE' }),
  searchList: () => req('/api/v1/searches') as Promise<SearchRow[]>,
  searchCreate: (d: SearchDraft) =>
    req('/api/v1/searches', { method: 'POST', body: JSON.stringify(d) }),
  searchUpdate: (id: number, d: SearchDraft) =>
    req(`/api/v1/searches/${id}`, { method: 'PUT', body: JSON.stringify(d) }),
  searchRemove: (id: number) => req(`/api/v1/searches/${id}`, { method: 'DELETE' }),
  settings: () => req('/api/v1/settings') as Promise<AppSettings>,
  setSettings: (patch: Partial<AppSettings>) =>
    req('/api/v1/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  vintedCatalogs: () =>
    req('/api/v1/vinted/catalogs') as Promise<{ id: string; title: string }[]>,
}

export const eur = (c: number) => (c / 100).toFixed(0) + '€'

export const SOURCE_LABEL: Record<string, string> = {
  kleinanzeigen: 'kk',
  vinted: 'vint',
}

export function downloadCsv(verdict: string, minMargin: number) {
  const p = new URLSearchParams()
  if (verdict) p.set('verdict', verdict)
  if (minMargin) p.set('min_margin', String(minMargin))
  fetch(`/api/v1/export.csv?${p}`, { headers: { Authorization: `Bearer ${getKey()}` } })
    .then((r) => {
      if (!r.ok) throw new Error(String(r.status))
      return r.blob()
    })
    .then((b) => {
      const a = document.createElement('a')
      a.href = URL.createObjectURL(b)
      a.download = `flipper-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
    })
    .catch(() => {})
}
export const VERDICT_ICON: Record<string, string> = { TAKE: '🟢', MAYBE: '🟡', SKIP: '⚪' }
export const VERDICT_TEXT: Record<string, string> = {
  TAKE: 'брать',
  MAYBE: 'посмотреть',
  SKIP: 'мимо',
}