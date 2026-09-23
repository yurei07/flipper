import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  clearAuth,
  getServer,
  setServer,
  type AppSettings,
  type SearchDraft,
  type SearchRow,
} from '../lib/api'

const EMPTY_SETTINGS: AppSettings = {
  exclude_keywords: [],
  exclude_title_keywords: [],
  vinted_enabled: true,
  vinted_default_catalog_ids: [],
  vinted_domain: 'https://www.vinted.de',
}

const SORT_OPTIONS = [
  ['newest_first', 'сначала новые'],
  ['price_low_to_high', 'от дешёвых'],
  ['price_high_to_low', 'от дорогих'],
]

function ChipEditor(props: {
  label: string
  hint?: string
  values: string[]
  onChange: (v: string[]) => void
}) {
  const [input, setInput] = useState('')
  const add = () => {
    const words = input.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean)
    if (!words.length) return
    props.onChange([...new Set([...props.values, ...words])])
    setInput('')
  }
  return (
    <div className="chips">
      <div className="chips-row">
        {props.values.map((v) => (
          <span className="chip" key={v}>
            {v}
            <button
              type="button"
              className="chip-x"
              title="убрать"
              onClick={() => props.onChange(props.values.filter((x) => x !== v))}
            >
              ×
            </button>
          </span>
        ))}
        {!props.values.length && <span className="chips-empty">пока пусто</span>}
      </div>
      <div className="chips-add">
        <input
          placeholder="добавить через запятую"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <button onClick={add}>+</button>
      </div>
      {props.hint && <div className="dim chips-hint">{props.hint}</div>}
    </div>
  )
}

function SearchForm(props: {
  initial: SearchRow | null
  catalogs: { id: string; title: string }[]
  onSave: (d: SearchDraft, id?: number) => Promise<void>
  onCancel: () => void
}) {
  const [d, setD] = useState<SearchDraft>(() => {
    const f = props.initial?.filters || {}
    return {
      query: props.initial?.query || '',
      source: props.initial?.source || 'kleinanzeigen',
      max_price_eur: f.max_price_eur,
      radius_km: f.radius_km,
      location: f.location,
      sort_price: f.sort_price,
      catalog_ids: f.catalog_ids || [],
      brand_ids: f.brand_ids || [],
      status_ids: f.status_ids || [],
      order: f.order,
      active: props.initial?.active ?? true,
    }
  })
  const [busy, setBusy] = useState(false)
  const set = (p: Partial<SearchDraft>) => setD((x) => ({ ...x, ...p }))
  const isV = d.source === 'vinted'

  return (
    <form
      className="search-form"
      onSubmit={async (e) => {
        e.preventDefault()
        if (!d.query.trim()) return
        setBusy(true)
        try {
          await props.onSave(d, props.initial?.id)
        } finally {
          setBusy(false)
        }
      }}
    >
      <select value={d.source} onChange={(e) => set({ source: e.target.value as SearchDraft['source'] })}>
        <option value="kleinanzeigen">Kleinanzeigen</option>
        <option value="vinted">Vinted</option>
      </select>
      <input
        required
        placeholder="запрос"
        value={d.query}
        onChange={(e) => set({ query: e.target.value })}
      />
      <input
        type="number"
        min="0"
        placeholder="цена ≤ €"
        value={d.max_price_eur ?? ''}
        onChange={(e) => set({ max_price_eur: e.target.value ? Number(e.target.value) : undefined })}
      />
      {!isV && (
        <>
          <input
            type="number"
            min="0"
            placeholder="радиус км"
            value={d.radius_km ?? ''}
            onChange={(e) => set({ radius_km: e.target.value ? Number(e.target.value) : undefined })}
          />
          <input
            placeholder="город"
            value={d.location ?? ''}
            onChange={(e) => set({ location: e.target.value })}
          />
          <select
            value={d.sort_price || ''}
            onChange={(e) => set({ sort_price: e.target.value || undefined })}
          >
            <option value="">цена: без сортировки</option>
            <option value="ASCENDING">по возрастанию</option>
            <option value="DESCENDING">по убыванию</option>
          </select>
        </>
      )}
      {isV && (
        <>
          <select
            value={d.order || 'newest_first'}
            onChange={(e) => set({ order: e.target.value })}
          >
            {SORT_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select
            value={(d.catalog_ids || [])[0] ?? ''}
            onChange={(e) => set({ catalog_ids: e.target.value ? [Number(e.target.value)] : [] })}
          >
            <option value="">категория: без фильтра</option>
            {props.catalogs.map((c) => (
              <option key={c.id} value={c.id}>
                #{c.id} {c.title}
              </option>
            ))}
          </select>
        </>
      )}
      <label className="switch form-active">
        <input
          type="checkbox"
          checked={d.active}
          onChange={(e) => set({ active: e.target.checked })}
        />
        активен
      </label>
      <button type="submit" disabled={busy} className="btn-pri">
        {props.initial ? 'Сохранить' : 'Добавить'}
      </button>
      {props.initial && (
        <button type="button" onClick={props.onCancel}>
          Отмена
        </button>
      )}
    </form>
  )
}

export function Settings() {
  const [settings, setSettings] = useState<AppSettings>(EMPTY_SETTINGS)
  const [searches, setSearches] = useState<SearchRow[]>([])
  const [catalogs, setCatalogs] = useState<{ id: string; title: string }[]>([])
  const [adding, setAdding] = useState<SearchRow | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [msg, setMsg] = useState('')
  const [serverInp, setServerInp] = useState(getServer())

  const load = useCallback(async () => {
    try {
      const [s, lst, cats] = await Promise.all([
        api.settings(),
        api.searchList(),
        api.vintedCatalogs().catch(() => []),
      ])
      setSettings(s)
      setSearches(lst)
      setCatalogs(cats)
      setMsg('')
    } catch (e: any) {
      setMsg(e.message || 'ошибка загрузки')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const saveSearch = async (d: SearchDraft, id?: number) => {
    if (id != null) {
      await api.searchUpdate(id, d)
    } else {
      await api.searchCreate(d)
    }
    setAdding(null)
    setEditingId(null)
    const lst = await api.searchList()
    setSearches(lst)
    setMsg('поиск сохранён')
  }

  const removeSearch = async (id: number) => {
    await api.searchRemove(id)
    setSearches((x) => x.filter((r) => r.id !== id))
    setMsg('поиск удалён')
  }

  const applySettings = async (patch: Partial<AppSettings>) => {
    const next = await api.setSettings(patch)
    setSettings(next)
    setMsg('настройки применены (в следующем цикле)')
  }

  const kk = searches.filter((s) => s.source === 'kleinanzeigen')
  const vin = searches.filter((s) => s.source === 'vinted')

  return (
    <div className="settings">
      <div className="toolbar">
        <Link to="/" className="back">
          ← лента
        </Link>
        <span className="stats">настройки</span>
      </div>
      {msg && <div className="ok">{msg}</div>}

      <section>
        <h2>Сервер и доступ</h2>
        <div className="srow">
          <span className="s-query">Адрес сервера</span>
          <input
            placeholder="https://… (пусто = этот адрес)"
            value={serverInp}
            autoCapitalize="none"
            autoCorrect="off"
            onChange={(e) => setServerInp(e.target.value)}
          />
          <button
            className="btn-pri"
            onClick={() => {
              setServer(serverInp)
              setMsg('сохранено')
            }}
          >
            Сохранить
          </button>
        </div>
        <div className="srow">
          <span className="s-query">Сессия</span>
          <button
            className="danger"
            onClick={() => {
              clearAuth()
              window.location.reload()
            }}
          >
            Выйти
          </button>
        </div>
        <div className="dim">
          Вне домашнего Wi-Fi укажи стабильный https-адрес туннеля (nazariipalahnii.com).
        </div>
      </section>

      <section>
        <h2>Поиски — Kleinanzeigen</h2>
        {kk.map((r) => (
          <div className="srow" key={r.id}>
            {editingId === r.id ? (
              <SearchForm
                initial={r}
                catalogs={catalogs}
                onSave={(d) => saveSearch(d, r.id)}
                onCancel={() => setEditingId(null)}
              />
            ) : (
              <>
                <span className="badge src-kleinanzeigen">kk</span>
                <span className="s-query">{r.query}</span>
                <span className="dim">
                  {r.filters.max_price_eur != null && `≤ ${r.filters.max_price_eur}€ · `}
                  {[r.filters.radius_km != null && `+${r.filters.radius_km}км`, r.filters.location]
                    .filter(Boolean)
                    .join(' ')}
                </span>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={r.active}
                    onChange={(e) => saveSearch({ ...rowDraft(r), active: e.target.checked }, r.id)}
                  />
                  активен
                </label>
                <button onClick={() => setEditingId(r.id)}>✎</button>
                <button className="danger" onClick={() => removeSearch(r.id)}>
                  ✕
                </button>
              </>
            )}
          </div>
        ))}
        {!kk.length && <div className="dim">Поиски из конфига — правятся в config.yaml.</div>}
      </section>

      <section>
        <h2>Поиски — Vinted</h2>
        {vin.map((r) => (
          <div className="srow" key={r.id}>
            {editingId === r.id ? (
              <SearchForm
                initial={r}
                catalogs={catalogs}
                onSave={(d) => saveSearch(d, r.id)}
                onCancel={() => setEditingId(null)}
              />
            ) : (
              <>
                <span className="badge src-vinted">vint</span>
                <span className="s-query">{r.query}</span>
                <span className="dim">
                  {r.filters.max_price_eur != null && `≤ ${r.filters.max_price_eur}€ · `}
                  {r.filters.catalog_ids?.length ? `кат. ${r.filters.catalog_ids.join(',')}` : ''}
                </span>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={r.active}
                    onChange={(e) => saveSearch({ ...rowDraft(r), active: e.target.checked }, r.id)}
                  />
                  активен
                </label>
                <button onClick={() => setEditingId(r.id)}>✎</button>
                <button className="danger" onClick={() => removeSearch(r.id)}>
                  ✕
                </button>
              </>
            )}
          </div>
        ))}
        {!vin.length && <div className="dim">Поиски из конфига — правятся в config.yaml.</div>}
      </section>

      <section>
        <h2>Добавить поиск</h2>
        {adding ? (
          <SearchForm
            initial={null}
            catalogs={catalogs}
            onSave={saveSearch}
            onCancel={() => setAdding(null)}
          />
        ) : (
          <button
            className="btn-pri add-btn"
            onClick={() => setAdding({ id: 0 } as SearchRow)}
          >
            + добавить
          </button>
        )}
      </section>

      <section>
        <h2>Vinted</h2>
        <div className="srow">
          <span className="s-query">Источник Vinted</span>
          <label className="switch">
            <input
              type="checkbox"
              checked={settings.vinted_enabled}
              onChange={(e) => applySettings({ vinted_enabled: e.target.checked })}
            />
            {settings.vinted_enabled ? 'включён' : 'выключен'}
          </label>
        </div>
        <div className="srow">
          <span className="s-query">Категория по умолчанию</span>
          <select
            value={(settings.vinted_default_catalog_ids || [])[0] ?? ''}
            onChange={(e) =>
              applySettings({ vinted_default_catalog_ids: e.target.value ? [Number(e.target.value)] : [] })
            }
          >
            <option value="">без фильтра (весь Vinted)</option>
            {catalogs.map((c) => (
              <option key={c.id} value={c.id}>
                #{c.id} {c.title}
              </option>
            ))}
          </select>
        </div>
        <div className="dim">домен: {settings.vinted_domain}</div>
      </section>

      <section>
        <h2>Стоп-слова</h2>
        <ChipEditor
          label="Общие (в заголовке и описании)"
          values={settings.exclude_keywords}
          onChange={(v) => applySettings({ exclude_keywords: v })}
        />
        <ChipEditor
          label="Только в заголовке (аксессуары: чехол, плёнка, сумка…)"
          values={settings.exclude_title_keywords}
          onChange={(v) => applySettings({ exclude_title_keywords: v })}
        />
        <div className="dim">Стоп-слова применяются к обоим источникам с следующего цикла.</div>
      </section>
    </div>
  )
}

function rowDraft(r: SearchRow): SearchDraft {
  const f = r.filters || {}
  return {
    query: r.query,
    source: r.source,
    max_price_eur: f.max_price_eur,
    radius_km: f.radius_km,
    location: f.location,
    sort_price: f.sort_price,
    catalog_ids: f.catalog_ids,
    brand_ids: f.brand_ids,
    status_ids: f.status_ids,
    order: f.order,
    active: r.active,
  }
}