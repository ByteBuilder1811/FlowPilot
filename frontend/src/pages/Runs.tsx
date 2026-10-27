import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Empty, ErrorState, Loading, PageHeader, Panel, inputCls } from '../components/ui'
import RunTable from '../components/RunTable'

export default function Runs() {
  const [sp, setSp] = useSearchParams()
  const status = sp.get('status') ?? '', mode = sp.get('mode') ?? ''
  const { data, error, loading, reload } = useAsync(() => api.get(`/api/runs?limit=100${status ? `&status=${status}` : ''}${mode ? `&mode=${mode}` : ''}`), [status, mode], 3000)
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n) }
  if (error) return <ErrorState error={error} retry={reload} />
  return (
    <>
      <PageHeader title="Runs" subtitle="Every execution, persisted. Live runs and fixture replays are labelled separately." actions={<>
        <select aria-label="Filter status" className={`${inputCls} max-w-40`} value={status} onChange={e => set('status', e.target.value)}><option value="">All statuses</option>{['queued', 'running', 'retrying', 'waiting_for_approval', 'succeeded', 'failed', 'cancelled', 'timed_out'].map(s => <option key={s} value={s}>{s}</option>)}</select>
        <select aria-label="Filter mode" className={`${inputCls} max-w-40`} value={mode} onChange={e => set('mode', e.target.value)}><option value="">Live + replays</option><option value="live">Live only</option><option value="fixture_replay">Fixture replays</option></select></>} />
      <div className="boot p-6 max-sm:p-4"><Panel pad={false}>
        {loading && !data ? <Loading /> : data?.runs.length ? <RunTable runs={data.runs} /> : <Empty title="No runs match">Run a workflow from the Studio, or clear the filters.</Empty>}
      </Panel>{data && <div className="mt-2 font-mono text-[11px] text-faint">{data.total} run{data.total === 1 ? '' : 's'}</div>}</div>
    </>
  )
}
