import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Gauge, Plus, Play, Trash2 } from 'lucide-react'
import { api } from '../api'
import { useAsync, fmtDur } from '../hooks'
import { Button, Empty, ErrorState, Field, inputCls, Json, Loading, PageHeader, Panel, StatusBadge, SysLabel, Tag } from '../components/ui'

const SCORERS = [['exact', 'Exact — output equals expected.output'], ['contains', 'Contains — expected.contains'], ['regex', 'Regex — expected.regex'], ['json_subset', 'JSON subset — expected.json'], ['judge', 'Judge — rubric-based score']]
const SAMPLE = '[\n  { "input": { "topic": "idempotency" }, "expected": { "contains": "idempotency" } }\n]'

function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const ds = useAsync(() => api.get(`/api/evals/datasets/${id}`), [id])
  const wf = useAsync(async () => { const d = await api.get(`/api/evals/datasets/${id}`); return api.get(`/api/workflows/${d.workflow_id}`) }, [id])
  const [rowsText, setRowsText] = useState(SAMPLE)
  const [version, setVersion] = useState('')
  const [scorers, setScorers] = useState<string[]>(['contains'])
  const [judge, setJudge] = useState({ provider: 'mock', model: '', threshold: 0.6 })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ k: 'ok' | 'err'; t: string } | null>(null)
  const [result, setResult] = useState<any>(null)
  if (ds.error) return <ErrorState error={ds.error} retry={ds.reload} />
  if (!ds.data || !wf.data) return <Loading />
  const d = ds.data
  const versions = wf.data.versions ?? []
  async function addRows() {
    try { await api.post(`/api/evals/datasets/${id}/rows`, { rows: JSON.parse(rowsText) }); setMsg({ k: 'ok', t: 'Rows added' }); ds.reload(true); onChanged() } catch (e: any) { setMsg({ k: 'err', t: e instanceof SyntaxError ? 'Rows must be a JSON array' : e.message }) }
  }
  async function run() {
    setBusy(true); setMsg(null); setResult(null)
    try {
      const r = await api.post('/api/evals/run', { dataset_id: id, version_id: version || versions[0]?.id, scorers, judge: scorers.includes('judge') ? judge : null })
      setResult(await api.get(`/api/evals/runs/${r.id}`)); ds.reload(true); onChanged()
    } catch (e: any) { setMsg({ k: 'err', t: e.message }) } finally { setBusy(false) }
  }
  return (
    <div className="space-y-4 p-5">
      <div className="flex items-start justify-between"><div><SysLabel tone="violet">EVAL // DATASET</SysLabel><h2 className="font-display mt-1 text-[18px] font-semibold">{d.name}</h2>
        <p className="text-[12.5px] text-muted">{d.rows.length} rows · runs the workflow <b>live</b> for each row, then scores the output. Rows that reach an approval gate are reported as <i>blocked</i>; evals never bypass approvals.</p></div>
        <Button variant="danger" size="sm" aria-label="Delete dataset" onClick={async () => { if (confirm('Delete this dataset and its results?')) { await api.del(`/api/evals/datasets/${id}`); onChanged() } }}><Trash2 className="size-3.5" /></Button></div>

      <Panel title={`Rows (${d.rows.length})`}>
        {d.rows.length > 0 && <ul className="mb-3 max-h-40 space-y-1 overflow-auto">{d.rows.map((r: any) => <li key={r.id} className="mono truncate text-[11.5px] text-muted">{JSON.stringify(r.input)} → {JSON.stringify(r.expected)}</li>)}</ul>}
        <Field label="Add rows (JSON array of {input, expected})" hint="expected keys: output, contains, regex, json, rubric"><textarea className={`${inputCls} mono`} rows={5} value={rowsText} onChange={e => setRowsText(e.target.value)} aria-label="Rows JSON" /></Field>
        <div className="mt-2 flex items-center gap-3"><Button onClick={addRows} data-testid="add-rows"><Plus className="size-4" />Add rows</Button>{msg && <span role="status" className={msg.k === 'ok' ? 'text-ok' : 'text-bad'}>{msg.t}</span>}</div>
      </Panel>

      <Panel title="Run eval">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Workflow version"><select className={inputCls} aria-label="Eval version" value={version} onChange={e => setVersion(e.target.value)}>{versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version_no}{v.note ? ` — ${v.note}` : ''}</option>)}</select></Field>
          <div><div className="mb-1 text-[11.5px] font-medium text-muted">Scorers</div>{SCORERS.map(([k, l]) => <label key={k} className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={scorers.includes(k)} onChange={e => setScorers(e.target.checked ? [...scorers, k] : scorers.filter(x => x !== k))} aria-label={`Scorer ${k}`} />{l}</label>)}</div>
        </div>
        {scorers.includes('judge') && <div className="mt-3 grid gap-3 md:grid-cols-3">
          <Field label="Judge provider"><select className={inputCls} value={judge.provider} onChange={e => setJudge({ ...judge, provider: e.target.value })}><option value="mock">Mock (keyword heuristic — not a real judge)</option><option value="anthropic">Anthropic</option></select></Field>
          <Field label="Judge model"><input className={inputCls} value={judge.model} onChange={e => setJudge({ ...judge, model: e.target.value })} placeholder="claude-haiku-5-5" /></Field>
          <Field label="Pass threshold (0–1)"><input type="number" step="0.05" min={0} max={1} className={inputCls} value={judge.threshold} onChange={e => setJudge({ ...judge, threshold: Number(e.target.value) })} /></Field></div>}
        <div className="mt-3"><Button variant="primary" busy={busy} disabled={!d.rows.length || !scorers.length || !versions.length} onClick={run} data-testid="run-eval"><Play className="size-4" />Run eval</Button></div>
      </Panel>

      {result && <Panel title="Results" hud="violet" actions={<SysLabel tone="faint">v{result.version_no}</SysLabel>}>
        <div data-testid="eval-summary" className="mb-3 flex flex-wrap gap-2">{(['passed', 'failed', 'errored', 'blocked'] as const).map(k => <Tag key={k} tone={k === 'passed' ? 'ok' : k === 'failed' ? 'bad' : 'warn'}>{result.summary[k]} {k}</Tag>)}<span className="text-[12px] text-muted">sample size {result.summary.rows} — inspect rows; no single aggregate score</span></div>
        <ul className="divide-y divide-line/60" data-testid="eval-rows">{result.results.map((r: any) => (
          <li key={r.row_id} className="py-2.5"><div className="flex flex-wrap items-center gap-2"><StatusBadge status={r.status === 'passed' ? 'succeeded' : r.status === 'failed' ? 'failed' : r.status === 'blocked' ? 'waiting_for_approval' : 'timed_out'} />
            <span className="mono text-[12px] text-muted">{JSON.stringify(r.input)}</span><span className="ml-auto text-[12px] text-faint">{fmtDur(r.duration_ms)}</span>{r.run_id && <Link className="text-[12px] text-accent hover:underline" to={`/runs/${r.run_id}`}>run</Link>}</div>
            <ul className="mt-1 space-y-0.5">{r.scores.map((s: any, i: number) => <li key={i} className="text-[12.5px]"><span className={s.passed ? 'text-ok' : 'text-bad'}>{s.passed ? '✓' : '✗'}</span> {s.scorer}{s.score != null && <span className="tabular text-muted"> {s.score}</span>} <span className="text-muted">· {s.detail}</span></li>)}</ul></li>))}</ul>
      </Panel>}

      <Panel title="History" pad={false}>
        {d.runs.length ? <table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2">Version</th><th>Scorers</th><th>Passed</th><th>Failed</th><th>Blocked/err</th></tr></thead><tbody>
          {d.runs.map((r: any) => <tr key={r.id} className="border-b border-line/60 last:border-0"><td className="px-4 py-2">v{r.version_no}</td><td className="text-muted">{r.scorers.join(', ')}</td><td className="tabular text-ok">{r.summary.passed}/{r.summary.rows}</td><td className="tabular text-bad">{r.summary.failed}</td><td className="tabular text-warn">{r.summary.blocked + r.summary.errored}</td></tr>)}</tbody></table> : <Empty title="No eval runs yet">Run the dataset against a version to see results here.</Empty>}
      </Panel>
    </div>
  )
}

export default function Evals() {
  const list = useAsync(() => api.get('/api/evals/datasets'), [])
  const wfs = useAsync(() => api.get('/api/workflows'), [])
  const [sel, setSel] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [wid, setWid] = useState('')
  const [err, setErr] = useState('')
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />
  async function create() { setErr(''); try { const r = await api.post('/api/evals/datasets', { name: name.trim(), workflow_id: wid || wfs.data?.[0]?.id }); setName(''); await list.reload(true); setSel(r.id) } catch (e: any) { setErr(e.message) } }
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Evals" subtitle="Score workflow outputs against datasets — exact, contains, regex, JSON subset, or a rubric judge." />
      <div className="flex min-h-0 flex-1">
        <aside className="w-[310px] shrink-0 overflow-auto border-r border-line">
          <form className="space-y-2 border-b border-line p-3" onSubmit={e => { e.preventDefault(); create() }}>
            <input className={inputCls} value={name} onChange={e => setName(e.target.value)} placeholder="New dataset name" aria-label="Dataset name" />
            <div className="flex gap-2"><select className={inputCls} value={wid} onChange={e => setWid(e.target.value)} aria-label="Dataset workflow">{(wfs.data ?? []).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
              <Button type="submit" variant="primary" disabled={!name.trim() || !wfs.data?.length} data-testid="create-dataset"><Plus className="size-4" /></Button></div>
            {err && <div role="alert" className="text-[12.5px] text-bad">{err}</div>}
          </form>
          {!list.data ? <Loading /> : !list.data.length ? <Empty icon={<Gauge className="size-7" />} title="No datasets">Create one for a workflow, add rows, and score it.</Empty> :
            list.data.map((d: any) => <button key={d.id} onClick={() => setSel(d.id)} className={`block w-full border-b border-line/60 px-4 py-3 text-left hover:bg-raised/60 ${sel === d.id ? 'bg-accent/10' : ''}`}><div className="font-medium">{d.name}</div><div className="truncate text-[12px] text-muted">{d.workflow_name} · {d.rows} rows{d.last_run ? ` · last ${d.last_run.summary.passed}/${d.last_run.summary.rows} passed` : ''}</div></button>)}
        </aside>
        <div className="min-w-0 flex-1 overflow-auto">{sel && list.data?.some((d: any) => d.id === sel) ? <Detail key={sel} id={sel} onChanged={() => list.reload(true)} /> : <Empty title="Select a dataset" />}</div>
      </div>
    </div>
  )
}
