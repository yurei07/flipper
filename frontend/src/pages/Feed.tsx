import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, eur, SOURCE_LABEL, VERDICT_ICON, type FeedItem } from '../lib/api'

const PAGE = 30
const HOUR_OPTIONS = [
  { v: 0, label: 'всё время' },
  { v: 24, label: '24 ч' },
  { v: 72, label: '72 ч' },
  { v: 168, label: '7 дней' },
]
const VERDICTS: { v: string; icon: string; label: string }[] = [
  { v: '', icon: '●', label: 'все' },
  { v: 'TAKE', icon: '🟢', label: 'брать' },
  { v: 'MAYBE', icon: '🟡', label: 'посмотреть' },
  { v: 'SKIP', icon: '⚪', label: 'мимо' },
]

function relTime(iso: string | null): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const h = Math.floor(ms / 3_600_000)
  if (h < 1) return 'только что'
  if (h < 24) return `${h} ч назад`
  return `${Math.floor(h / 24)} дн назад`
}

export function Feed({ savedOnly = false }: { savedOnly?: boolean }) {
  const [items, setItems] = useState<FeedItem[]>([])
  const [verdict, setVerdict] = useState('')
  const [q, setQ] = useState('')
  const [hours, setHours] = useState(0)
  const [off, setOff] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState(0)
  const [err, setErr] = useState('')

  const params = useMemo(
    () => ({
      verdict,
      created_after_hours: hours,
      limit: PAGE,
      saved: savedOnly,
    }),
    [verdict, hours, savedOnly],
  )

  const loadMore = useCallback(
    async (start: number, append: boolean) => {
      try {
        const rows = await api.feed({ ...params, offset: start })
        let shown = rows
        const needle = q.trim().toLowerCase()
        if (needle) {
          shown = rows.filter((r) => r.title.toLowerCase().includes(needle))
        }
        setItems((prev) => (append ? [...prev, ...shown] : shown))
        setOff(start + shown.length)
        setHasMore(rows.length >= PAGE)
        setErr('')
        if (!savedOnly) api.stats().then((st) => setTotal(st.total_listings)).catch(() => {})
      } catch (e: any) {
        setErr(e.message || 'ошибка загрузки')
      }
    },
    [params, q, savedOnly],
  )

  useEffect(() => {
    loadMore(0, false)
  }, [loadMore])

  useEffect(() => {
    const onRefresh = () => loadMore(0, false)
    window.addEventListener('flipper-refresh', onRefresh)
    const t = setInterval(onRefresh, 90_000)
    return () => {
      window.removeEventListener('flipper-refresh', onRefresh)
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadMore])

  const act = async (id: number, action: 'saved' | 'ignored' | 'dismissed') => {
    try {
      await api.act(id, action)
      setItems((xs) =>
        xs.map((x) =>
          x.ad_id === id
            ? { ...x, saved: action === 'saved', ignored: ['ignored', 'dismissed'].includes(action) }
            : x,
        ),
      )
      if (savedOnly && action !== 'saved') {
        setItems((xs) => xs.filter((x) => x.ad_id !== id))
      }
    } catch (e: any) {
      setErr(e.message || 'ошибка действия')
    }
  }

  return (
    <>
      <div className="toolbar">
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {HOUR_OPTIONS.map((h) => (
            <option key={h.v} value={h.v}>
              {h.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="поиск по названию"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button title="обновить" onClick={() => loadMore(0, false)}>
          ⟳
        </button>
        {!savedOnly && total > 0 && <span className="stats">лента: {total}</span>}
      </div>

      <div className="verdict-row">
        {VERDICTS.map((v) => (
          <button
            key={v.v}
            className={'verdict-chip' + (verdict === v.v ? ' on' : '')}
            onClick={() => setVerdict(v.v)}
          >
            <span className="vicon">{v.icon}</span>
            {v.label}
          </button>
        ))}
      </div>

      {savedOnly && <div className="dim title-line">Сохранённые лоты (последнее действие)</div>}

      {err && <div className="err">{err}</div>}

      <div className="grid">
        {items.map((it) => (
          <Link to={`/deal/${it.ad_id}`} className="card" key={it.ad_id}>
            <div className="thumb">
              {it.image_url ? <img src={it.image_url} loading="lazy" alt="" /> : <span className="noimg">◈</span>}
              <span className={`badge src-${it.source}`}>{SOURCE_LABEL[it.source] || it.source}</span>
              <span className={`verdict-ribbon ${it.verdict.toLowerCase()}`}>
                {VERDICT_ICON[it.verdict]} {it.verdict}
              </span>
            </div>
            <div className="body">
              <div className="deal-price">
                <b>{eur(it.price_cents)}</b>
                <span className={'marg' + (it.margin_eur >= 0 ? ' ok' : ' bad')}>
                  +{it.margin_eur.toFixed(0)}€ ({it.margin_pct.toFixed(0)}%)
                </span>
              </div>
              <div className="title">{it.title}</div>
              <div className="meta dim">
                <span className="dim">{it.location_city || '—'}</span>
                {it.market_estimated && <span title="рынок по малой выборке">~</span>}
                <span className="dim">{relTime(it.created_at)}</span>
              </div>
              <div className="actions">
                <button
                  className={'act' + (it.saved ? ' on saved' : '')}
                  onClick={(e) => {
                    e.preventDefault()
                    act(it.ad_id, 'saved')
                  }}
                >
                  {it.saved ? '★ сохранён' : '☆'}
                </button>
                <button
                  className={'act' + (it.ignored ? ' on ign' : '')}
                  onClick={(e) => {
                    e.preventDefault()
                    act(it.ad_id, 'ignored')
                  }}
                >
                  ☓
                </button>
              </div>
            </div>
          </Link>
        ))}
        {!items.length && !err && <div className="empty">{savedOnly ? 'пока нет сохранённых' : 'ничего не найдено'}</div>}
      </div>

      {hasMore && (
        <div className="pager">
          <button onClick={() => loadMore(off, true)}>показать ещё</button>
        </div>
      )}
    </>
  )
}