import { useEffect, useRef, useState } from 'react'
import { api } from './api'

export type RunEvent = { seq: number; type: string; node_id: string | null; data: any; ts: string }

/** Streams run events over SSE. Dedupes by seq; after a drop it backfills missed events via REST before resuming. */
export function useRunStream(runId: string, onBatch: () => void) {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [state, setState] = useState<'connecting' | 'live' | 'reconnecting' | 'closed'>('connecting')
  const last = useRef(0)
  const cb = useRef(onBatch); cb.current = onBatch

  useEffect(() => {
    let es: EventSource | null = null
    let dead = false
    let timer: any
    const add = (evs: RunEvent[]) => {
      const fresh = evs.filter(e => e.seq > last.current)
      if (!fresh.length) return
      last.current = fresh[fresh.length - 1].seq
      setEvents(prev => [...prev, ...fresh]); cb.current()
    }
    const connect = () => {
      if (dead) return
      es = new EventSource(`/api/runs/${runId}/events/stream?after=${last.current}`)
      es.onopen = () => setState('live')
      es.addEventListener('run_event', (m: MessageEvent) => add([JSON.parse(m.data)]))
      es.addEventListener('end', () => { setState('closed'); es?.close(); cb.current() })
      es.onerror = async () => {
        es?.close(); if (dead) return
        setState('reconnecting')
        try { add((await api.get(`/api/runs/${runId}/events?after=${last.current}`)).events) } catch { /* retry */ }
        timer = setTimeout(connect, 1500)
      }
    }
    last.current = 0; setEvents([]); connect()
    return () => { dead = true; es?.close(); clearTimeout(timer) }
  }, [runId])
  return { events, state }
}
