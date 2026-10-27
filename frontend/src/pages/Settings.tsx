import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, ErrorState, Field, inputCls, Loading, PageHeader, Panel } from '../components/ui'

function AuditPanel() {
  const a = useAsync(() => api.get('/api/audit?limit=25'), [], 6000)
  return (
    <Panel title="Audit log" pad={false}>
      {!a.data ? <div className="p-4 text-muted">Loading…</div> : !a.data.entries.length ? <div className="p-4 text-muted">No admin actions recorded yet.</div> : (
        <table className="w-full text-left text-[12.5px]" data-testid="audit"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2">When</th><th>Action</th><th>Status</th></tr></thead><tbody>
          {a.data.entries.map((e: any) => <tr key={e.id} className="border-b border-line/60 last:border-0"><td className="px-4 py-1.5 text-muted">{new Date(e.ts).toLocaleString()}</td><td className="mono">{e.method} {e.path}</td><td className="tabular">{e.status}</td></tr>)}</tbody></table>)}
      <div className="border-t border-line px-4 py-2 text-[11.5px] text-faint">Records method, path and status of authenticated admin actions. Request bodies are never stored.</div>
    </Panel>
  )
}

export default function SettingsPage() {
  const s = useAsync(() => api.get('/api/settings'), [])
  const meta = useAsync(() => api.get('/api/meta'), [])
  const [f, setF] = useState<any>(null)
  const [msg, setMsg] = useState<{ k: 'ok' | 'err'; t: string } | null>(null)
  useEffect(() => { if (s.data) setF(s.data) }, [s.data])
  if (s.error) return <ErrorState error={s.error} retry={s.reload} />
  if (!f || !meta.data) return <Loading />
  const L = meta.data.limits
  async function save() { try { await api.put('/api/settings', f); setMsg({ k: 'ok', t: 'Saved' }); s.reload(true) } catch (e: any) { setMsg({ k: 'err', t: e.message }) } }
  const num = (k: string, label: string, max: number, hint: string) => <Field label={label} hint={`${hint} (hard limit ${max})`}><input type="number" min={1} max={max} className={inputCls} aria-label={label} value={f[k]} onChange={e => setF({ ...f, [k]: Number(e.target.value) })} /></Field>
  return (
    <>
      <PageHeader title="Settings & execution policies" subtitle="Workspace-wide limits. Individual workflows can only be stricter, never looser." />
      <div className="boot max-w-3xl space-y-5 p-6 max-sm:p-4">
        <Panel title="Execution budgets"><div className="grid gap-4 md:grid-cols-2">
          {num('max_steps', 'Max steps per run', L.max_steps, 'Stops runaway graphs')}{num('max_run_seconds', 'Max active seconds per run', 3600, 'Time spent waiting for approval is not counted')}
          {num('max_agent_iterations', 'Default agent iterations', L.max_agent_iterations, 'Tool-use loops per LLM node')}{num('loop_repeat_threshold', 'Loop detection threshold', 20, 'Identical tool calls within one agent step')}</div></Panel>
        <Panel title="Safety policy">
          <label className="flex items-start gap-2.5"><input type="checkbox" className="mt-1" checked={f.require_approval_for_side_effects} onChange={e => setF({ ...f, require_approval_for_side_effects: e.target.checked })} />
            <span><span className="font-medium">Require approval for every side-effecting tool</span><span className="block text-muted">Enforced by the backend at execution time. Turning it off lets workflows send emails, charge cards and call non-GET endpoints without a human decision.</span></span></label>
          {!f.require_approval_for_side_effects && <div role="alert" className="mt-3 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-warn">Approval gating is OFF. Nodes may still opt in with “Always require human approval”.</div>}
        </Panel>
        <div className="flex items-center gap-3"><Button variant="primary" onClick={save} data-testid="save-settings">Save settings</Button>{msg && <span role="status" className={msg.k === 'ok' ? 'text-ok' : 'text-bad'}>{msg.t}</span>}</div>
        <AuditPanel />
        <Panel title="Deployment & security notes"><ul className="list-disc space-y-1 pl-5 text-[12.5px] text-muted"><li>Local single-user mode: state-changing API calls need a per-install admin token that is only handed to local requests.</li><li>Secrets are never stored in the database or returned by the API; logs, events, fixtures and exports are redacted.</li><li>This build is not hardened for multi-user or internet-facing deployment.</li></ul></Panel>
      </div>
    </>
  )
}
