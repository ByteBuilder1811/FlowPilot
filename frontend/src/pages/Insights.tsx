import { useState } from 'react'
import { api } from '../api'
import { useAsync, fmtDur } from '../hooks'
import { AnimatedNumber, Empty, ErrorState, Loading, PageHeader, Panel, SysLabel, Tag, inputCls } from '../components/ui'

const COLORS: Record<string, string> = { succeeded: 'var(--color-ok)', failed: 'var(--color-bad)', timed_out: 'var(--color-warn)', cancelled: '#6b7288', waiting_for_approval: 'var(--color-accent)', running: 'var(--color-signal)', queued: '#4a5168', retrying: 'var(--color-warn)' }

export default function Insights() {
  const [days, setDays] = useState(14)
  const q = useAsync(() => api.get(`/api/insights?days=${days}`), [days], 8000)
  if (q.error) return <ErrorState error={q.error} retry={q.reload} />
  if (!q.data) return <Loading />
  const d = q.data
  const max = Math.max(1, ...d.by_day.map((x: any) => Object.entries(x).filter(([k]) => k !== 'day').reduce((a, [, v]) => a + (v as number), 0)))
  const maxErr = Math.max(1, ...d.errors.map((e: any) => e.count))
  return (
    <>
      <PageHeader title="Insights" subtitle="Operational analytics from your persisted live runs. Nothing here is sampled or estimated." actions={
        <select className={`${inputCls} w-36`} aria-label="Window" value={days} onChange={e => setDays(Number(e.target.value))}>{[7, 14, 30, 90].map(n => <option key={n} value={n}>Last {n} days</option>)}</select>} />
      <div className="space-y-5 p-6" data-testid="insights">
        {!d.total_runs ? <Panel><Empty title="No live runs in this window">Run a workflow and its reliability numbers will appear here.</Empty></Panel> : <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            {[['Runs', <AnimatedNumber value={d.total_runs} />, `${d.finished} finished`],
              ['Success rate', d.success_rate == null ? '—' : `${Math.round(d.success_rate * 100)}%`, `of ${d.finished} finished runs`],
              ['Duration p50 / p95', `${fmtDur(d.duration_ms.p50)} / ${fmtDur(d.duration_ms.p95)}`, `${d.duration_ms.samples} samples`],
              ['Retries', <AnimatedNumber value={d.retries} />, `${d.runs_with_retries} runs needed one`],
              ['Approval wait (median)', d.approvals.median_wait_s == null ? '—' : `${d.approvals.median_wait_s}s`, `${d.approvals.decided} decided · ${d.approvals.pending} pending`]].map(([k, v, sub]: any) => (
              <div key={k} className="rounded-xl border border-line bg-panel px-4 py-3"><div className="kicker text-[10px] text-faint">{k}</div><div className="tabular mt-1.5 font-display text-[24px] font-semibold leading-none">{v}</div><div className="mt-1.5 text-[11.5px] text-muted">{sub}</div></div>))}
          </div>
          <Panel title="Runs per day" actions={<SysLabel tone="faint">UTC</SysLabel>}>
            <div className="flex h-40 items-end gap-2" data-testid="by-day">{d.by_day.map((x: any) => {
              const parts = Object.entries(x).filter(([k]) => k !== 'day') as [string, number][]
              const total = parts.reduce((a, [, v]) => a + v, 0)
              return <div key={x.day} className="flex min-w-[28px] flex-1 flex-col items-center gap-1" title={`${x.day}: ${parts.map(([k, v]) => `${v} ${k}`).join(', ')}`}>
                <div className="flex w-full flex-col-reverse overflow-hidden rounded-t" style={{ height: `${(total / max) * 120}px` }}>{parts.map(([k, v]) => <div key={k} style={{ flex: v, background: COLORS[k] ?? '#555' }} />)}</div>
                <span className="mono text-[10px] text-faint">{x.day.slice(5)}</span></div>
            })}</div>
            <div className="mt-2 flex flex-wrap gap-3 text-[11.5px] text-muted">{['succeeded', 'failed', 'timed_out', 'cancelled', 'waiting_for_approval'].map(k => <span key={k} className="flex items-center gap-1.5"><span className="size-2 rounded-sm" style={{ background: COLORS[k] }} />{k}</span>)}</div>
          </Panel>
          <div className="grid gap-5 xl:grid-cols-2">
            <Panel title="Why runs fail" pad={false}>{d.errors.length ? <ul className="p-4 space-y-2">{d.errors.map((e: any) => <li key={e.code}><div className="flex justify-between text-[12.5px]"><span className="mono">{e.code}</span><span className="tabular text-muted">{e.count}</span></div><div className="mt-1 h-1.5 overflow-hidden rounded bg-line"><div className="h-full rounded bg-bad" style={{ width: `${(e.count / maxErr) * 100}%` }} /></div></li>)}</ul> : <Empty title="No errors">Every run in this window ended without an error code.</Empty>}</Panel>
            <Panel title="Tool reliability" pad={false}>{d.tools.length ? <table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2">Tool</th><th>Calls</th><th>Failed</th><th>Unknown outcome</th><th>Failure rate</th></tr></thead><tbody>
              {d.tools.map((t: any) => <tr key={t.tool} className="border-b border-line/60 last:border-0"><td className="mono px-4 py-2">{t.tool}</td><td className="tabular">{t.calls}</td><td className="tabular">{t.failed}</td><td className="tabular">{t.unknown ? <Tag tone="warn">{t.unknown}</Tag> : 0}</td><td className="tabular">{Math.round(t.failure_rate * 100)}%</td></tr>)}</tbody></table> : <Empty title="No tool calls" />}</Panel>
          </div>
          <Panel title="Slowest nodes (average)" pad={false}>{d.slow_nodes.length ? <table className="w-full text-left text-[12.5px]"><tbody>{d.slow_nodes.map((n: any) => <tr key={n.node} className="border-b border-line/60 last:border-0"><td className="mono px-4 py-2">{n.node}</td><td className="tabular">{fmtDur(n.avg_ms)}</td><td className="px-4 text-right text-muted">{n.samples} samples</td></tr>)}</tbody></table> : <Empty title="No timing data" />}</Panel>
          <div className="text-[12px] text-faint">{d.tokens ? `Provider-reported tokens: ${d.tokens.input} in / ${d.tokens.output} out. ` : 'Token usage: unavailable (mock providers report none). '}Cost: not estimated — no reliable pricing source. {d.notes[0]}.</div>
        </>}
      </div>
    </>
  )
}
