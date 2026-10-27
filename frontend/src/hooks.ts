import { useCallback, useEffect, useRef, useState } from 'react'

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const fnRef = useRef(fn); fnRef.current = fn
  const reload = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try { setData(await fnRef.current()); setError(null) } catch (e) { setError(e as Error) } finally { setLoading(false) }
  }, [])
  useEffect(() => { reload() }, deps) // eslint-disable-line
  useEffect(() => { if (!pollMs) return; const t = setInterval(() => reload(true), pollMs); return () => clearInterval(t) }, [pollMs, reload])
  return { data, error, loading, reload, setData }
}

export function fmtDur(ms: number | null | undefined) {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}
export function ago(iso?: string | null) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 5) return 'just now'
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}
export const runDuration = (r: { started_at?: string | null; completed_at?: string | null }) =>
  r.started_at ? (r.completed_at ? new Date(r.completed_at).getTime() : Date.now()) - new Date(r.started_at).getTime() : null
