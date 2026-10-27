import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Fingerprint, Radar, ScrollText } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, Panel, StatusBadge, SysLabel, Tag } from './ui'

const STATE: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' | 'accent' | 'neutral' }> = {
  executed: { label: 'executed', tone: 'ok' }, unknown: { label: 'outcome unknown', tone: 'warn' }, reconciled: { label: 'reconciled by operator', tone: 'accent' },
  simulated: { label: 'simulated (replay)', tone: 'neutral' }, not_executed: { label: 'did not execute', tone: 'neutral' },
}

/** What did this run actually do to the outside world? */
export function EffectsLedger({ runId, active }: { runId: string; active: boolean }) {
  const q = useAsync(() => api.get(`/api/runs/${runId}/effects`), [runId], active ? 2000 : 0)
  const d = q.data
  return (
    <Panel title={<span className="flex items-center gap-2"><ScrollText className="size-3.5" />Side-effect ledger</span>} hud={d?.needs_attention ? 'warn' : 'signal'}
      actions={d && <SysLabel tone={d.needs_attention ? 'warn' : d.counts.executed ? 'signal' : 'faint'}>{d.needs_attention ? 'SYS // ATTENTION' : 'SYS // AUDIT'}</SysLabel>}>
      <div data-testid="effects-ledger">
        {!d ? <div className="text-muted">Loading…</div> : !d.entries.length ? <p className="text-[12.5px] text-muted">No external side effects have been attempted by this run.</p> : <>
          <div className="mb-2 flex flex-wrap gap-2 text-[12px]">{Object.entries(d.counts).filter(([, n]) => (n as number) > 0).map(([k, n]) => <Tag key={k} tone={STATE[k].tone}>{n as number} {STATE[k].label}</Tag>)}
            {d.unapproved > 0 && <Tag tone="bad">{d.unapproved} without approval</Tag>}</div>
          <ul className="divide-y divide-line/60">{d.entries.map((e: any, i: number) => (
            <li key={i} className="flex flex-wrap items-center gap-2 py-1.5 text-[12.5px]"><span className="mono">{e.tool}</span><span className="text-muted">→ {e.target ?? '—'}</span>
              <Tag tone={STATE[e.state].tone}>{STATE[e.state].label}</Tag><span className="text-faint">attempt {e.attempt}</span>
              {e.approval_id ? <Tag tone="ok">approved</Tag> : <Tag tone="bad">no approval</Tag>}{e.idempotency_key && <Tag>idem key</Tag>}</li>))}</ul>
          {d.needs_attention && <p className="mt-2 text-[12px] text-warn">At least one action may have happened without a recorded result. FlowPilot will not repeat it until you reconcile.</p>}
        </>}
      </div>
    </Panel>
  )
}

/** Replay a case N times and prove (or disprove) that it is deterministic. */
export function DeterminismCheck({ caseId }: { caseId: string }) {
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState('')
  async function go() { setBusy(true); setErr(''); setRes(null); try { setRes(await api.post(`/api/lab/cases/${caseId}/determinism`, { runs: 5 })) } catch (e: any) { setErr(e.message) } finally { setBusy(false) } }
  return (
    <Panel title={<span className="flex items-center gap-2"><Fingerprint className="size-3.5" />Determinism check</span>} actions={<SysLabel tone="faint">PROOF // 5×</SysLabel>}>
      <p className="text-[12.5px] text-muted">Replays this case 5 times and compares a fingerprint of every step, tool result and output (ids and timestamps excluded). A flaky case is worthless as a regression test.</p>
      <div className="mt-3 flex items-center gap-3"><Button busy={busy} onClick={go} data-testid="determinism-go">Run 5× replay</Button>
        {res && <span data-testid="determinism-result" className={res.identical ? 'text-ok' : 'text-bad'}>{res.identical ? `Deterministic — 5 identical runs (fingerprint ${res.fingerprints[0]})` : `NOT deterministic — ${res.distinct_fingerprints} distinct outcomes`}</span>}
        {err && <span role="alert" className="text-bad">{err}</span>}</div>
    </Panel>
  )
}

/** Replay every case on every version to find exactly which saved version broke it. */
export function BreakFinder({ workflowId }: { workflowId: string }) {
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState('')
  async function go() { setBusy(true); setErr(''); setRes(null); try { setRes(await api.post('/api/lab/matrix', { workflow_id: workflowId })) } catch (e: any) { setErr(e.message) } finally { setBusy(false) } }
  return (
    <Panel title={<span className="flex items-center gap-2"><Radar className="size-3.5" />Find the breaking version</span>} hud="violet" actions={<SysLabel tone="faint">BISECT // ALL VERSIONS</SysLabel>}>
      <p className="text-[12.5px] text-muted">Replays every regression case against every saved version and names the first version where each case went from passing to failing, with the exact change.</p>
      <div className="mt-3"><Button busy={busy} onClick={go} data-testid="matrix-go"><Radar className="size-4" />Run version matrix</Button>{err && <span role="alert" className="ml-3 text-bad">{err}</span>}</div>
      {res && (
        <div className="mt-4 overflow-x-auto" data-testid="matrix">
          <table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="py-2 pr-3">Case</th>{res.versions.map((v: any) => <th key={v.id} className="px-2">v{v.version_no}</th>)}<th className="px-2">Verdict</th></tr></thead>
            <tbody>{res.cases.map((c: any) => (
              <tr key={c.case_id} className="border-b border-line/60 align-top"><td className="py-2 pr-3 font-medium">{c.name}</td>
                {res.versions.map((v: any) => <td key={v.id} className="px-2 py-2"><Link to={`/runs/${c.cells[v.id].run_id}`}><StatusBadge status={c.cells[v.id].status} /></Link></td>)}
                <td className="px-2 py-2">{c.regressed_in ? <div data-testid="culprit"><Tag tone="bad">broke in v{c.regressed_in.version_no}</Tag>
                  {c.regressed_in.diff.nodes_changed.map((n: any) => n.fields.map((f: any) => <div key={n.node_id + f.path} className="mono mt-1 text-[11.5px]"><span className="text-muted">{n.node_id}.{f.path}</span> <span className="text-bad line-through">{JSON.stringify(f.a)}</span> → <span className="text-ok">{JSON.stringify(f.b)}</span></div>))}</div> : <span className="text-muted">no pass→fail transition</span>}</td></tr>))}</tbody></table>
          {!res.cases.length && <p className="mt-2 text-muted">This workflow has no regression cases yet.</p>}
        </div>)}
    </Panel>
  )
}
