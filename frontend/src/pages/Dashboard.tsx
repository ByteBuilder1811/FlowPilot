import { Link, useNavigate } from 'react-router-dom'
import { useState, type ReactNode } from 'react'
import { Play, ShieldCheck, FlaskConical, TriangleAlert, Plus, Activity, CircleCheck, CircleX, Hand, ArrowUpRight } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { AnimatedNumber, Button, Empty, ErrorState, Led, Loading, PageHeader, Panel, StatusBadge, SysLabel, statusColor } from '../components/ui'
import RunTable from '../components/RunTable'

function Stat({ label, value, tone = 'var(--color-fg)', sub, to, icon }: { label: string; value: number; tone?: string; sub?: string; to?: string; icon: ReactNode }) {
  const lit = value > 0
  const body = (
    <div className="glass hud lift group relative h-full overflow-hidden rounded-xl px-4 pb-3.5 pt-3">
      <span className="absolute inset-x-0 top-0 h-px" style={{ background: lit ? `linear-gradient(90deg, transparent, ${tone}, transparent)` : 'transparent' }} aria-hidden />
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] font-medium uppercase tracking-[.14em] text-faint">{label}</span>
        <span className="grid size-7 place-items-center rounded-lg border border-line bg-white/[.03] transition-colors group-hover:border-line-strong" style={{ color: lit ? tone : 'var(--color-faint)' }}>{icon}</span>
      </div>
      <div className="font-display mt-2 text-[32px] font-semibold leading-none" style={{ color: lit ? tone : 'var(--color-muted)', textShadow: lit && tone !== 'var(--color-fg)' ? `0 0 24px ${tone}` : undefined }}><AnimatedNumber value={value} /></div>
      {sub && <div className="mt-2 truncate text-[11.5px] text-muted">{sub}</div>}
      {to && <ArrowUpRight className="absolute bottom-3 right-3 size-3.5 text-faint opacity-0 transition-opacity group-hover:opacity-100" />}
    </div>
  )
  return to ? <Link to={to} className="block h-full">{body}</Link> : body
}

const ORDER = ['running', 'retrying', 'waiting_for_approval', 'queued', 'succeeded', 'failed', 'timed_out', 'cancelled']
const LABEL: Record<string, string> = { running: 'running', retrying: 'retrying', waiting_for_approval: 'awaiting', queued: 'queued', succeeded: 'succeeded', failed: 'failed', timed_out: 'timed out', cancelled: 'cancelled' }

/** Hero strip: real run counts only, rendered as a segmented telemetry bar. */
function Hero({ d }: { d: any }) {
  const rc = d.run_counts as Record<string, number>
  const keys = ORDER.filter(k => rc[k])
  const total = keys.reduce((n, k) => n + rc[k], 0)
  const inFlight = ['running', 'retrying', 'queued', 'waiting_for_approval'].reduce((n, k) => n + (rc[k] ?? 0), 0)
  return (
    <section className="glass hairline relative overflow-hidden rounded-2xl">
      <div className="pointer-events-none absolute -right-24 -top-24 size-[340px] rounded-full bg-accent-strong/20 blur-3xl" aria-hidden />
      <div className="pointer-events-none absolute -bottom-32 left-1/3 size-[300px] rounded-full bg-signal/10 blur-3xl" aria-hidden />
      <svg className="pointer-events-none absolute right-6 top-1/2 hidden h-[150%] -translate-y-1/2 opacity-[.35] lg:block" viewBox="0 0 200 200" aria-hidden>
        {[90, 66, 42, 18].map(r => <circle key={r} cx="100" cy="100" r={r} fill="none" stroke="#2b3147" strokeDasharray={r === 66 ? '2 5' : undefined} />)}
        <g className="orbit" style={{ animationDuration: '9s' }}><path d="M100 100 L100 10 A90 90 0 0 1 163.6 36.4 Z" fill="url(#hero-sw)" /></g>
        <defs><linearGradient id="hero-sw" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#22d3ee" stopOpacity="0" /><stop offset="1" stopColor="#22d3ee" stopOpacity=".45" /></linearGradient></defs>
      </svg>
      <div className="relative grid gap-6 p-6 max-sm:p-4 lg:grid-cols-[1fr_minmax(320px,440px)] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            {inFlight > 0 ? <SysLabel tone="signal" live>SYS // LIVE · {inFlight} in flight</SysLabel> : <SysLabel tone="ok">SYS // IDLE · all runs landed</SysLabel>}
            <SysLabel tone="faint">workspace · local</SysLabel>
          </div>
          <div className="font-display mt-3 text-[34px] font-bold leading-[1.02] tracking-tight max-sm:text-[28px]"><span className="text-signal">Agent flight deck</span></div>
          <p className="mt-2 max-w-xl text-[13.5px] text-muted">
            <span className="tabular font-mono text-fg">{d.workflow_count}</span> workflow{d.workflow_count === 1 ? '' : 's'} · <span className="tabular font-mono text-fg">{d.total_runs}</span> persisted run{d.total_runs === 1 ? '' : 's'}
            {d.pending_approvals > 0 && <> · <span className="font-medium text-accent">{d.pending_approvals} paused for your decision</span></>}
          </p>
        </div>
        <div className="min-w-0">
          <div className="flex items-baseline justify-between"><span className="font-mono text-[10px] uppercase tracking-[.14em] text-faint">Run telemetry · by status</span><span className="tabular font-mono text-[11px] text-muted">n={total}</span></div>
          <div className="mt-2 flex h-3 w-full gap-[3px] overflow-hidden rounded-[4px] bg-[#141826] p-[2px]" role="img" aria-label={`Run status distribution: ${keys.map(k => `${rc[k]} ${LABEL[k]}`).join(', ') || 'no runs'}`}>
            {keys.map(k => <span key={k} className="h-full rounded-[2px] transition-[flex-grow] duration-700" style={{ flexGrow: rc[k], background: statusColor(k), boxShadow: `0 0 10px ${statusColor(k)}` }} />)}
            {!total && <span className="h-full flex-1 rounded-[2px] bg-line" />}
          </div>
          <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5">
            {keys.map(k => <span key={k} className="flex items-center gap-1.5 text-[12px] text-muted"><span className="size-2 rounded-[2px]" style={{ background: statusColor(k) }} /><span className="tabular font-mono text-fg">{rc[k]}</span>{LABEL[k]}</span>)}
            {!total && <span className="text-[12px] text-faint">No runs recorded yet.</span>}
          </div>
        </div>
      </div>
    </section>
  )
}

export default function Dashboard() {
  const { data, error, loading, reload } = useAsync(() => api.get('/api/dashboard'), [], 3000)
  const meta = useAsync(() => api.get('/api/meta'), [])
  const nav = useNavigate()
  const [busy, setBusy] = useState('')
  const approvals = useAsync(() => api.get('/api/approvals?status=pending'), [], 3000)

  async function loadExample(key: string) {
    setBusy(key)
    try { const r = await api.post(`/api/examples/${key}/load`); nav(`/workflows/${r.id}`) } finally { setBusy('') }
  }
  if (loading && !data) return <Loading />
  if (error && !data) return <ErrorState error={error} retry={reload} />
  const d = data!
  const rc = d.run_counts
  const failed = (rc.failed ?? 0) + (rc.timed_out ?? 0)
  const empty = d.workflow_count === 0 && d.total_runs === 0

  return (
    <>
      <PageHeader title="Overview" subtitle="Live view of your workspace. Everything below is read from your persisted runs." actions={<Button variant="primary" onClick={() => nav('/workflows')}><Plus className="size-4" />New workflow</Button>} />
      <div className="boot space-y-5 p-6 max-sm:p-4">
        <Hero d={d} />
        {empty && (
          <Panel hud="violet">
            <Empty icon={<Play className="size-6" />} title="Start with a working example"
              action={<div className="flex flex-wrap justify-center gap-2">{(meta.data?.examples ?? []).map((e: any) => <Button key={e.key} busy={busy === e.key} variant={e.key === 'resilient' ? 'primary' : 'secondary'} onClick={() => loadExample(e.key)}>{e.name}</Button>)}</div>}>
              Examples are clearly marked as samples and run entirely on mock tools and a deterministic mock model, so no credentials are needed.
            </Empty>
          </Panel>
        )}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Live runs" value={d.total_runs} icon={<Activity className="size-3.5" />} tone="var(--color-signal)" sub={`${d.workflow_count} workflow${d.workflow_count === 1 ? '' : 's'}`} to="/runs" />
          <Stat label="Succeeded" value={rc.succeeded ?? 0} icon={<CircleCheck className="size-3.5" />} tone="var(--color-ok)" />
          <Stat label="Failed / timed out" value={failed} icon={<CircleX className="size-3.5" />} tone="var(--color-bad)" to="/runs?status=failed" />
          <Stat label="Pending approvals" value={d.pending_approvals} icon={<Hand className="size-3.5" />} tone="var(--color-accent)" sub={d.pending_approvals ? 'Runs are paused for you' : 'Nothing waiting'} />
          <Stat label="Regression cases" value={d.case_count} icon={<FlaskConical className="size-3.5" />} sub={d.case_count ? `${d.regression_status.passed} pass · ${d.regression_status.failed} fail · ${d.regression_status.inconclusive} inconclusive` : 'None saved yet'} to="/lab" />
        </div>

        {(approvals.data?.length ?? 0) > 0 && (
          <section className="glass hud hud-violet sweep sweep-violet overflow-hidden rounded-xl border-accent/40 shadow-[0_0_50px_-20px_rgba(161,148,255,.8)]">
            <header className="relative flex items-center justify-between border-b border-accent/20 px-4 py-2.5">
              <h2 className="flex items-center gap-2 font-mono text-[10.5px] font-semibold uppercase tracking-[.14em] text-accent"><ShieldCheck className="size-3.5" />Waiting for your decision</h2>
              <Led on={Math.min(5, approvals.data!.length)} color="var(--color-accent)" chase />
            </header>
            <ul className="relative">{approvals.data!.map((a: any) => (
              <li key={a.id} className="flex items-center justify-between gap-3 border-b border-line/60 px-4 py-2.5 last:border-0">
                <div className="min-w-0"><div className="truncate font-medium">{a.kind === 'reconcile' ? 'Outcome unknown — reconcile' : a.proposed?.title ?? `Approve ${a.proposed?.tool}`}</div>
                  <div className="truncate font-mono text-[11.5px] text-muted">{a.workflow_name} · {a.node_id}</div></div>
                <Link to={`/runs/${a.run_id}`}><Button size="sm" variant="primary">Review</Button></Link>
              </li>))}</ul>
          </section>
        )}

        <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
          <Panel title="Recent runs" pad={false} actions={<Link to="/runs" className="font-mono text-[11px] text-accent hover:text-signal">All runs →</Link>}>
            {d.recent_runs.length ? <RunTable runs={d.recent_runs} compact /> : <Empty title="No runs yet">Run a workflow from the Studio and it will appear here.</Empty>}
          </Panel>
          <div className="space-y-5">
            <Panel hud="bad" title={<span className="flex items-center gap-2"><TriangleAlert className="size-3.5 text-bad" />Recent failures</span>} pad={false}>
              {d.recent_failures.length ? <ul>{d.recent_failures.map((r: any) => (
                <li key={r.id} className="relative border-b border-line/60 py-2.5 pl-5 pr-4 last:border-0">
                  <span className="absolute inset-y-2.5 left-0 w-[3px] rounded-r-full bg-bad shadow-[0_0_8px_var(--color-bad)]" aria-hidden />
                  <div className="flex items-center justify-between gap-2"><Link to={`/runs/${r.id}`} className="truncate font-medium hover:text-accent">{r.workflow_name}</Link><StatusBadge status={r.status} /></div>
                  <div className="mt-1 line-clamp-2 font-mono text-[11px] text-muted"><span className="text-bad">{r.error?.code}</span>: {r.error?.message}</div>
                </li>))}</ul> : <Empty title="No failures">Failed runs show up here with their error.</Empty>}
            </Panel>
            <Panel title={<span className="flex items-center gap-2"><FlaskConical className="size-3.5" />Regression status</span>}>
              {d.case_count ? <div className="grid grid-cols-3 gap-2">{([['passed', 'var(--color-ok)'], ['failed', 'var(--color-bad)'], ['inconclusive', 'var(--color-warn)']] as const).map(([k, c]) => (
                <div key={k} className="rounded-lg border border-line bg-white/[.02] px-3 py-2"><div className="font-display text-[22px] font-semibold leading-none" style={{ color: d.regression_status[k] ? c : 'var(--color-muted)' }}><AnimatedNumber value={d.regression_status[k]} /></div><div className="mt-1.5 font-mono text-[9.5px] uppercase tracking-[.14em] text-faint">{k}</div></div>))}</div>
                : <p className="text-muted">Turn a failed run into a regression case from its run page, then replay it here.</p>}
            </Panel>
          </div>
        </div>
      </div>
    </>
  )
}
