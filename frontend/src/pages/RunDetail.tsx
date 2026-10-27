import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AlertOctagon, Ban, Check, CircleCheck, CircleDot, CircleX, Flag, FlaskConical, History, Radio, Repeat, Rocket, RotateCcw, RotateCw, ShieldAlert, ShieldCheck, Sparkles, Timer, Wifi, WifiOff, Wrench, Dot } from 'lucide-react'
import { api } from '../api'
import { useAsync, fmtDur, runDuration } from '../hooks'
import { useRunStream, type RunEvent } from '../useRunStream'
import { Button, ErrorState, Field, inputCls, Json, Led, Loading, Meta, Panel, StatusBadge, SysLabel, Tag } from '../components/ui'
import { EffectsLedger } from '../components/Differentiators'
import RunGraph from '../components/RunGraph'
import { NODE_META } from '../components/nodeMeta'

const TONE: Record<string, string> = { 'run.succeeded': 'text-ok', 'run.failed': 'text-bad', 'run.timed_out': 'text-warn', 'run.cancelled': 'text-muted', 'step.failed': 'text-bad', 'tool.failed': 'text-bad', 'llm.failed': 'text-bad', 'retry.scheduled': 'text-warn', 'retry.blocked': 'text-warn', 'retry.declined': 'text-warn', 'approval.requested': 'text-accent', 'approval.decided': 'text-accent', 'loop.detected': 'text-bad', 'step.succeeded': 'text-ok', 'step.recovered': 'text-warn', 'run.recovered': 'text-warn' }

function glyph(t: string) {
  if (t === 'run.succeeded') return CircleCheck
  if (t.endsWith('.failed') || t === 'run.timed_out' || t === 'loop.detected') return CircleX
  if (t.startsWith('retry.')) return RotateCcw
  if (t.startsWith('approval.') || t.startsWith('reconcile.') || t === 'run.waiting_for_approval') return ShieldCheck
  if (t.startsWith('tool.')) return Wrench
  if (t.startsWith('llm.')) return Sparkles
  if (t.startsWith('delay.')) return Timer
  if (t.startsWith('loop.')) return Repeat
  if (t.includes('cancel')) return Ban
  if (t === 'run.output') return Flag
  if (t === 'step.succeeded') return Check
  if (t.startsWith('step.')) return CircleDot
  if (t.startsWith('run.')) return Rocket
  return Dot
}

function describe(e: RunEvent): string {
  const d = e.data ?? {}
  switch (e.type) {
    case 'run.queued': return `Queued (${d.mode === 'fixture_replay' ? 'fixture replay' : 'live'}, ${d.trigger})`
    case 'run.started': return `Started · workflow v${d.workflow_version}`
    case 'run.resumed': return 'Resumed from checkpoint (completed steps are not re-executed)'
    case 'run.queued_again': return 'Re-queued'
    case 'step.started': return `${d.label ?? e.node_id} started`
    case 'step.resumed': return `${e.node_id} resumed`
    case 'step.succeeded': return `${e.node_id} succeeded${d.attempts ? ` after ${d.attempts} failed attempt${d.attempts > 1 ? 's' : ''}` : ''}${d.handle && !['out', null].includes(d.handle) ? ` → ${d.handle}` : ''}`
    case 'step.failed': return `${e.node_id} failed: ${d.error?.message}`
    case 'step.recovered': return `${e.node_id} failed but was recovered via its error path`
    case 'tool.called': return `Call ${d.tool}${d.attempt > 1 ? ` (attempt ${d.attempt})` : ''}${d.side_effect ? ' · side effect' : ''}${d.idempotency_key ? ' · idempotency key' : ''}`
    case 'tool.succeeded': return `${d.tool} returned`
    case 'tool.failed': return `${d.tool} failed: ${d.error?.message}${d.error?.executed === null ? ' — OUTCOME UNKNOWN' : ''}`
    case 'llm.requested': return `LLM request → ${d.provider}/${d.model}`
    case 'llm.responded': return `${d.mock ? '[MOCK] ' : ''}LLM replied${d.tool_calls?.length ? ` with ${d.tool_calls.length} tool call(s)` : ''}`
    case 'llm.failed': return `LLM failed: ${d.error?.message}`
    case 'retry.scheduled': return `Retry ${d.next_attempt} of ${d.tool} in ${d.backoff_ms} ms${d.skipped_wait ? ' (wait skipped in replay)' : ''} — ${d.reason}`
    case 'retry.blocked': return `Automatic retry BLOCKED for ${d.tool}: ${d.reason}`
    case 'retry.declined': return `No retry for ${d.tool}: ${d.reason}`
    case 'approval.requested': return `Approval requested (${d.kind}) — run paused`
    case 'approval.replayed': return `Approval ${d.status} (replayed decision)`
    case 'approval.decided': return `Human ${d.decision === 'approve' ? 'approved' : 'rejected'}${d.action ? ` · ${d.action}` : ''}${d.note ? ` — “${d.note}”` : ''}`
    case 'reconcile.decided': return `Reconciliation: ${d.action}`
    case 'loop.detected': return `Loop detected (${d.signal})${d.tool ? `: ${d.tool} ×${d.repeats}` : ''}`
    case 'delay.started': return `Waiting ${d.seconds}s${d.skipped ? ' (skipped in replay)' : ''}`
    case 'child.started': return `Started child run #${d.index} (${String(d.child_run_id).slice(4, 14)})`
    case 'child.finished': return `All ${d.children} child run(s) finished${d.failed ? ` — ${d.failed} did not succeed` : ''}`
    case 'run.waiting_for_child': return 'Paused — waiting for child runs (worker released)'
    case 'run.waiting_for_approval': return 'Paused — waiting for a human decision'
    case 'run.retrying': return 'Retrying'
    case 'run.cancel_requested': return 'Cancellation requested'
    case 'run.cancel_acknowledged': return `Cancellation acknowledged${d.at_node ? ` at ${d.at_node}` : ''}`
    case 'run.cancelled': return 'Cancelled'
    case 'run.succeeded': return 'Run succeeded'
    case 'run.failed': return 'Run failed'
    case 'run.timed_out': return 'Run timed out'
    case 'run.claimed': return 'Picked up by worker'
    case 'run.output': return 'Output produced'
    case 'run.running': return d.from === 'retrying' ? 'Back to running' : 'Running'
    default: return e.type
  }
}

function ApprovalCard({ a, onDone }: { a: any; onDone: () => void }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const rec = a.kind === 'reconcile'
  async function decide(decision: 'approve' | 'reject', action?: string, output?: any) {
    setBusy(action ?? decision); setErr('')
    try { await api.post(`/api/approvals/${a.id}/decide`, { decision, note, action, output }); onDone() } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }
  const p = a.proposed ?? {}
  return (
    <div className={`glass hud hairline fade-in relative overflow-hidden rounded-2xl ${rec ? 'hud-warn hairline-warn shadow-[0_0_60px_-22px_rgba(251,191,36,.9)]' : 'hud-violet shadow-[0_0_60px_-22px_rgba(161,148,255,1)]'}`} data-testid="approval-card" role="region" aria-label="Approval request">
      <div className={`sweep ${rec ? '' : 'sweep-violet'} relative flex items-center gap-3 border-b px-4 py-3 ${rec ? 'border-warn/25 bg-warn/[.07]' : 'border-accent/25 bg-accent/[.08]'}`}>
        <span className={`relative grid size-10 shrink-0 place-items-center rounded-xl ${rec ? 'bg-warn/15 text-warn' : 'bg-accent/15 text-accent'}`}>
          <span className="signal-dot absolute inset-0 rounded-xl" aria-hidden />{rec ? <ShieldAlert className="size-5" /> : <ShieldCheck className="size-5" />}</span>
        <div className="relative min-w-0 flex-1">
          <SysLabel tone={rec ? 'warn' : 'violet'} live>{rec ? 'SYS // HOLD · outcome unverified' : 'SYS // HOLD · human gate'}</SysLabel>
          <div className={`font-display mt-1 text-[16px] font-semibold leading-snug ${rec ? 'text-warn' : 'text-fg'}`}>{rec ? 'Outcome unknown — reconcile before anything is repeated' : p.title ?? 'Approval required before a sensitive action'}</div>
        </div>
        <span className="relative max-sm:hidden"><Led on={5} color={rec ? 'var(--color-warn)' : 'var(--color-accent)'} chase /></span>
      </div>
      <div className="p-4">
      {rec ? <p className="text-[13px] text-muted">{p.problem}. {p.consequence}</p> : <p className="text-[13px] text-muted">{p.consequence ?? p.message}</p>}
      <dl className="mt-3 grid grid-cols-[100px_1fr] gap-x-3 gap-y-2 rounded-xl border border-line bg-ink/60 p-3 text-[12.5px]">
        {p.tool && <><dt className="kicker pt-0.5 text-[9.5px] text-faint">Action</dt><dd className="mono text-[12.5px] font-semibold text-fg">{p.tool}</dd></>}
        {p.target && <><dt className="kicker pt-0.5 text-[9.5px] text-faint">Target</dt><dd className="mono">{String(p.target)}</dd></>}
        {p.idempotency_key && <><dt className="kicker pt-0.5 text-[9.5px] text-faint">Idempotency</dt><dd className="mono">{p.idempotency_key}</dd></>}
        {(p.arguments || p.args) && <><dt className="kicker pt-0.5 text-[9.5px] text-faint">Arguments</dt><dd className="min-w-0"><Json value={p.arguments ?? p.args} max={140} /></dd></>}
        {p.details && Object.keys(p.details).length > 0 && <><dt className="kicker pt-0.5 text-[9.5px] text-faint">Details</dt><dd className="min-w-0"><Json value={p.details} max={140} /></dd></>}
      </dl>
      <div className="mt-3"><Field label="Note (recorded with the decision)"><input className={inputCls} value={note} onChange={e => setNote(e.target.value)} aria-label="Decision note" /></Field></div>
      {err && <div role="alert" className="mt-2 text-[12.5px] text-bad">{err}</div>}
      <div className="mt-4 flex flex-wrap gap-2">
        {rec ? <>
          <Button variant="primary" busy={busy === 'mark_done'} onClick={() => decide('approve', 'mark_done', { reconciled: true, note: note || 'confirmed by operator' })}>It did happen — mark done</Button>
          <Button busy={busy === 'retry'} onClick={() => decide('approve', 'retry')}>Retry (may duplicate)</Button>
          <Button variant="danger" busy={busy === 'fail'} onClick={() => decide('reject', 'fail')}>Fail the run</Button>
        </> : <>
          <Button variant="primary" busy={busy === 'approve'} onClick={() => decide('approve')} data-testid="approve">Approve</Button>
          <Button variant="danger" busy={busy === 'reject'} onClick={() => decide('reject')} data-testid="reject">Reject</Button>
        </>}
      </div>
      </div>
    </div>
  )
}

export default function RunDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const run = useAsync(() => api.get(`/api/runs/${id}`), [id])
  const stream = useRunStream(id!, useCallback(() => run.reload(true), [run.reload])) // eslint-disable-line
  const [sel, setSel] = useState<string | null>(null)
  const [tab, setTab] = useState<'timeline' | 'steps'>('timeline')
  const [toast, setToast] = useState('')
  const [caseOpen, setCaseOpen] = useState(false)
  const [now, setNow] = useState(Date.now())
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [])
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest' }) }, [stream.events.length])
  const r = run.data
  const active = r && !['succeeded', 'failed', 'cancelled', 'timed_out'].includes(r.status)
  const elapsed = useMemo(() => r ? runDuration({ started_at: r.started_at, completed_at: r.completed_at }) : null, [r, now]) // eslint-disable-line

  if (run.error) return <ErrorState error={run.error} retry={run.reload} />
  if (!r) return <Loading />
  const pending = r.approvals.filter((a: any) => a.status === 'pending')
  const selStep = r.steps.filter((s: any) => s.node_id === sel)
  const selCalls = r.tool_calls.filter((c: any) => c.node_id === sel)
  const t0 = stream.events[0] ? new Date(stream.events[0].ts).getTime() : 0

  async function act(fn: () => Promise<any>, then?: (x: any) => void) { try { const x = await fn(); then?.(x) } catch (e: any) { setToast(e.message); setTimeout(() => setToast(''), 4000) } }
  const replayMode = r.mode === 'fixture_replay'

  return (
    <div className="flex h-full flex-col">
      <header className="glass-flat relative border-b border-line px-6 pb-3 pt-4 max-sm:px-4">
        {active && <div className="scanline absolute inset-x-0 bottom-0 h-px bg-signal/20" aria-hidden />}
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              {active ? <SysLabel tone="signal" live>SYS // RUN IN FLIGHT</SysLabel> : <SysLabel tone={r.status === 'succeeded' ? 'ok' : r.status === 'failed' || r.status === 'timed_out' ? 'bad' : 'faint'}>SYS // RUN LANDED</SysLabel>}
              <span className="mono text-[11px] text-faint">{r.id}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2.5"><h1 className="font-display truncate text-[22px] font-semibold leading-tight tracking-tight">{r.workflow_name}</h1><StatusBadge status={r.status} size="lg" />
              {replayMode && <Tag tone="accent">fixture replay — not a new real-world execution</Tag>}{r.trigger === 'webhook' && <Tag>webhook</Tag>}</div>
            <div className="mono mt-1 text-[11.5px] text-faint"><Link to={`/workflows/${r.workflow_id}`} className="hover:text-accent">workflow v{r.version_no}</Link>{r.source_run_id && <> · from <Link className="hover:text-accent" to={`/runs/${r.source_run_id}`}>{r.source_run_id.slice(4, 14)}</Link></>}</div></div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {active && <Button variant="danger" data-testid="cancel" onClick={() => act(() => api.post(`/api/runs/${id}/cancel`))}><Ban className="size-4" />{r.cancel_requested ? 'Cancelling…' : 'Cancel run'}</Button>}
            {!active && <>
              <Button onClick={() => act(() => api.post(`/api/runs/${id}/replay`), x => nav(`/runs/${x.run_id}`))} title="Re-run using recorded tool/LLM results. Nothing real is executed."><History className="size-4" />Fixture replay</Button>
              {!replayMode && <Button onClick={() => { if (confirm('Live rerun calls real tools and providers again and may cause external side effects. Continue?')) act(() => api.post(`/api/runs/${id}/rerun`), x => nav(`/runs/${x.run_id}`)) }} title="Calls tools again. May differ and may have side effects."><RotateCw className="size-4" />Live rerun</Button>}
              {!replayMode && <Button variant="primary" onClick={() => setCaseOpen(true)} data-testid="make-case"><FlaskConical className="size-4" />Save as regression case</Button>}
            </>}
          </div>
        </div>
        <div className="mt-3.5 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
          {[
            ['Elapsed', <span className={`tabular font-mono text-[15px] font-medium ${active ? 'text-signal' : ''}`}>{fmtDur(elapsed)}</span>],
            ['Current node', r.current_node ? <span className="mono text-[13px]">{r.current_node}</span> : <span className="text-faint">—</span>],
            ['Steps', <span className="tabular font-mono text-[15px] font-medium">{r.step_count}</span>],
            ['Tool / LLM calls', <span className="tabular font-mono text-[15px] font-medium">{r.usage.tool_calls ?? 0} / {r.usage.llm_calls ?? 0}</span>],
            ['Retries', <span className={`tabular font-mono text-[15px] font-medium ${r.usage.retries ? 'text-warn' : ''}`}>{r.usage.retries ?? 0}</span>],
            ['Tokens · cost', r.usage.input_tokens != null ? <span className="tabular font-mono">{r.usage.input_tokens}↑ {r.usage.output_tokens}↓ · cost n/a</span> : <span className="text-faint" title="Mock providers report no reliable token data; no pricing source is configured.">unavailable</span>],
          ].map(([k, v]) => <div key={k as string} className="bg-[#0c0f18] px-3.5 py-2.5"><Meta k={k as string} v={v} /></div>)}
        </div>
      </header>
      {toast && <div role="alert" className="mx-6 mt-3 rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-bad">{toast}</div>}
      <div className="boot grid min-h-0 flex-1 gap-4 overflow-auto p-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,400px)] 2xl:grid-cols-[minmax(0,1fr)_minmax(400px,480px)]">
        <div className="flex min-w-0 flex-col gap-4">
          {pending.map((a: any) => <ApprovalCard key={a.id} a={a} onDone={() => run.reload(true)} />)}
          {r.error && (
            <div className="glass hud hud-bad relative overflow-hidden rounded-xl border-bad/40 p-4 shadow-[0_0_50px_-24px_rgba(248,113,113,.9)]" role="alert" data-testid="run-error">
              <span className="absolute inset-y-0 left-0 w-[3px] bg-bad shadow-[0_0_12px_var(--color-bad)]" aria-hidden />
              <SysLabel tone="bad">SYS // FAULT</SysLabel>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 font-display text-[15px] font-semibold text-bad"><AlertOctagon className="size-4" />{r.error.code}{r.error.node_id && <span className="mono font-normal text-muted">at {r.error.node_id}</span>}</div>
              <p className="mt-1 text-[13px] text-fg">{r.error.message}</p>
              {r.error.details?.retry_reason && <p className="mt-1 text-[12.5px] text-muted">Retry decision: {r.error.details.retry_reason}</p>}
              {r.error.details && <details className="mt-2"><summary className="cursor-pointer text-[12px] text-muted hover:text-fg">Details</summary><div className="mt-1.5"><Json value={r.error.details} max={160} /></div></details>}
            </div>)}
          <Panel title="Execution graph" pad={false} actions={<span className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[.12em] text-faint max-sm:hidden"><span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-signal shadow-[0_0_6px_var(--color-signal)]" />active</span><span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-ok" />done</span><span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-bad" />failed</span></span>}>
            <div className="h-[46vh] min-h-[340px] rounded-b-xl bg-[radial-gradient(ellipse_at_50%_40%,rgba(109,92,245,.08),transparent_70%)]"><RunGraph definition={r.definition} steps={r.steps} current={active ? r.current_node : null} onSelect={setSel} selected={sel} /></div></Panel>
          {(r.children?.length > 0 || r.parent_run_id) && (
            <Panel title="Child runs" actions={<SysLabel tone="faint">FAN // {r.children?.length ?? 0}</SysLabel>}>
              <div data-testid="child-runs" className="space-y-1.5 text-[12.5px]">
                {r.parent_run_id && <div>Child of <Link className="mono text-accent hover:underline" to={`/runs/${r.parent_run_id}`}>{r.parent_run_id.slice(4, 14)}</Link></div>}
                {r.children?.map((k: any) => <div key={k.id} className="flex items-center gap-2"><span className="mono text-faint">{k.key?.split(':').slice(-1)[0] !== undefined ? `#${k.key.split(':').slice(-1)[0]}` : ''}</span><Link className="mono text-accent hover:underline" to={`/runs/${k.id}`}>{k.id.slice(4, 14)}</Link><StatusBadge status={k.status} /></div>)}
              </div>
            </Panel>)}
          <EffectsLedger runId={r.id} active={!!active} />
          <Panel title="Input / output">
            <div className="grid gap-3 md:grid-cols-2"><div><div className="kicker mb-1.5 text-[9.5px] text-faint">Input (sanitized)</div><Json value={r.input} /></div><div><div className="kicker mb-1.5 text-[9.5px] text-faint">Output</div><Json value={r.output} /></div></div>
          </Panel>
        </div>
        <Panel pad={false} className="flex min-h-[420px] min-w-0 flex-col xl:max-h-full" title={
          <div className="flex items-center gap-1 rounded-lg bg-black/30 p-0.5">
            {(['timeline', 'steps'] as const).map(t => <button key={t} onClick={() => setTab(t)} className={`rounded-md px-2 py-1 transition-all ${tab === t ? 'bg-accent/20 text-fg shadow-[inset_0_0_0_1px_rgba(161,148,255,.35)]' : 'hover:text-fg'}`}>{t === 'timeline' ? `Timeline (${stream.events.length})` : `Steps (${r.steps.length})`}</button>)}
          </div>} actions={<span className={`flex items-center gap-2 font-mono text-[10px] uppercase tracking-[.12em] ${stream.state === 'live' ? 'text-ok' : stream.state === 'closed' ? 'text-faint' : 'text-warn'}`} data-testid="stream-state">
            <Led on={stream.state === 'live' ? 4 : stream.state === 'closed' ? 0 : 2} total={4} color={stream.state === 'live' ? 'var(--color-ok)' : 'var(--color-warn)'} chase={stream.state === 'live' && !!active} />
            {stream.state === 'live' ? <Radio className="size-3.5" /> : stream.state === 'closed' ? <Wifi className="size-3.5" /> : <WifiOff className="size-3.5" />}{stream.state === 'live' ? 'Live' : stream.state === 'closed' ? 'Complete' : stream.state === 'reconnecting' ? 'Reconnecting — catching up…' : 'Connecting…'}</span>}>
          {tab === 'timeline' ? (
            <ol className="relative min-h-0 flex-1 overflow-auto py-2" data-testid="timeline">
              {stream.events.filter(e => !['run.claimed'].includes(e.type)).map((e, i, arr) => {
                const G = glyph(e.type), tone = TONE[e.type] ?? 'text-muted', major = e.type.startsWith('run.') && e.type !== 'run.queued'
                return (
                <li key={e.seq} className="flash-in group relative flex gap-2.5 py-[5px] pl-3 pr-4 hover:bg-white/[.025]">
                  <span className="mono w-12 shrink-0 pt-[3px] text-right text-[10.5px] text-faint tabular">+{((new Date(e.ts).getTime() - t0) / 1000).toFixed(1)}s</span>
                  <span className="relative flex w-5 shrink-0 justify-center">
                    {i > 0 && <span className="absolute -top-[5px] h-[13px] w-px bg-line-strong" aria-hidden />}
                    {i < arr.length - 1 && <span className="absolute bottom-[-5px] top-[18px] w-px bg-line-strong" aria-hidden />}
                    <span className={`relative z-[1] mt-px grid size-[18px] place-items-center rounded-full border bg-[#0b0e16] ${tone} ${major ? 'border-current shadow-[0_0_10px_currentColor]' : 'border-line-strong'}`}><G className="size-[11px]" strokeWidth={2.2} /></span>
                  </span>
                  <span className={`type-in min-w-0 flex-1 pt-px text-[12.5px] ${tone} ${major ? 'font-medium' : ''}`}>
                    {e.node_id && <button className="mono mr-1.5 rounded border border-line bg-white/[.04] px-1 text-[10.5px] text-faint transition-colors hover:border-accent/50 hover:text-fg" onClick={() => { setSel(e.node_id); setTab('steps') }}>{e.node_id}</button>}{describe(e)}
                  </span>
                </li>)})}
              {!stream.events.length && <li className="flex flex-col items-center gap-3 p-8 text-center text-muted"><Led on={3} chase />Waiting for events…</li>}
              <div ref={endRef} />
            </ol>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {r.steps.map((s: any) => (
                <div key={s.id} className={`border-b border-line/60 transition-colors ${sel === s.node_id ? 'bg-accent/[.06]' : ''}`}>
                  <button className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left hover:bg-white/[.03]" onClick={() => setSel(sel === s.node_id ? null : s.node_id)}>
                    <span className="grid size-6 shrink-0 place-items-center rounded-md" style={{ background: (NODE_META[s.node_type]?.color ?? '#7d849c') + '22', color: NODE_META[s.node_type]?.color }}>{(() => { const I = NODE_META[s.node_type]?.icon ?? Dot; return <I className="size-3.5" /> })()}</span>
                    <span className="mono text-[12px] text-fg">{s.node_id}</span><span className="text-[11px] text-faint">{NODE_META[s.node_type]?.label}{s.visit > 0 ? ` · visit ${s.visit + 1}` : ''}</span><span className="ml-auto"><StatusBadge status={s.status} /></span>
                  </button>
                  {sel === s.node_id && <StepDetail step={s} calls={selCalls.filter((c: any) => true)} />}
                </div>))}
              {!r.steps.length && <div className="p-6 text-center text-muted">No steps yet.</div>}
            </div>)}
          {sel && tab === 'timeline' && selStep.length > 0 && <div className="border-t border-line p-3 text-[12px] text-muted">Selected <span className="mono">{sel}</span> — open the Steps tab for inputs, outputs, attempts and tool calls.</div>}
        </Panel>
      </div>
      {caseOpen && <CaseDialog run={r} onClose={() => setCaseOpen(false)} nav={nav} />}
    </div>
  )
}


function StepDetail({ step, calls }: { step: any; calls: any[] }) {
  return (
    <div className="space-y-3 px-4 pb-4">
      {step.error && <div className="rounded-lg border border-bad/35 bg-bad/10 p-2 font-mono text-[12px] text-bad">{step.error.code}: {step.error.message}</div>}
      {step.attempts.length > 0 && <div><div className="kicker mb-1.5 text-[9.5px] text-faint">Attempt history</div>
        <ul className="space-y-1" data-testid="attempts">{step.attempts.map((a: any, i: number) => (
          <li key={i} className="relative rounded-lg border border-line bg-ink/80 py-2 pl-3.5 pr-2 text-[12px]"><span className="absolute inset-y-1.5 left-0 w-[3px] rounded-r-full bg-bad" aria-hidden /><span className="font-mono text-bad">attempt {a.n} failed</span> <span className="text-muted">· {a.tool} · {a.error?.message}</span>
            <div className="mt-0.5"><Tag tone={a.decision === 'retry' ? 'warn' : a.decision === 'reconcile' ? 'accent' : 'neutral'}>{a.decision}</Tag> <span className="text-muted">{a.reason}{a.reconciliation ? ` · operator: ${a.reconciliation}` : ''}</span></div></li>))}</ul></div>}
      <div><div className="kicker mb-1.5 text-[9.5px] text-faint">Input</div><Json value={step.input} max={200} /></div>
      <div><div className="kicker mb-1.5 text-[9.5px] text-faint">Output</div><Json value={step.output} max={240} /></div>
      {calls.length > 0 && <div><div className="kicker mb-1.5 text-[9.5px] text-faint">Tool & LLM calls</div>
        <ul className="space-y-1.5">{calls.filter(c => c.node_id === step.node_id).map((c: any) => (
          <li key={c.id} className="rounded-lg border border-line bg-ink/80 p-2 text-[12px]">
            <div className="flex flex-wrap items-center gap-1.5"><span className="mono">{c.tool}</span><Tag tone={c.outcome === 'ok' ? 'ok' : c.outcome === 'unknown' ? 'warn' : 'bad'}>{c.outcome}</Tag>
              {c.side_effect && <Tag tone="warn">side effect</Tag>}{c.source !== 'live' && <Tag tone="accent">{c.source}</Tag>}{c.idempotency_key && <Tag>idem key</Tag>}{c.approval_id && <Tag tone="ok">approved</Tag>}</div>
            {c.error && <div className="mt-1 text-bad">{c.error.message}</div>}
            <details className="mt-1"><summary className="cursor-pointer text-muted">args / result</summary><Json value={{ args: c.args, result: c.result }} max={200} /></details></li>))}</ul></div>}
    </div>
  )
}

function CaseDialog({ run, onClose, nav }: any) {
  const [name, setName] = useState(`${run.status}: ${run.workflow_name}`)
  const [status, setStatus] = useState('succeeded')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function go() {
    setBusy(true)
    try { const r = await api.post('/api/lab/cases', { run_id: run.id, name, notes, expect: { status } }); nav(`/lab?case=${r.id}`) } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#03040a]/70 p-4 backdrop-blur-sm" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Save regression case" className="glass hud hud-violet hairline fade-in w-full max-w-lg rounded-2xl p-5 shadow-[0_30px_80px_-20px_rgba(0,0,0,.9)]">
        <SysLabel tone="violet">LAB // CAPTURE</SysLabel><h2 className="font-display mt-1.5 text-[17px] font-semibold">Save as regression case</h2>
        <p className="mt-1 text-[12.5px] text-muted">Captures the workflow version, the sanitized input and every recorded tool/LLM result as fixtures, so this run can be replayed deterministically — without calling anything real.</p>
        <div className="mt-4 space-y-3">
          <Field label="Name"><input className={inputCls} value={name} onChange={e => setName(e.target.value)} aria-label="Case name" /></Field>
          <Field label="Expected outcome" hint={run.status === 'succeeded' ? 'This run succeeded — pin that behavior.' : 'This run did not succeed. Expecting “succeeded” makes it a bug report that fails until fixed.'}><select className={inputCls} value={status} onChange={e => setStatus(e.target.value)} aria-label="Expected status">{['succeeded', 'failed', 'cancelled', 'timed_out'].map(s => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Notes"><textarea className={inputCls} rows={2} value={notes} onChange={e => setNotes(e.target.value)} /></Field>
          <p className="text-[11.5px] text-faint">Secrets are redacted from fixtures. Review them for personal data before exporting.</p>
          {err && <div role="alert" className="text-bad">{err}</div>}
          <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} onClick={go} data-testid="case-save">Save case</Button></div>
        </div>
      </div>
    </div>
  )
}
