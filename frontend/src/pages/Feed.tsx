import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, downloadCsv, eur, VERDICT_ICON, VERDICT_TEXT, type FeedItem } from '../lib/api'

const PAGE = 40

export function Feed() {
  const [items, setItems] = useState<FeedItem[]>([])
  const [verdict, setVerdict] = useState<string>('')
  const [minMargin, setMinMargin] = useState(0)
  const [q, setQ] = useState('')
  const [off, setOff] = useState(0)
  const [stats, setStats] = useState<{ total_listings: number; verdicts: Record<string, number> } | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(
    async (start = 0, useQ?: string, useV?: string, useMargin?: number) => {
      try {
        let rows = await api.feed({
          verdict: useV || verdict,
          min_margin: useMargin ?? minMargin,
          limit: PAGE,
          offset: start,
        })
        if (useQ ?? q) {
          const needle = (useQ ?? q).toLowerCase()
          rows = rows.filter((r) => r.title.toLowerCase().includes(needle))
        }
        setItems(rows)
        setOff(start)
        setErr('')
        api.stats().then(setStats).catch(() => {})
      } catch (e: any) {
        setErr(e.message || 'ошибка загрузки')
      }
    },
    [verdict, minMargin, q],
  )

  useEffect(() => {
    load(0)
    const onRefresh = () => load(0)
    window.addEventListener('flipper-refresh', onRefresh)
    const t = setInterval(() => load(off), 90_000)
    return () => {
      window.removeEventListener('flipper-refresh', onRefresh)
      clearInterval(t)
    }
  }, [load])

  const act = async (id: number, action: 'saved' | 'ignored' | 'dismissed') => {
    try {
      await api.act(id, action)
      setItems((prev) => prev.map((i) => (i.ad_id === id ? { ...i, saved: action === 'saved', ignored: action !== 'saved' } : i)))
    } catch {}
  }

  return (
    <main>
      <div className="toolbar">
        <select value={verdict} onChange={(e) => { setVerdict(e.target.value); load(0, q, e.target.value, minMargin) }}>
          <option value="">все вердикты</option>
          <option value="TAKE">🟢 TAKE</option>
          <option value="MAYBE">🟡 MAYBE</option>
          <option value="SKIP">⚪ SKIP</option>
        </select>
        <input type="number" placeholder="мин. маржа €" value={minMargin || ''}
          onChange={(e) => { const v = Number(e.target.value) || 0; setMinMargin(v); load(0, q, verdict, v) }} />
        <input placeholder="поиск по названию"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load(0) }} />
        <button title="Скачать CSV текущего списка" onClick={() => downloadCsv(verdict, minMargin)}>⬇ CSV</button>
        {stats && <span className="stats">📦 {stats.total_listings} · 🟢{stats.verdicts.TAKE ?? 0} 🟡{stats.verdicts.MAYBE ?? 0}</span>}
      </div>

      {err && <div className="err">{err}</div>}

      <div className="grid">
        {items.map((it) => (
          <article className="card" key={it.ad_id}>
            <Link to={`/deal/${it.ad_id}`} className="thumb">
              {it.image_url ? <img src={it.image_url} loading="lazy" alt="" /> : <div className="noimg">📷</div>}
            </Link>
            <div className="body">
              <Link to={`/deal/${it.ad_id}`} className="title">{it.title}</Link>
              <div className="meta">
                <b className={it.verdict.toLowerCase()}>{VERDICT_ICON[it.verdict]} {VERDICT_TEXT[it.verdict]}</b>
                <span>+{it.margin_eur.toFixed(0)}€ ({it.margin_pct.toFixed(0)}%)</span>
                {it.market_estimated && <span className="badge" title="мало лотов-аналогов: оценка ИИ, а не рынок">оценочно</span>}
              </div>
              <div className="meta dim">
                <span>{eur(it.price_cents)} · {it.location_city || ''}</span>
              </div>
              <div className="actions">
                <button title="беру/отложил" className={it.saved ? 'on saved' : ''} onClick={() => act(it.ad_id, 'saved')}>⭐ <small>{it.saved ? 'взял' : 'беру'}</small></button>
                <button title="игнорировать" className={it.ignored ? 'on ign' : ''} onClick={() => act(it.ad_id, 'ignored')}>🚫 <small>{it.ignored ? 'скрыт' : 'мимо'}</small></button>
              </div>
            </div>
          </article>
        ))}
        {items.length === 0 && <div className="empty">Пусто. Новые TAKE/MAYBE появятся здесь после цикла сканирования.</div>}
      </div>

      {items.length === PAGE && (
        <div className="pager">
          <button disabled={off === 0} onClick={() => load(Math.max(0, off - PAGE))}>← назад</button>
          <button onClick={() => load(off + PAGE)}>дальше →</button>
        </div>
      )}
    </main>
  )
}