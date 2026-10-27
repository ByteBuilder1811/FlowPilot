import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Download, FlaskConical, Play, Trash2, Upload, Zap } from 'lucide-react'
import { api } from '../api'
import { useAsync, fmtDur } from '../hooks'
import { DeterminismCheck } from '../components/Differentiators'
import { Button, Empty, ErrorState, Field, inputCls, Json, Loading, PageHeader, Panel, StatusBadge, SysLabel, Tag, statusColor } from '../components/ui'

function CaseDetail({ id, onChanged, versions }: { id: string; onChanged: () => void; versions: any[] }) {
  const c = useAsync(() => api.get(`/api/lab/cases/${id}`), [id])
  const meta = useAsync(() => api.get('/api/meta'), [])
  const [version, setVersion] = useState('')
  const [fault, setFault] = useState({ on: false, tool: '', kind: 'rate_limit', times: 1 })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [last, setLast] = useState<any>(null)
  const [exp, setExp] = useState<any>(null)
  useEffect(() => { setLast(null); setMsg(null); setExp(null) }, [id])
  if (c.error) return <ErrorState error={c.error} retry={c.reload} />
  if (!c.data) return <Loading />
  const d = c.data
  const expect = exp ?? d.expect
  const tools = [...new Set(d.fixtures.map((f: any) => f.tool))] as string[]

  async function replay() {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post(`/api/lab/cases/${id}/replay`, { version_id: version || undefined, faults: fault.on ? [{ tool: fault.tool || undefined, kind: fault.kind, times: Number(fault.times) }] : undefined })
      setLast(r); c.reload(true); onChanged()
    } catch (e: any) { setMsg({ kind: 'err', text: e.message }) } finally { setBusy(false) }
  }
  async function saveExpect() { await api.patch(`/api/lab/cases/${id}`, { expect }); setExp(null); setMsg({ kind: 'ok', text: 'Expectations saved' }); c.reload(true) }

  return (
    <div className="boot space-y-4 p-5 max-sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><SysLabel tone="violet">LAB // REGRESSION CASE</SysLabel><h2 className="font-display mt-1.5 text-[20px] font-semibold leading-tight tracking-tight">{d.name}</h2>
          <div className="mt-0.5 text-[12.5px] text-muted">Recorded from {d.source_run_id ? <Link className="mono text-accent hover:underline" to={`/runs/${d.source_run_id}`}>{d.source_run_id.slice(4, 14)}</Link> : 'import'} · {d.fixtures.length} fixtures · {d.approvals.length} recorded decisions</div></div>
        <div className="flex gap-1.5">
          <a href={`/api/lab/cases/${id}/export`} download={`${d.name}.case.json`}><Button size="sm"><Download className="size-3.5" />Export</Button></a>
          <Button size="sm" variant="danger" aria-label="Delete case" onClick={async () => { if (confirm('Delete this case and its results?')) { await api.del(`/api/lab/cases/${id}`); onChanged() } }}><Trash2 className="size-3.5" /></Button>
        </div>
      </div>
      <div className="hatch flex items-start gap-2.5 rounded-xl border border-accent/30 px-3.5 py-2.5 text-[12.5px] text-muted"><span className="mt-1 size-1.5 shrink-0 rounded-[2px] bg-accent shadow-[0_0_8px_var(--color-accent)]" aria-hidden /><span><b className="text-accent">Fixture replay</b> — uses the recorded tool/LLM results. Nothing real is called and no side effect is executed. Side-effect tools without a recorded result return a labelled <i>simulated</i> result.</span></div>

      <Panel title="Replay" hud="violet" actions={<SysLabel tone="faint">SIM // DETERMINISTIC</SysLabel>}>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Workflow version to test"><select className={inputCls} value={version} onChange={e => setVersion(e.target.value)} aria-label="Version to test"><option value="">Case version (v{d.version_id && versions.find(v => v.id === d.version_id)?.version_no})</option>{versions.map(v => <option key={v.id} value={v.id}>v{v.version_no}{v.note ? ` — ${v.note}` : ''}</option>)}</select></Field>
          <div>
            <label className={`mb-1 flex items-center gap-2 text-[11.5px] font-medium ${fault.on ? 'text-warn' : 'text-muted'}`}><input type="checkbox" checked={fault.on} onChange={e => setFault({ ...fault, on: e.target.checked })} /><Zap className={`size-3.5 ${fault.on ? 'drop-shadow-[0_0_6px_var(--color-warn)]' : ''}`} />Inject a fault</label>
            <div className={`grid grid-cols-3 gap-2 ${fault.on ? '' : 'pointer-events-none opacity-40'}`}>
              <select className={inputCls} aria-label="Fault tool" value={fault.tool} onChange={e => setFault({ ...fault, tool: e.target.value })}><option value="">any tool</option>{tools.map(t => <option key={t}>{t}</option>)}</select>
              <select className={inputCls} aria-label="Fault kind" value={fault.kind} onChange={e => setFault({ ...fault, kind: e.target.value })}>{(meta.data?.fault_kinds ?? []).map((k: string) => <option key={k}>{k}</option>)}</select>
              <input className={inputCls} type="number" min={1} max={10} aria-label="Fault times" value={fault.times} onChange={e => setFault({ ...fault, times: Number(e.target.value) })} />
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3"><Button variant="primary" busy={busy} onClick={replay} data-testid="replay"><Play className="size-4" />Replay case</Button>
          {msg && <span role="status" className={msg.kind === 'ok' ? 'text-ok' : 'text-bad'}>{msg.text}</span>}</div>
        {last && <div className="fade-in relative mt-3 overflow-hidden rounded-xl border border-line bg-ink/80 py-3 pl-4 pr-3" data-testid="replay-result"><span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: statusColor(last.status), boxShadow: `0 0 12px ${statusColor(last.status)}` }} aria-hidden /><div className="flex flex-wrap items-center gap-2"><StatusBadge status={last.status} /><span className="tabular font-mono text-[11.5px] text-muted">{fmtDur(last.metrics.duration_ms)} · {last.metrics.steps} steps · {last.metrics.retries} retries</span><Link className="ml-auto text-accent hover:underline" to={`/runs/${last.run_id}`}>Open replay run</Link></div>
          <ul className="mt-2.5 space-y-1">{last.checks.map((k: any, i: number) => <li key={i} className="flex flex-wrap gap-2 text-[12.5px]"><span className={`grid size-4 place-items-center rounded font-mono text-[10px] font-bold ${k.passed === null ? 'bg-warn/15 text-warn' : k.passed ? 'bg-ok/15 text-ok' : 'bg-bad/15 text-bad'}`}>{k.passed === null ? '?' : k.passed ? '✓' : '✗'}</span><span>{k.name}</span>{k.expected !== undefined && <span className="text-muted">expected <span className="mono">{String(k.expected)}</span>, got <span className="mono">{String(k.actual)}</span></span>}{k.detail && <span className="text-muted">{k.detail}</span>}</li>)}</ul></div>}
      </Panel>

      <DeterminismCheck caseId={id} />

      <Panel title="Expectations" actions={exp && <Button size="sm" variant="primary" onClick={saveExpect}>Save</Button>}>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Terminal status"><select className={inputCls} aria-label="Expected status" value={expect.status ?? ''} onChange={e => setExp({ ...expect, status: e.target.value })}>{['succeeded', 'failed', 'cancelled', 'timed_out'].map(s => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Output contains"><input className={inputCls} value={expect.output_contains ?? ''} onChange={e => setExp({ ...expect, output_contains: e.target.value || null })} /></Field>
          <Field label="Max retries"><input type="number" className={inputCls} value={expect.max_retries ?? ''} onChange={e => setExp({ ...expect, max_retries: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
          <Field label="Approvals during replay"><select className={inputCls} value={expect.approval_default ?? 'approve'} onChange={e => setExp({ ...expect, approval_default: e.target.value })}><option value="approve">Approve unrecorded gates</option><option value="reject">Reject unrecorded gates</option></select></Field>
          <label className="flex items-center gap-2 self-end pb-2 text-[12.5px]"><input type="checkbox" checked={expect.no_unapproved_side_effects !== false} onChange={e => setExp({ ...expect, no_unapproved_side_effects: e.target.checked })} />No unapproved side effects</label>
        </div>
      </Panel>

      <Panel title="Result history" pad={false}>
        {d.results.length ? <table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2">Version</th><th>Result</th><th>Checks</th><th>Retries</th><th>Duration</th><th /></tr></thead><tbody>
          {d.results.map((r: any) => <tr key={r.id} className="border-b border-line/60 last:border-0"><td className="px-4 py-2">v{r.version_no}</td><td><StatusBadge status={r.status} /></td>
            <td className="text-muted">{r.checks.filter((k: any) => k.passed).length}/{r.checks.length} passed</td><td className="tabular text-muted">{r.metrics.retries}</td><td className="tabular text-muted">{fmtDur(r.metrics.duration_ms)}</td><td className="px-4 text-right"><Link className="text-accent hover:underline" to={`/runs/${r.run_id}`}>run</Link></td></tr>)}</tbody></table>
          : <Empty title="Not replayed yet">Press “Replay case” to run it against a version.</Empty>}
      </Panel>
      <details><summary className="cursor-pointer font-mono text-[11px] uppercase tracking-[.1em] text-muted hover:text-fg">Recorded fixtures ({d.fixtures.length}) — sanitized</summary><div className="mt-2"><Json value={d.fixtures} max={300} /></div></details>
    </div>
  )
}

export default function Lab() {
  const [sp, setSp] = useSearchParams()
  const cases = useAsync(() => api.get('/api/lab/cases'), [], 4000)
  const wfs = useAsync(() => api.get('/api/workflows'), [])
  const sel = sp.get('case')
  const file = useRef<HTMLInputElement>(null)
  const [importWf, setImportWf] = useState('')
  const [msg, setMsg] = useState('')
  const wfId = cases.data?.find((c: any) => c.id === sel)?.workflow_id
  const wf = useAsync(() => (wfId ? api.get(`/api/workflows/${wfId}`) : Promise.resolve(null)), [wfId])
  useEffect(() => { if (!sel && cases.data?.length) setSp({ case: cases.data[0].id }, { replace: true }) }, [cases.data, sel, setSp])
  async function doImport(f: File) {
    try { const r = await api.post('/api/lab/cases/import', { workflow_id: importWf || wfs.data?.[0]?.id, data: JSON.parse(await f.text()) }); cases.reload(true); setSp({ case: r.id }); setMsg('') } catch (e: any) { setMsg(`Import failed: ${e.message}`) }
  }
  if (cases.error) return <ErrorState error={cases.error} retry={cases.reload} />
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Failure Lab" subtitle="Turn failures into deterministic regression cases, inject faults, and test workflow versions against them." actions={<>
        <input ref={file} type="file" hidden accept="application/json" onChange={e => e.target.files?.[0] && doImport(e.target.files[0])} />
        <select className={`${inputCls} max-w-52`} aria-label="Import into workflow" value={importWf} onChange={e => setImportWf(e.target.value)}><option value="">Import into…</option>{(wfs.data ?? []).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
        <Button onClick={() => file.current?.click()}><Upload className="size-4" />Import case</Button></>} />
      {msg && <div role="alert" className="mx-6 mt-3 rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-bad">{msg}</div>}
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        <aside className="glass-flat w-[320px] shrink-0 overflow-auto border-r border-line max-md:max-h-[40vh] max-md:w-full max-md:border-b max-md:border-r-0" aria-label="Cases">
          {cases.loading && !cases.data ? <Loading /> : !cases.data?.length ? <Empty icon={<FlaskConical className="size-7" />} title="No regression cases">Open a finished run and choose “Save as regression case”. Failed runs make the best cases.</Empty> :
            cases.data.map((c: any) => (
              <button key={c.id} onClick={() => setSp({ case: c.id })} data-testid="case-item" className={`relative block w-full border-b border-line/60 py-3 pl-5 pr-4 text-left transition-colors hover:bg-white/[.04] ${sel === c.id ? 'bg-gradient-to-r from-accent/15 to-transparent' : ''}`}>
                <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full" style={{ background: statusColor(c.last_result?.status), boxShadow: c.last_result ? `0 0 8px ${statusColor(c.last_result.status)}` : undefined }} aria-hidden />
                <div className="flex items-center justify-between gap-2"><span className="truncate font-medium">{c.name}</span>{c.last_result ? <StatusBadge status={c.last_result.status} /> : <Tag>never run</Tag>}</div>
                <div className="mt-1 truncate font-mono text-[11px] text-muted">{c.workflow_name} · v{c.version_no} · {c.fixture_count} fixtures</div>
              </button>))}
        </aside>
        <div className="min-w-0 flex-1 overflow-auto">{sel && cases.data?.some((c: any) => c.id === sel) ? <CaseDetail key={sel} id={sel} versions={wf.data?.versions ?? []} onChanged={() => cases.reload(true)} /> : <Empty title="Select a case" />}</div>
      </div>
    </div>
  )
}
