import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Field, inputCls, Button, Tag } from './ui'
import { NODE_META } from './nodeMeta'

function JsonField({ label, value, onChange, hint, rows = 5 }: { label: string; value: any; onChange: (v: any) => void; hint?: string; rows?: number }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2))
  const [err, setErr] = useState('')
  useEffect(() => { setText(JSON.stringify(value ?? {}, null, 2)); setErr('') }, [JSON.stringify(value)]) // eslint-disable-line
  return (
    <Field label={label} hint={err ? undefined : hint}>
      <textarea className={`${inputCls} mono`} rows={rows} value={text} aria-label={label} spellCheck={false}
        onChange={e => { setText(e.target.value); try { onChange(JSON.parse(e.target.value)); setErr('') } catch { setErr('Invalid JSON — not applied yet') } }} />
      {err && <span className="mt-1 block text-[11px] text-warn">{err}</span>}
    </Field>
  )
}

const T = ({ label, k, c, set, hint, area, mono, ph }: any) => (
  <Field label={label} hint={hint}>
    {area ? <textarea className={`${inputCls} ${mono ? 'mono' : ''}`} rows={area} value={c[k] ?? ''} placeholder={ph} aria-label={label} onChange={e => set({ [k]: e.target.value })} />
      : <input className={`${inputCls} ${mono ? 'mono' : ''}`} value={c[k] ?? ''} placeholder={ph} aria-label={label} onChange={e => set({ [k]: e.target.value })} />}
  </Field>
)
const Num = ({ label, k, c, set, hint, min, max }: any) => (
  <Field label={label} hint={hint}><input type="number" min={min} max={max} className={inputCls} value={c[k] ?? ''} aria-label={label} onChange={e => set({ [k]: e.target.value === '' ? undefined : Number(e.target.value) })} /></Field>
)

function RetrySection({ c, set }: any) {
  const r = c.retry
  return (
    <fieldset className="space-y-2 rounded-xl border border-line bg-white/[.02] p-3">
      <legend className="px-1 font-mono text-[9.5px] font-semibold uppercase tracking-[.16em] text-signal">Reliability</legend>
      <label className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={!!r} onChange={e => set({ retry: e.target.checked ? { max_attempts: 3, backoff_ms: 500, max_backoff_ms: 8000, safe_to_retry: false } : undefined })} />Retry transient failures</label>
      {r && <div className="grid grid-cols-3 gap-2">
        <Num label="Attempts" min={1} max={5} k="max_attempts" c={r} set={(p: any) => set({ retry: { ...r, ...p } })} />
        <Num label="Backoff ms" k="backoff_ms" c={r} set={(p: any) => set({ retry: { ...r, ...p } })} />
        <Num label="Max ms" k="max_backoff_ms" c={r} set={(p: any) => set({ retry: { ...r, ...p } })} />
        <label className="col-span-3 flex items-start gap-2 text-[12px] text-muted"><input type="checkbox" className="mt-0.5" checked={!!r.safe_to_retry} onChange={e => set({ retry: { ...r, safe_to_retry: e.target.checked } })} /><span>Allow retry when a side effect's outcome is unknown <b className="text-warn">(may duplicate the action)</b>. Off = FlowPilot stops and asks you to reconcile.</span></label>
      </div>}
    </fieldset>
  )
}

function ActionSection({ c, set, tool }: any) {
  return (
    <fieldset className="space-y-2 rounded-xl border border-line bg-white/[.02] p-3">
      <legend className="px-1 font-mono text-[9.5px] font-semibold uppercase tracking-[.16em] text-signal">Safety</legend>
      <label className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={!!c.requires_approval} onChange={e => set({ requires_approval: e.target.checked })} />Always require human approval</label>
      <p className="text-[11px] text-faint">Side-effecting tools need approval anyway while the workspace policy is on (Settings).</p>
      {tool?.supports_idempotency_key && <T label="Idempotency key" k="idempotency_key" c={c} set={set} mono ph="{{ run.id }}-welcome-email" hint="Lets FlowPilot retry safely: the remote system dedupes on this key." />}
    </fieldset>
  )
}

export default function Properties({ node, meta, agents, workflows, onChange, onDelete, onRename, issues }: any) {
  const type: string = node.type
  const c = node.data.config ?? {}
  const set = (patch: any) => {
    const next = { ...c, ...patch }
    Object.keys(next).forEach(k => next[k] === undefined && delete next[k])
    onChange(next)
  }
  const m = NODE_META[type]
  const tools: any[] = meta?.tools ?? []
  const tool = tools.find(t => t.name === c.tool)
  const nodeIssues = issues.filter((i: any) => i.node_id === node.id)
  return (
    <div className="space-y-3 p-4" data-testid="properties">
      <div className="-mx-4 -mt-4 mb-1 flex items-center justify-between border-b border-line py-3 pl-4 pr-10" style={{ background: `linear-gradient(100deg, ${m.color}24, transparent 70%)` }}>
        <div className="flex min-w-0 items-center gap-2.5"><span className="grid size-8 shrink-0 place-items-center rounded-lg" style={{ background: m.color + '26', color: m.color, boxShadow: `inset 0 0 0 1px ${m.color}55, 0 0 18px -6px ${m.color}` }}><m.icon className="size-4" /></span>
          <div className="min-w-0"><div className="font-mono text-[9.5px] uppercase tracking-[.16em]" style={{ color: m.color }}>module // {type}</div><div className="font-display truncate text-[15px] font-semibold">{m.label}</div></div></div>
        {type !== 'start' && <Button size="sm" variant="ghost" aria-label="Delete node" onClick={onDelete}><Trash2 className="size-3.5" /></Button>}
      </div>
      <p className="text-[12px] text-muted">{m.blurb}</p>
      <Field label="Label"><input className={inputCls} value={node.data.label ?? ''} aria-label="Label" onChange={e => onRename(e.target.value)} /></Field>
      <div className="mono rounded-lg border border-dashed border-line px-2 py-1.5 text-[10.5px] text-faint">id: {node.id} — reference as <span className="text-muted">nodes.{node.id}</span></div>
      {nodeIssues.map((i: any, n: number) => <div key={n} className={`rounded-md border px-2.5 py-1.5 text-[12px] ${i.level === 'error' ? 'border-bad/30 bg-bad/5 text-bad' : 'border-warn/30 bg-warn/5 text-warn'}`}>{i.message}</div>)}

      {type === 'start' && <>
        <Field label="Trigger"><select className={inputCls} value={c.trigger ?? 'manual'} aria-label="Trigger" onChange={e => set({ trigger: e.target.value })}><option value="manual">Manual / API</option><option value="schedule">Schedule (cron, UTC)</option><option value="webhook">Webhook (token-authenticated)</option></select></Field>
        {c.trigger === 'schedule' && <>
          <T label="Cron (UTC, 5 fields)" k="cron" c={c} set={set} mono ph="*/15 * * * *" hint="minute hour day-of-month month day-of-week. Enable the schedule from the workflow panel after saving. Missed minutes are not back-filled." />
          <JsonField label="Input for scheduled runs" value={c.schedule_input ?? {}} onChange={v => set({ schedule_input: v })} rows={3} />
        </>}
        {c.trigger === 'webhook' && <T label="Dedupe key (webhook re-deliveries)" k="dedupe_key" c={c} set={set} mono ph="{{ input.event_id }}" hint="Same key = same event: in-flight or succeeded deliveries return the existing run instead of running again. A failed run may be re-attempted. An Idempotency-Key header also works." />}
        <JsonField label="Input schema" value={c.input_schema} onChange={v => set({ input_schema: v })} hint='{"required":["topic"],"properties":{"topic":{"type":"string"}}}' />
      </>}
      {type === 'end' && <JsonField label="Output (templates allowed)" value={c.output} onChange={v => set({ output: v })} hint="e.g. {{ nodes.agent.text }}" />}
      {type === 'llm' && <>
        <Field label="Agent (optional)" hint="Selecting an agent freezes its config into each saved version."><select className={inputCls} value={c.agent_id ?? ''} aria-label="Agent" onChange={e => {
          const a = agents.find((x: any) => x.id === e.target.value)
          set(a ? { agent_id: a.id, provider: a.config.provider, model: a.config.model, system: a.config.system, tools: a.config.tools ?? [], max_iterations: a.config.max_iterations ?? 4 } : { agent_id: undefined, agent_snapshot: undefined })
        }}><option value="">— inline configuration —</option>{agents.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Provider"><select className={inputCls} value={c.provider ?? 'mock'} aria-label="Provider" onChange={e => set({ provider: e.target.value, model: meta.providers.find((p: any) => p.name === e.target.value)?.models[0] })}>{meta.providers.map((p: any) => <option key={p.name} value={p.name}>{p.label}{p.configured ? '' : ' (not configured)'}</option>)}</select></Field>
          <Field label="Model"><select className={inputCls} value={c.model ?? ''} aria-label="Model" onChange={e => set({ model: e.target.value })}>{(meta.providers.find((p: any) => p.name === (c.provider ?? 'mock'))?.models ?? []).map((m: string) => <option key={m}>{m}</option>)}</select></Field>
        </div>
        {(c.provider ?? 'mock') === 'mock' && <div className="flex items-center gap-2 text-[11.5px] text-warn"><Tag tone="mock">mock</Tag>Deterministic test double — not a real model.</div>}
        <T label="System instructions" k="system" c={c} set={set} area={3} />
        <T label="Prompt" k="prompt" c={c} set={set} area={5} mono hint='Use {{ input.x }} / {{ nodes.id.text }}. Mock directive: [[tool:mock.search {"query":"…"}]]' />
        <Field label="Tools the agent may call"><div className="space-y-1">{tools.map((t: any) => (
          <label key={t.name} className="flex items-start gap-2 text-[12.5px]"><input type="checkbox" className="mt-0.5" checked={(c.tools ?? []).includes(t.name)} onChange={e => set({ tools: e.target.checked ? [...(c.tools ?? []), t.name] : (c.tools ?? []).filter((x: string) => x !== t.name) })} />
            <span><span className="mono">{t.name}</span>{t.side_effect && <span className="ml-1.5"><Tag tone="warn">side effect</Tag></span>}</span></label>))}</div></Field>
        <div className="grid grid-cols-2 gap-2"><Num label="Max iterations" min={1} max={25} k="max_iterations" c={c} set={set} /><Num label="Timeout (s)" k="timeout_s" c={c} set={set} /></div>
        <Field label="On tool failure"><select className={inputCls} value={c.tool_error ?? 'fail'} aria-label="On tool failure" onChange={e => set({ tool_error: e.target.value })}><option value="fail">Fail the step</option><option value="report_to_model">Report error to the model</option></select></Field>
        <fieldset className="space-y-2 rounded-md border border-line p-3"><legend className="px-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Memory</legend>
          <label className="flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={!!c.memory?.enabled} onChange={e => set({ memory: { window: 6, key: '{{ run.id }}', ...(c.memory ?? {}), enabled: e.target.checked } })} aria-label="Enable memory" />Remember conversation history</label>
          {c.memory?.enabled && <div className="grid grid-cols-[1fr_90px] gap-2"><T label="Memory key (e.g. chat session)" k="key" c={c.memory} set={(p: any) => set({ memory: { ...c.memory, ...p } })} mono ph="{{ input.session }}" /><Num label="Window" min={1} max={20} k="window" c={c.memory} set={(p: any) => set({ memory: { ...c.memory, ...p } })} /></div>}
          <p className="text-[11px] text-faint">Same key = shared history across runs. Fixture replays never read or write memory.</p></fieldset>
        <Field label="Output format"><select className={inputCls} value={c.output_format ?? 'text'} aria-label="Output format" onChange={e => set({ output_format: e.target.value })}><option value="text">Text</option><option value="json">JSON (validated)</option></select></Field>
        <RetrySection c={c} set={set} /><ActionSection c={c} set={set} />
      </>}
      {type === 'tool' && <>
        <Field label="Tool"><select className={inputCls} value={c.tool ?? ''} aria-label="Tool" onChange={e => set({ tool: e.target.value })}>{tools.filter((t: any) => t.name !== 'http.request').map((t: any) => <option key={t.name} value={t.name}>{t.name}{t.side_effect ? ' ⚠ side effect' : ''}</option>)}</select></Field>
        {tool && <p className="text-[12px] text-muted">{tool.description} {tool.mock && <Tag tone="mock">mock</Tag>}</p>}
        <JsonField label="Arguments" value={c.args} onChange={v => set({ args: v })} rows={6} hint={tool ? `Required: ${(tool.schema.required ?? []).join(', ') || 'none'}` : ''} />
        <RetrySection c={c} set={set} /><ActionSection c={c} set={set} tool={tool} />
      </>}
      {type === 'http' && <>
        <div className="grid grid-cols-[110px_1fr] gap-2"><Field label="Method"><select className={inputCls} value={c.method ?? 'GET'} aria-label="Method" onChange={e => set({ method: e.target.value })}>{['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].map(m => <option key={m}>{m}</option>)}</select></Field><T label="URL" k="url" c={c} set={set} mono /></div>
        <p className="text-[11.5px] text-faint">Private networks, localhost and cloud metadata addresses are blocked. Non-GET methods are side effects and require approval.</p>
        <JsonField label="Headers" value={c.headers ?? {}} onChange={v => set({ headers: v })} rows={3} hint="Secrets in headers are redacted from logs." />
        <JsonField label="Body" value={c.body ?? {}} onChange={v => set({ body: v })} rows={3} />
        <Num label="Timeout (s)" k="timeout_s" c={c} set={set} />
        <RetrySection c={c} set={set} /><ActionSection c={c} set={set} tool={{ supports_idempotency_key: true }} />
      </>}
      {type === 'transform' && <>
        <Field label="Operation"><select className={inputCls} value={c.op ?? ''} aria-label="Operation" onChange={e => set({ op: e.target.value })}>{meta.transform_ops.map((o: string) => <option key={o}>{o}</option>)}</select></Field>
        {c.op === 'template' && <T label="Template" k="template" c={c} set={set} area={4} mono />}
        {c.op === 'expression' && <T label="Expression" k="expression" c={c} set={set} area={3} mono hint="Sandboxed: no calls except len/str/int/min/max/…" />}
        {c.op === 'pick' && <><T label="Source" k="source" c={c} set={set} mono /><JsonField label="Keys" value={c.keys ?? []} onChange={v => set({ keys: v })} rows={2} /></>}
        {c.op === 'json_parse' && <T label="Source" k="source" c={c} set={set} mono />}
        {c.op === 'regex_extract' && <><T label="Source" k="source" c={c} set={set} mono /><T label="Pattern" k="pattern" c={c} set={set} mono /></>}
        {c.op === 'merge' && <JsonField label="Sources" value={c.sources ?? []} onChange={v => set({ sources: v })} rows={3} />}
        <p className="text-[11.5px] text-faint">Transforms are declarative. FlowPilot does not execute arbitrary code.</p>
      </>}
      {type === 'set_variable' && <><T label="Variable name" k="name" c={c} set={set} mono /><T label="Value" k="value" c={c} set={set} mono hint="Template or literal" /></>}
      {type === 'condition' && <T label="Expression" k="expression" c={c} set={set} area={3} mono hint="true → 'true' output, otherwise 'false'. e.g. input.amount >= 1000" />}
      {type === 'loop' && <>
        <Field label="Mode"><select className={inputCls} value={c.mode ?? 'count'} aria-label="Mode" onChange={e => set({ mode: e.target.value })}><option value="count">Count</option><option value="foreach">For each item</option></select></Field>
        {c.mode === 'foreach' ? <T label="Items expression" k="items" c={c} set={set} mono ph="input.items" /> : <Num label="Count" k="count" c={c} set={set} />}
        <Num label="Max iterations (hard limit)" min={1} max={100} k="max_iterations" c={c} set={set} hint="Required. Exceeding it fails the run — it never silently truncates unless you choose that." />
        <Field label="When the limit would be exceeded"><select className={inputCls} value={c.on_limit ?? 'fail'} aria-label="On limit" onChange={e => set({ on_limit: e.target.value })}><option value="fail">Fail the run</option><option value="truncate">Truncate to the limit</option></select></Field>
        <p className="text-[11.5px] text-faint">Connect <b>body</b> to the first node of the loop body, and the last body node back into this node.</p>
      </>}
      {type === 'approval' && <><T label="Title" k="title" c={c} set={set} /><T label="Message" k="message" c={c} set={set} area={3} /><JsonField label="Details shown to the approver" value={c.details ?? {}} onChange={v => set({ details: v })} rows={3} /></>}
      {type === 'subworkflow' && <>
        <Field label="Workflow to call"><select className={inputCls} value={c.workflow_id ?? ''} aria-label="Workflow to call" onChange={e => set({ workflow_id: e.target.value })}><option value="">Select…</option>{(workflows ?? []).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Mode"><select className={inputCls} value={c.mode ?? 'single'} aria-label="Sub-workflow mode" onChange={e => set({ mode: e.target.value })}><option value="single">Single call</option><option value="foreach">Map — one parallel child run per item</option></select></Field>
        {c.mode === 'foreach' && <><T label="Items expression" k="items" c={c} set={set} mono ph="input.ids" /><Num label="Max items (hard limit 20)" min={1} max={20} k="max_items" c={c} set={set} />
          <Field label="When an item fails"><select className={inputCls} value={c.on_item_error ?? 'fail'} aria-label="On item error" onChange={e => set({ on_item_error: e.target.value })}><option value="fail">Fail this step</option><option value="continue">Continue and report failures</option></select></Field></>}
        <JsonField label="Input for the child (templates; use {{ item }} / {{ index }} in map mode)" value={c.input ?? {}} onChange={v => set({ input: v })} rows={4} />
        <p className="text-[11.5px] text-faint">Children are real runs (see them on the run page). The parent pauses without holding a worker, resumes when all finish, and cancelling the parent cancels the children. Recursion is rejected and nesting is limited to 3 levels.</p>
      </>}
      {type === 'delay' && <Num label="Seconds" min={0} max={3600} k="seconds" c={c} set={set} />}
      {type === 'retry' && <>
        <p className="text-[12px] text-muted">Applies to the node this policy is connected to.</p>
        <div className="grid grid-cols-3 gap-2"><Num label="Attempts" min={1} max={5} k="max_attempts" c={c} set={set} /><Num label="Backoff ms" k="backoff_ms" c={c} set={set} /><Num label="Max ms" k="max_backoff_ms" c={c} set={set} /></div>
        <label className="flex items-start gap-2 text-[12px] text-muted"><input type="checkbox" className="mt-0.5" checked={!!c.safe_to_retry} onChange={e => set({ safe_to_retry: e.target.checked })} /><span>Retry even if a side effect's outcome is unknown <b className="text-warn">(may duplicate)</b></span></label>
      </>}
    </div>
  )
}
