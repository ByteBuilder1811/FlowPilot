import { useState } from 'react'
import { Bot, Plus, Trash2 } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, Empty, ErrorState, Field, inputCls, Loading, PageHeader, Panel, SysLabel, Tag } from '../components/ui'

const BLANK = { name: '', description: '', config: { provider: 'mock', model: 'mock-agent-1', system: '', tools: [] as string[], max_iterations: 4, timeout_s: 60, requires_approval: false, tool_error: 'fail', input_schema: {}, output_schema: {}, memory: { enabled: false, window: 0 } } }

export default function Agents() {
  const list = useAsync(() => api.get('/api/agents'), [])
  const meta = useAsync(() => api.get('/api/meta'), [])
  const [sel, setSel] = useState<string | 'new' | null>(null)
  const [form, setForm] = useState<any>(null)
  const [msg, setMsg] = useState<{ k: 'ok' | 'err'; t: string } | null>(null)
  const [schemaText, setSchemaText] = useState({ i: '{}', o: '{}' })
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />
  if (!meta.data || !list.data) return <Loading />

  function open(a: any | 'new') {
    const f = a === 'new' ? JSON.parse(JSON.stringify(BLANK)) : JSON.parse(JSON.stringify(a))
    setForm(f); setSel(a === 'new' ? 'new' : a.id); setMsg(null)
    setSchemaText({ i: JSON.stringify(f.config.input_schema ?? {}, null, 2), o: JSON.stringify(f.config.output_schema ?? {}, null, 2) })
  }
  const cfg = (p: any) => setForm({ ...form, config: { ...form.config, ...p } })
  async function save() {
    let i, o
    try { i = JSON.parse(schemaText.i); o = JSON.parse(schemaText.o) } catch { setMsg({ k: 'err', t: 'Schemas must be valid JSON' }); return }
    try {
      const body = { name: form.name, description: form.description, config: { ...form.config, input_schema: i, output_schema: o } }
      if (sel === 'new') { const r = await api.post('/api/agents', body); await list.reload(true); setSel(r.id) } else { await api.put(`/api/agents/${sel}`, body); await list.reload(true) }
      setMsg({ k: 'ok', t: 'Agent saved. Existing workflow versions keep the config they were saved with.' })
    } catch (e: any) { setMsg({ k: 'err', t: e.message }) }
  }
  const prov = meta.data.providers.find((p: any) => p.name === form?.config.provider)
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Agents" subtitle="Reusable agent configurations. Attach one to an LLM node; its config is frozen into each saved workflow version." actions={<Button variant="primary" onClick={() => open('new')} data-testid="new-agent"><Plus className="size-4" />New agent</Button>} />
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        <aside className="glass-flat w-[300px] shrink-0 overflow-auto border-r border-line max-md:max-h-[36vh] max-md:w-full max-md:border-b max-md:border-r-0">
          {!list.data.length ? <Empty icon={<Bot className="size-7" />} title="No agents yet">Create an agent to reuse instructions, tools and limits across workflows.</Empty> :
            list.data.map((a: any) => <button key={a.id} onClick={() => open(a)} className={`relative flex w-full items-center gap-3 border-b border-line/60 px-4 py-3 text-left transition-colors hover:bg-white/[.04] ${sel === a.id ? 'bg-gradient-to-r from-accent/15 to-transparent' : ''}`}>
              {sel === a.id && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full bg-signal shadow-[0_0_10px_var(--color-signal)]" aria-hidden />}
              <span className="relative grid size-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent shadow-[inset_0_0_0_1px_rgba(161,148,255,.35)]"><Bot className="size-4" /><span className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-[#0c0e16] bg-ok" aria-hidden /></span>
              <span className="min-w-0"><span className="block truncate font-medium">{a.name}</span><span className="block truncate font-mono text-[11px] text-muted">{a.config.provider} · {a.config.model} · {(a.config.tools ?? []).length} tools</span></span></button>)}
        </aside>
        <div className="min-w-0 flex-1 overflow-auto p-6 max-sm:p-4">
          {!form ? <Empty title="Select or create an agent" /> : (
            <div className="boot mx-auto max-w-3xl space-y-4">
              <div className="flex items-center gap-3"><SysLabel tone="violet" live>AGENT LINK // CONFIG</SysLabel><span className="mono text-[11px] text-faint">{sel === 'new' ? 'unsaved' : sel}</span></div>
              <Panel title="Identity"><div className="grid gap-3 md:grid-cols-2"><Field label="Name"><input className={inputCls} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} aria-label="Agent name" /></Field><Field label="Description"><input className={inputCls} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></Field></div></Panel>
              <Panel title="Model"><div className="grid gap-3 md:grid-cols-2">
                <Field label="Provider"><select className={inputCls} value={form.config.provider} onChange={e => cfg({ provider: e.target.value, model: meta.data.providers.find((p: any) => p.name === e.target.value).models[0] })}>{meta.data.providers.map((p: any) => <option key={p.name} value={p.name}>{p.label}</option>)}</select></Field>
                <Field label="Model"><select className={inputCls} value={form.config.model} onChange={e => cfg({ model: e.target.value })}>{prov?.models.map((m: string) => <option key={m}>{m}</option>)}</select></Field></div>
                {prov && !prov.configured && <p className="mt-2 text-[12.5px] text-warn">{prov.message}</p>}{prov?.mock && <p className="mt-2 flex items-center gap-2 text-[12.5px] text-muted"><Tag tone="mock">mock</Tag>Deterministic test double. Responses are labelled and never presented as a real model.</p>}</Panel>
              <Panel title="Instructions"><Field label="System instructions"><textarea className={inputCls} rows={5} value={form.config.system} onChange={e => cfg({ system: e.target.value })} aria-label="System instructions" /></Field></Panel>
              <Panel title="Tools & permissions">
                <div className="grid gap-1.5 md:grid-cols-2">{meta.data.tools.map((t: any) => <label key={t.name} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line bg-white/[.02] p-2.5 text-[12.5px] transition-colors hover:border-line-strong has-[:checked]:border-accent/50 has-[:checked]:bg-accent/[.07]"><input type="checkbox" className="mt-0.5" checked={form.config.tools.includes(t.name)} onChange={e => cfg({ tools: e.target.checked ? [...form.config.tools, t.name] : form.config.tools.filter((x: string) => x !== t.name) })} /><span><span className="mono">{t.name}</span> {t.side_effect && <Tag tone="warn">side effect</Tag>}<div className="text-muted">{t.description}</div></span></label>)}</div>
                <label className="mt-3 flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={form.config.requires_approval} onChange={e => cfg({ requires_approval: e.target.checked })} />Require human approval for every tool call this agent makes</label>
                <p className="mt-1 text-[11.5px] text-faint">The agent can only call tools you select here — anything else is denied by the backend.</p></Panel>
              <Panel title="Limits"><div className="grid gap-3 md:grid-cols-3"><Field label="Max iterations"><input type="number" min={1} max={25} className={inputCls} value={form.config.max_iterations} onChange={e => cfg({ max_iterations: Number(e.target.value) })} /></Field><Field label="Timeout (s)"><input type="number" className={inputCls} value={form.config.timeout_s} onChange={e => cfg({ timeout_s: Number(e.target.value) })} /></Field>
                <Field label="On tool failure"><select className={inputCls} value={form.config.tool_error} onChange={e => cfg({ tool_error: e.target.value })}><option value="fail">Fail the step</option><option value="report_to_model">Report to model</option></select></Field></div></Panel>
              <Panel title="Schemas & memory"><div className="grid gap-3 md:grid-cols-2"><Field label="Input schema (JSON)"><textarea className={`${inputCls} mono`} rows={4} value={schemaText.i} onChange={e => setSchemaText({ ...schemaText, i: e.target.value })} /></Field><Field label="Output schema (JSON)"><textarea className={`${inputCls} mono`} rows={4} value={schemaText.o} onChange={e => setSchemaText({ ...schemaText, o: e.target.value })} /></Field></div>
                <label className="mt-3 flex items-center gap-2 text-[12.5px]"><input type="checkbox" checked={!!form.config.memory?.enabled} onChange={e => cfg({ memory: { window: 6, key: '{{ run.id }}', ...(form.config.memory ?? {}), enabled: e.target.checked } })} aria-label="Agent memory" />Remember conversation history (keyed per run unless you set a key on the node)</label>
                <p className="mt-2 text-[11.5px] text-faint">Schemas are stored with the agent; the LLM node enforces JSON output format only.</p></Panel>
              <div className="flex items-center gap-3"><Button variant="primary" onClick={save} disabled={!form.name.trim()} data-testid="save-agent">Save agent</Button>
                {sel !== 'new' && <Button variant="danger" onClick={async () => { if (confirm('Delete this agent? Saved workflow versions keep their frozen copy.')) { await api.del(`/api/agents/${sel}`); setForm(null); setSel(null); list.reload(true) } }}><Trash2 className="size-4" />Delete</Button>}
                {msg && <span role="status" className={msg.k === 'ok' ? 'text-ok' : 'text-bad'}>{msg.t}</span>}</div>
            </div>)}
        </div>
      </div>
    </div>
  )
}
