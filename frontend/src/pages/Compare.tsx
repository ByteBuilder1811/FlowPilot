import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, GitCompareArrows, TrendingDown, TrendingUp } from 'lucide-react'
import { api } from '../api'
import { useAsync, fmtDur } from '../hooks'
import { BreakFinder } from '../components/Differentiators'
import { AnimatedNumber, Button, Empty, ErrorState, Field, inputCls, Json, Loading, PageHeader, Panel, StatusBadge, SysLabel, Tag } from '../components/ui'

const CHG: Record<string, { label: string; tone: string; c: string }> = {
  regression: { label: 'Regression', tone: 'text-bad border-bad/40 bg-bad/10', c: 'var(--color-bad)' }, improvement: { label: 'Improvement', tone: 'text-ok border-ok/40 bg-ok/10', c: 'var(--color-ok)' },
  unchanged_pass: { label: 'Still passing', tone: 'text-muted border-line', c: 'var(--color-line-strong)' }, unchanged_fail: { label: 'Still failing', tone: 'text-warn border-warn/40 bg-warn/10', c: 'var(--color-warn)' }, inconclusive: { label: 'Inconclusive', tone: 'text-warn border-warn/40 bg-warn/10', c: 'var(--color-warn)' },
}
const show = (v: any) => (v === undefined ? '∅' : typeof v === 'string' ? v : JSON.stringify(v))
const dl = (v: number | null, unit = '') => (v == null ? '—' : v === 0 ? '0' : `${v > 0 ? '+' : ''}${v}${unit}`)

export default function Compare() {
  const [sp, setSp] = useSearchParams()
  const wfs = useAsync(() => api.get('/api/workflows'), [])
  const [wid, setWid] = useState(sp.get('workflow') ?? '')
  const wf = useAsync(() => (wid ? api.get(`/api/workflows/${wid}`) : Promise.resolve(null)), [wid])
  const cases = useAsync(() => api.get(`/api/lab/cases${wid ? `?workflow_id=${wid}` : ''}`), [wid])
  const [a, setA] = useState(sp.get('a') ?? '')
  const [b, setB] = useState(sp.get('b') ?? '')
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const vs = wf.data?.versions ?? []

  async function go() {
    setBusy(true); setErr(''); setRes(null)
    try { setRes(await api.post('/api/lab/compare', { workflow_id: wid, version_a: a, version_b: b })) } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  if (wfs.error) return <ErrorState error={wfs.error} retry={wfs.reload} />
  const s = res?.summary
  return (
    <>
      <PageHeader title="Compare versions" subtitle="Run the same regression cases against two versions, and see exactly what changed and what it did." />
      <div className="boot space-y-5 p-6 max-sm:p-4">
        <Panel>
          <div className="grid items-end gap-3 md:grid-cols-[1.2fr_1fr_auto_1fr_auto]">
            <Field label="Workflow"><select className={inputCls} aria-label="Workflow" value={wid} onChange={e => { setWid(e.target.value); setA(''); setB(''); setRes(null) }}><option value="">Select…</option>{(wfs.data ?? []).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Baseline (A)"><select className={inputCls} aria-label="Version A" value={a} onChange={e => setA(e.target.value)}><option value="">Select…</option>{vs.map((v: any) => <option key={v.id} value={v.id}>v{v.version_no} {v.note && `— ${v.note}`}</option>)}</select></Field>
            <span className="mb-1.5 hidden size-6 place-items-center rounded-full border border-line-strong bg-ink text-signal md:grid"><ArrowRight className="size-3.5" /></span>
            <Field label="Candidate (B)"><select className={inputCls} aria-label="Version B" value={b} onChange={e => setB(e.target.value)}><option value="">Select…</option>{vs.map((v: any) => <option key={v.id} value={v.id}>v{v.version_no} {v.note && `— ${v.note}`}</option>)}</select></Field>
            <Button variant="primary" busy={busy} disabled={!wid || !a || !b || a === b || !(cases.data?.length)} onClick={go} data-testid="compare-go"><GitCompareArrows className="size-4" />Compare</Button>
          </div>
          {wid && cases.data && !cases.data.length && <p className="mt-3 text-[12.5px] text-warn">This workflow has no regression cases yet. Save one from a finished run first — a comparison needs cases to run.</p>}
          {wid && vs.length < 2 && <p className="mt-3 text-[12.5px] text-muted">Needs at least two saved versions. Edit the workflow and use “Save as version”.</p>}
          {err && <p role="alert" className="mt-3 text-bad">{err}</p>}
        </Panel>
        {wid && !!cases.data?.length && <BreakFinder workflowId={wid} />}
        {busy && <Loading label="Replaying every case against both versions…" />}
        {!res && !busy && <Panel><Empty icon={<GitCompareArrows className="size-7" />} title="No comparison yet">Pick two versions. Each regression case is replayed deterministically on both, then diffed case by case.</Empty></Panel>}
        {res && <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5" data-testid="summary">
            {[['regression', 'Regressions', 'text-bad'], ['improvement', 'Improvements', 'text-ok'], ['unchanged_pass', 'Still passing', ''], ['unchanged_fail', 'Still failing', 'text-warn'], ['inconclusive', 'Inconclusive', 'text-warn']].map(([k, l, t]) => (
              <div key={k} className="glass hud relative overflow-hidden rounded-xl px-4 py-3">
                <span className="absolute inset-x-0 top-0 h-[2px]" style={{ background: s[k] ? CHG[k].c : 'transparent', boxShadow: s[k] ? `0 0 14px ${CHG[k].c}` : undefined }} aria-hidden />
                <div className="font-mono text-[10px] uppercase tracking-[.14em] text-faint">{l}</div><div className={`font-display mt-1.5 text-[30px] font-semibold leading-none ${s[k] ? t : 'text-muted'}`}><AnimatedNumber value={s[k]} /></div></div>))}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[12.5px] text-muted"><span>Sample size: <b className="text-fg">{s.sample_size} case{s.sample_size === 1 ? '' : 's'}</b> — no aggregate score is shown; inspect each case.</span>
            <span className={s.new_safety_violations ? 'text-bad' : ''}>New safety violations (side effects without approval): <b>{s.new_safety_violations}</b></span>
            {res.unavailable.map((u: string) => <Tag key={u} tone="warn">unavailable: {u}</Tag>)}</div>

          <Panel hud="violet" title={`What changed · v${res.a.version_no} → v${res.b.version_no}`} pad={false} actions={<SysLabel tone="faint">DIFF // STRUCTURAL</SysLabel>}>
            <StructuralDiff d={res.structural} />
          </Panel>

          <Panel title="Cases" pad={false}>
            {res.cases.map((c: any) => (
              <div key={c.case_id} className="relative border-b border-line/60 last:border-0" data-testid="compare-case">
                <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full" style={{ background: CHG[c.change].c, boxShadow: `0 0 10px ${CHG[c.change].c}` }} aria-hidden />
                <button className="flex w-full flex-wrap items-center gap-3 py-3 pl-5 pr-4 text-left transition-colors hover:bg-white/[.03]" onClick={() => setOpen(open === c.case_id ? null : c.case_id)}>
                  <span className={`badge-glow inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider ${CHG[c.change].tone}`}>{c.change === 'regression' ? <TrendingDown className="size-3" /> : c.change === 'improvement' ? <TrendingUp className="size-3" /> : null}{CHG[c.change].label}</span>
                  <span className="min-w-0 flex-1 truncate font-medium">{c.name}</span>
                  <span className="flex items-center gap-2"><StatusBadge status={c.a.status} /><ArrowRight className="size-3.5 text-faint" /><StatusBadge status={c.b.status} /></span>
                </button>
                {open === c.case_id && (
                  <div className="grid gap-4 px-4 pb-4 md:grid-cols-2">
                    {(['a', 'b'] as const).map(side => (
                      <div key={side} className={`rounded-xl border bg-ink/70 p-3 ${side === 'a' ? 'border-line' : 'border-accent/30'}`}><div className="mb-2 flex items-center justify-between"><span className="kicker text-[9.5px] text-faint">{side === 'a' ? `A · v${res.a.version_no}` : `B · v${res.b.version_no}`}</span><Link className="text-[12px] text-accent hover:underline" to={`/runs/${c[side].run_id}`}>open run</Link></div>
                        <ul className="space-y-1">{c[side].checks.map((k: any, i: number) => <li key={i} className="text-[12.5px]"><span className={k.passed === null ? 'text-warn' : k.passed ? 'text-ok' : 'text-bad'}>{k.passed === null ? '?' : k.passed ? '✓' : '✗'}</span> {k.name} {k.expected !== undefined && <span className="text-muted">expected <span className="mono">{String(k.expected)}</span> got <span className="mono">{String(k.actual)}</span></span>}{k.detail && <span className="text-muted"> {k.detail}</span>}</li>)}</ul>
                        {c[side].metrics.error && <div className="mt-2 text-[12px] text-bad">{c[side].metrics.error.code}: {c[side].metrics.error.message}</div>}
                      </div>))}
                    <div className="grid grid-cols-5 gap-2 md:col-span-2">{(['duration_ms', 'steps', 'tool_calls', 'llm_calls', 'retries'] as const).map(k => (
                      <div key={k} className="rounded-lg border border-line bg-white/[.02] p-2 font-mono text-[11.5px]"><div className="text-[9.5px] uppercase tracking-[.12em] text-faint">{k.replace('_', ' ')}</div><div className="tabular">{k === 'duration_ms' ? fmtDur(c.a.metrics[k]) : c.a.metrics[k]} → {k === 'duration_ms' ? fmtDur(c.b.metrics[k]) : c.b.metrics[k]}</div><div className="tabular text-muted">Δ {dl(c.delta[k], k === 'duration_ms' ? ' ms' : '')}</div></div>))}</div>
                    <div className="md:col-span-2"><div className="kicker mb-1.5 text-[9.5px] text-faint">Output</div><div className="grid gap-3 md:grid-cols-2"><Json value={c.a.metrics.output} max={120} /><Json value={c.b.metrics.output} max={120} /></div></div>
                  </div>)}
              </div>))}
          </Panel>
        </>}
      </div>
    </>
  )
}

function StructuralDiff({ d }: { d: any }) {
  const none = !d.nodes_added.length && !d.nodes_removed.length && !d.nodes_changed.length && !d.edges_added.length && !d.edges_removed.length && !d.policy_changed
  if (none) return <div className="p-4 text-muted">No structural differences.</div>
  return (
    <div className="divide-y divide-line/60 text-[12.5px]" data-testid="structural">
      {d.nodes_changed.map((n: any) => (
        <div key={n.node_id} className="px-4 py-3"><div className="flex items-center gap-2 font-medium"><span className="rounded-md border border-warn/40 bg-warn/10 px-1.5 font-mono text-[10px] font-bold text-warn">~</span>{n.label} <span className="mono text-faint">{n.node_id}</span></div>
          <div className="mt-2 overflow-hidden rounded-lg border border-line font-mono text-[12px]">
          {n.fields.map((f: any) => <div key={f.path} className="border-b border-line/60 last:border-0">
            <div className="bg-white/[.02] px-3 py-1 text-[10.5px] text-muted">{f.path}</div>
            <div className="flex gap-2 bg-bad/[.08] px-3 py-1 text-bad"><span className="select-none opacity-70">−</span><span className="line-through decoration-bad/50">{show(f.a)}</span></div>
            <div className="flex gap-2 bg-ok/[.08] px-3 py-1 text-ok"><span className="select-none opacity-70">+</span><span>{show(f.b)}</span></div>
          </div>)}</div></div>))}
      {d.nodes_added.map((n: any) => <div key={n.id} className="bg-ok/[.06] px-4 py-2 text-ok"><span className="font-mono">+</span> node {n.label ?? n.id} <span className="mono text-faint">({n.type})</span></div>)}
      {d.nodes_removed.map((n: any) => <div key={n.id} className="bg-bad/[.06] px-4 py-2 text-bad"><span className="font-mono">−</span> node {n.label ?? n.id} <span className="mono text-faint">({n.type})</span></div>)}
      {d.edges_added.map((e: any, i: number) => <div key={i} className="mono bg-ok/[.06] px-4 py-2 text-ok">+ edge {e.source}[{e.handle}] → {e.target}</div>)}
      {d.edges_removed.map((e: any, i: number) => <div key={i} className="mono bg-bad/[.06] px-4 py-2 text-bad">− edge {e.source}[{e.handle}] → {e.target}</div>)}
      {d.policy_changed && <div className="bg-warn/[.06] px-4 py-2 text-warn">Workflow policy changed</div>}
    </div>
  )
}
