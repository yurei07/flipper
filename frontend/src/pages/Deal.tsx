import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, eur, VERDICT_ICON, type Deal } from '../lib/api'

export function DealPage() {
  const { id } = useParams()
  const [d, setD] = useState<Deal | null>(null)
  const [err, setErr] = useState('')
  const [photo, setPhoto] = useState(0)
  const [reMsg, setReMsg] = useState('')
  const [showDesc, setShowDesc] = useState(false)

  useEffect(() => {
    if (!id) return
    api.deal(Number(id)).then(setD).catch((e) => setErr(e.message || 'ошибка'))
  }, [id])

  if (err)
    return (
      <main>
        <div className="err">
          {err} <Link to="/">назад</Link>
        </div>
      </main>
    )
  if (!d) return <main><div className="empty">загрузка…</div></main>

  const a = d.analysis
  const act = async (action: 'saved' | 'ignored') => {
    try {
      await api.act(d.ad_id, action)
      setD({ ...d, saved: action === 'saved', ignored: action === 'ignored' })
    } catch {}
  }
  const reanalyze = async () => {
    setReMsg('анализирую…')
    try {
      await api.reanalyze(d.ad_id)
      const nd = await api.deal(d.ad_id)
      setD(nd)
      setReMsg('готово ✓')
    } catch {
      setReMsg('ошибка (может быть cooldown)')
    }
  }

  const img = d.image_urls.length ? d.image_urls[Math.min(photo, d.image_urls.length - 1)] : null
  const sourceName = d.source === 'vinted' ? 'Vinted' : 'Kleinanzeigen'
  const verdictCls = a ? a.verdict.toLowerCase() : ''

  const stats: { k: string; v: string; cls?: string }[] = a
    ? [
        { k: 'Цена лота', v: eur(d.price_cents) },
        { k: 'Запчасти', v: a.parts_cost_eur > 0 ? a.parts_cost_eur.toFixed(0) + '€' : '0€' },
        { k: 'Рынок', v: a.market_working_eur ? a.market_working_eur.toFixed(0) + '€' : '—' },
        { k: 'Маржа', v: `+${a.margin_eur.toFixed(0)}€ · ${a.margin_pct.toFixed(0)}%`, cls: a.margin_eur >= 0 ? verdictCls : 'bad' },
      ]
    : []

  return (
    <main className="deal">
      <Link to="/" className="back">
        ← лента
      </Link>

      <div className="deal-head">
        <span className={`badge src-${d.source}`}>{sourceName}</span>
        <span className="dim">{d.location_city || '—'}</span>
        <span className="dim">{d.created_at?.slice(0, 10) || ''}</span>
      </div>
      <h1>{d.title}</h1>

      {img && (
        <div className="hero">
          <img src={img} alt="" />
          {d.image_urls.length > 1 && (
            <div className="hero-thumbs">
              {d.image_urls.map((u, i) => (
                <button key={i} className={'thumb-btn' + (i === Math.min(photo, d.image_urls.length - 1) ? ' on' : '')} onClick={() => setPhoto(i)}>
                  <img src={u} alt="" />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {a ? (
        <div className={'verdict-panel ' + verdictCls}>
          <div className="verdict-line">
            <span className="verdict-big">
              {VERDICT_ICON[a.verdict]} {a.verdict}
            </span>
            <span className="verdict-markup">+{a.margin_eur.toFixed(0)}€ / {a.margin_pct.toFixed(0)}%</span>
          </div>
          <div className="stat-grid">
            {stats.map((s) => (
              <div className={'stat' + (s.cls ? ' ' + s.cls : '')} key={s.k}>
                <div className="stat-k">{s.k}</div>
                <div className="stat-v">{s.v}</div>
              </div>
            ))}
          </div>
          <div className="tag-row">
            <span className="tag">{a.flip_type === 'repair' ? '🧰 ремонт' : '📦 перепродажа'}</span>
            <span className="tag">уверенность {Math.round(a.confidence * 100)}%</span>
            {a.market_estimated && <span className="tag">рынок оценочный</span>}
          </div>
          {a.pros_ru.length > 0 && (
            <ul className="pros">
              {a.pros_ru.map((p, i) => <li key={i}>✅ {p}</li>)}
            </ul>
          )}
          {a.cons_ru.length > 0 && (
            <ul className="cons">
              {a.cons_ru.map((c, i) => <li key={i}>⚠️ {c}</li>)}
            </ul>
          )}
          {a.action_ru && <div className="action">{a.action_ru}</div>}
        </div>
      ) : (
        <div className="empty">Анализа ещё нет — нажми «переанализ».</div>
      )}

      <div className="big-actions">
        <a className="open-btn" href={d.url} target="_blank" rel="noreferrer">
          Открыть на {sourceName} ↗
        </a>
        <button className={'saved-btn' + (d.saved ? ' on' : '')} onClick={() => act('saved')}>
          {d.saved ? '★ Взял' : '☆ Беру'}
        </button>
        <button className={'skip-btn' + (d.ignored ? ' on' : '')} onClick={() => act('ignored')}>
          ☓ Мимо
        </button>
        <button onClick={reanalyze}>↻ Переанализ</button>
      </div>
      {reMsg && <div className="dim">{reMsg}</div>}

      {d.description && (
        <div className={'desc' + (showDesc ? '' : ' collapsed')} onClick={() => setShowDesc((v) => !v)}>
          {d.description}
        </div>
      )}
    </main>
  )
}