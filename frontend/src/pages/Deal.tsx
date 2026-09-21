import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, eur, VERDICT_ICON, type Deal } from '../lib/api'

export function DealPage() {
  const { id } = useParams()
  const [d, setD] = useState<Deal | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!id) return
    api.deal(Number(id)).then(setD).catch((e) => setErr(e.message || 'ошибка'))
  }, [id])

  if (err) return <main><div className="err">{err} <Link to="/">назад</Link></div></main>
  if (!d) return <main><div className="empty">загрузка…</div></main>

  const a = d.analysis
  const act = async (action: 'saved' | 'ignored') => {
    try {
      await api.act(d.ad_id, action)
      setD({ ...d, saved: action === 'saved', ignored: action === 'ignored' })
    } catch {}
  }

  const [reMsg, setReMsg] = useState('')
  const reanalyze = async () => {
    setReMsg('анализирую… (секунды)')
    try {
      await api.reanalyze(d.ad_id)
      const nd = await api.deal(d.ad_id)
      setD(nd)
      setReMsg('готово ✓')
    } catch {
      setReMsg('ошибка (может быть cooldown)')
    }
  }

  return (
    <main className="deal">
      <Link to="/" className="back">← лента</Link>
      <h1>{d.title}</h1>
      <div className="deal-top">
        <div className="photos">
          {d.image_urls.map((u, i) => <img key={i} src={u} alt="" />)}
        </div>
        <div className="deal-info">
          {a ? (
            <>
              <div className={`verdict ${a.verdict.toLowerCase()}`}>
                {VERDICT_ICON[a.verdict]} {a.verdict} · +{a.margin_eur.toFixed(0)}€ ({a.margin_pct.toFixed(0)}%)
                {a.market_estimated && <span className="badge">оценочно</span>}
              </div>
              <p className="row">Цена: <b>{eur(d.price_cents)}</b> · Рынок: <b>{a.market_working_eur ? a.market_working_eur.toFixed(0) + '€' : '—'}</b> ({a.flip_type === 'repair' ? 'ремонт' : 'перепродажа'}, запчасти {a.parts_cost_eur.toFixed(0)}€)</p>
              <p className="row">Уверенность: {Math.round(a.confidence * 100)}%</p>
              {a.pros_ru.length > 0 && <ul className="pros">{a.pros_ru.map((p, i) => <li key={i}>✅ {p}</li>)}</ul>}
              {a.cons_ru.length > 0 && <ul className="cons">{a.cons_ru.map((c, i) => <li key={i}>⚠️ {c}</li>)}</ul>}
              {a.action_ru && <p className="action">{a.action_ru}</p>}
            </>
          ) : <p className="empty">Анализа ещё нет.</p>}
          <div className="actions">
            <button className={d.saved ? 'on saved' : ''} onClick={() => act('saved')}>⭐ {d.saved ? 'взял' : 'беру'}</button>
            <button className={d.ignored ? 'on ign' : ''} onClick={() => act('ignored')}>🚫 {d.ignored ? 'скрыт' : 'мимо'}</button>
            <button onClick={reanalyze}>↻ переанализ</button>
            {reMsg && <span className="dim">{reMsg}</span>}
            <a className="ext" href={d.url} target="_blank" rel="noreferrer">открыть на Kleinanzeigen ↗</a>
          </div>
          <p className="dim">📍 {d.location_city || ''} · {d.created_at?.slice(0, 10) || ''}</p>
        </div>
      </div>
      <pre className="desc">{d.description}</pre>
    </main>
  )
}