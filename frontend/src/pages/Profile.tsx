import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Flip, type FlipSummary } from '../lib/api'

const money = (v: number) => String(+v.toFixed(2)) + '€'
const signed = (v: number) => (v >= 0 ? '+' : '') + money(v)
const today = () => new Date().toISOString().slice(0, 10)

function FlipCard({ f, onChanged }: { f: Flip; onChanged: () => void }) {
  const [edit, setEdit] = useState(false)
  const [buy, setBuy] = useState(String(f.buy_price_eur))
  const [extras, setExtras] = useState(String(f.extras_eur || ''))
  const [sale, setSale] = useState(f.sale_price_eur != null ? String(f.sale_price_eur) : '')
  const [fees, setFees] = useState(String(f.sell_fees_eur || ''))
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const num = (s: string) => {
    const n = parseFloat(s.replace(',', '.'))
    return isNaN(n) ? 0 : n
  }

  const saveBuy = async () => {
    setBusy(true)
    try {
      await api.flipUpdate(f.id, { buy_price_eur: num(buy), extras_eur: num(extras) })
      setEdit(false)
      onChanged()
    } catch (e: any) {
      setMsg(e.message || 'ошибка')
    } finally {
      setBusy(false)
    }
  }

  const sell = async () => {
    if (!sale) {
      setMsg('укажи цену продажи')
      return
    }
    setBusy(true)
    try {
      await api.flipUpdate(f.id, {
        status: 'sold',
        sale_price_eur: num(sale),
        sell_fees_eur: num(fees),
      })
      onChanged()
    } catch (e: any) {
      setMsg(e.message || 'ошибка')
    } finally {
      setBusy(false)
    }
  }

  const back = async () => {
    setBusy(true)
    try {
      await api.flipUpdate(f.id, { status: 'stock' })
      onChanged()
    } catch (e: any) {
      setMsg(e.message || 'ошибка')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!confirm('Удалить сделку из портфеля?')) return
    try {
      await api.flipRemove(f.id)
      onChanged()
    } catch (e: any) {
      setMsg(e.message || 'ошибка')
    }
  }

  const sold = f.status === 'sold'
  const profit = f.profit_eur

  return (
    <div className={'pf-card' + (sold ? ' sold' : '')}>
      <div className="pf-head">
        {f.listing_id ? (
          <Link to={`/deal/${f.listing_id}`} className="pf-title">
            {f.title || 'без названия'}
          </Link>
        ) : (
          <span className="pf-title">{f.title || 'без названия'}</span>
        )}
        <span className={'pf-status ' + f.status}>{sold ? 'продано' : 'в наличии'}</span>
      </div>

      {edit ? (
        <div className="pf-edit">
          <label>
            куплено, €
            <input inputMode="decimal" value={buy} onChange={(e) => setBuy(e.target.value)} />
          </label>
          <label>
            доп. расходы, €
            <input inputMode="decimal" value={extras} onChange={(e) => setExtras(e.target.value)} />
          </label>
          <div className="pf-edit-actions">
            <button className="pf-mini on" disabled={busy} onClick={saveBuy}>
              сохранить
            </button>
            <button className="pf-mini" onClick={() => setEdit(false)}>
              отмена
            </button>
          </div>
        </div>
      ) : (
        <div className="pf-money" onClick={() => setEdit(true)} title="изменить">
          куплено {money(f.buy_price_eur)}
          {f.extras_eur > 0 && <> + расходы {money(f.extras_eur)}</>}
          <span className="pf-invested">вложено {money(f.invested_eur)}</span>
        </div>
      )}

      {sold ? (
        <div className="pf-result">
          <span className="pf-sold-for">продано за {money(f.sale_price_eur || 0)}</span>
          <span className={'pf-profit' + (profit != null && profit >= 0 ? ' win' : ' lose')}>
            {profit != null ? signed(profit) : '—'}
            {f.roi_pct != null && <em> · {f.roi_pct.toFixed(0)}%</em>}
          </span>
        </div>
      ) : (
        <div className="pf-sell">
          <input
            inputMode="decimal"
            placeholder="продал за €"
            value={sale}
            onChange={(e) => setSale(e.target.value)}
          />
          <input
            inputMode="decimal"
            placeholder="комиссия €"
            value={fees}
            onChange={(e) => setFees(e.target.value)}
          />
          <button className="pf-mini on" disabled={busy} onClick={sell}>
            ✓ продано
          </button>
        </div>
      )}

      {msg && <div className="pf-msg">{msg}</div>}

      <div className="pf-tools">
        {sold && (
          <button className="pf-mini" disabled={busy} onClick={back}>
            ↩ в наличии
          </button>
        )}
        <button className="pf-mini danger" onClick={remove}>
          удалить
        </button>
      </div>
    </div>
  )
}

export function Profile() {
  const [sum, setSum] = useState<FlipSummary | null>(null)
  const [items, setItems] = useState<Flip[]>([])
  const [tab, setTab] = useState<'stock' | 'sold' | 'all'>('stock')
  const [err, setErr] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [t, setT] = useState('')
  const [b, setB] = useState('')
  const [ex, setEx] = useState('')
  const [bd, setBd] = useState(today())
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([api.flipSummary(), api.flips()])
      setSum(s)
      setItems(list)
      setErr('')
    } catch (e: any) {
      setErr(e.message || 'ошибка загрузки')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const num = (s: string) => {
    const n = parseFloat(s.replace(',', '.'))
    return isNaN(n) ? 0 : n
  }

  const add = async () => {
    if (!t.trim() && !b) {
      setErr('укажи название и цену')
      return
    }
    setBusy(true)
    try {
      await api.flipCreate({
        title: t.trim(),
        buy_price_eur: num(b),
        extras_eur: num(ex),
        buy_date: bd ? new Date(bd).toISOString() : undefined,
      })
      setT('')
      setB('')
      setEx('')
      setBd(today())
      setAddOpen(false)
      await load()
    } catch (e: any) {
      setErr(e.message || 'ошибка')
    } finally {
      setBusy(false)
    }
  }

  const shown = items.filter((f) => (tab === 'all' ? true : f.status === tab))

  return (
    <main className="profile">
      <h1>Портфель</h1>

      {sum && (
        <div className="pf-summary">
          <div className={'pf-stat big' + (sum.profit_eur >= 0 ? ' win' : ' lose')}>
            <div className="pf-stat-v">{signed(sum.profit_eur)}</div>
            <div className="pf-stat-k">заработано</div>
          </div>
          <div className="pf-stat">
            <div className="pf-stat-v">{sum.roi_pct != null ? sum.roi_pct.toFixed(0) + '%' : '—'}</div>
            <div className="pf-stat-k">средний ROI</div>
          </div>
          <div className="pf-stat">
            <div className="pf-stat-v">{sum.sold_count}</div>
            <div className="pf-stat-k">продано</div>
          </div>
          <div className="pf-stat">
            <div className="pf-stat-v">{sum.stock_count}</div>
            <div className="pf-stat-k">в деле · {money(sum.stock_invested_eur)}</div>
          </div>
        </div>
      )}

      <div className="pf-actions">
        <div className="pf-tabs">
          {(
            [
              ['stock', 'в наличии'],
              ['sold', 'продано'],
              ['all', 'все'],
            ] as const
          ).map(([v, label]) => (
            <button key={v} className={'pf-tab' + (tab === v ? ' on' : '')} onClick={() => setTab(v)}>
              {label}
            </button>
          ))}
        </div>
        <button className="pf-add" onClick={() => setAddOpen((v) => !v)}>
          {addOpen ? '×' : '+ сделка'}
        </button>
      </div>

      {addOpen && (
        <div className="pf-addform">
          <input placeholder="что купил (название)" value={t} onChange={(e) => setT(e.target.value)} />
          <div className="pf-addrow">
            <label>
              куплено, €
              <input inputMode="decimal" value={b} onChange={(e) => setB(e.target.value)} />
            </label>
            <label>
              доп. расходы, €
              <input inputMode="decimal" value={ex} onChange={(e) => setEx(e.target.value)} />
            </label>
            <label>
              дата
              <input type="date" value={bd} onChange={(e) => setBd(e.target.value)} />
            </label>
          </div>
          <button className="pf-mini on" disabled={busy} onClick={add}>
            добавить
          </button>
        </div>
      )}

      {err && <div className="err">{err}</div>}

      <div className="pf-list">
        {shown.map((f) => (
          <FlipCard key={f.id} f={f} onChanged={load} />
        ))}
        {!shown.length && !err && (
          <div className="empty">
            {tab === 'sold' ? 'пока ничего не продано' : 'портфель пуст — отметь лот кнопкой «Купил»'}
          </div>
        )}
      </div>
    </main>
  )
}
