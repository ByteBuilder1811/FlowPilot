import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider, addEdge, useReactFlow, useEdgesState, useNodesState, type Connection, type Edge, type Node } from '@xyflow/react'
import { AlertTriangle, CheckCircle2, ChevronDown, Download, GitCommitHorizontal, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Play, Save, Webhook, X } from 'lucide-react'
import { api, ApiError } from '../api'
import { useAsync } from '../hooks'
import { Button, ErrorState, Loading, Tag, inputCls, Field, SysLabel } from '../components/ui'
import FlowNode from '../components/FlowNode'
import Properties from '../components/Properties'
import { DEFAULT_CONFIG, NODE_META } from '../components/nodeMeta'

const nodeTypes = Object.fromEntries(Object.keys(NODE_META).map(k => [k, FlowNode]))

function Inner() {
  const { id } = useParams()
  const nav = useNavigate()
  const rf = useReactFlow()
  const wf = useAsync(() => api.get(`/api/workflows/${id}`), [id])
  const meta = useAsync(() => api.get('/api/meta'), [])
  const agents = useAsync(() => api.get('/api/agents'), [])
  const allWf = useAsync(() => api.get('/api/workflows'), [])
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [dirty, setDirty] = useState(false)
  const [issues, setIssues] = useState<any[]>([])
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [runOpen, setRunOpen] = useState(false)
  const [verOpen, setVerOpen] = useState(false)
  const [showIssues, setShowIssues] = useState(true)
  const wide = typeof window === 'undefined' || window.innerWidth >= 768
  const [palOpen, setPalOpen] = useState(wide)
  const [propsOpen, setPropsOpen] = useState(typeof window === 'undefined' || window.innerWidth >= 1600)
  useEffect(() => { if (selected) setPropsOpen(true) }, [selected])
  const loaded = useRef(false)
  const counter = useRef(1)

  useEffect(() => {
    if (!wf.data || loaded.current) return
    loaded.current = true
    const d = wf.data.draft
    setName(wf.data.name)
    setNodes(d.nodes.map((n: any) => ({ id: n.id, type: n.type, position: n.position, data: n.data })))
    setEdges(d.edges.map((e: any) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? 'out', targetHandle: 'in', label: e.sourceHandle && e.sourceHandle !== 'out' ? e.sourceHandle : undefined, type: 'smoothstep' })))
    setIssues(wf.data.issues)
    counter.current = d.nodes.length + 1
    setTimeout(() => rf.fitView({ padding: 0.2, maxZoom: 1, duration: 200 }), 60)
  }, [wf.data])

  const toDefinition = useCallback(() => ({
    format: 'flowpilot.workflow/v1',
    nodes: nodes.map(n => ({ id: n.id, type: n.type, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) }, data: { label: (n.data as any).label, config: (n.data as any).config ?? {} } })),
    edges: edges.map(e => ({ id: e.id, source: e.source, sourceHandle: e.sourceHandle ?? 'out', target: e.target })),
    policy: wf.data?.draft?.policy ?? {},
  }), [nodes, edges, wf.data])

  // live validation (debounced)
  useEffect(() => {
    if (!loaded.current) return
    const t = setTimeout(async () => { try { setIssues((await api.post(`/api/workflows/${id}/validate`, { definition: toDefinition() })).issues) } catch { /* offline */ } }, 400)
    return () => clearTimeout(t)
  }, [nodes, edges, id, toDefinition])

  const worst = useMemo(() => { const m: Record<string, string> = {}; issues.forEach(i => { if (i.node_id) m[i.node_id] = i.level === 'error' || m[i.node_id] === 'error' ? 'error' : 'warning' }); return m }, [issues])
  const viewNodes = useMemo(() => nodes.map(n => ({ ...n, data: { ...n.data, __issue: worst[n.id] } })), [nodes, worst])
  const errors = issues.filter(i => i.level === 'error')
  const sel = nodes.find(n => n.id === selected)

  const mark = <T,>(fn: (...a: any[]) => T) => (...a: any[]) => { setDirty(true); return fn(...a) }
  const onConnect = useCallback((c: Connection) => { setDirty(true); setEdges(es => addEdge({ ...c, id: `e_${c.source}_${c.sourceHandle}_${c.target}`, type: 'smoothstep', label: c.sourceHandle && c.sourceHandle !== 'out' ? c.sourceHandle : undefined }, es.filter(e => !(e.source === c.source && e.sourceHandle === c.sourceHandle)))) }, [setEdges])

  function addNode(type: string, pos?: { x: number; y: number }) {
    const idn = `${type}_${counter.current++}`
    const p = pos ?? { x: 200 + (nodes.length % 5) * 40, y: 80 + (nodes.length % 5) * 60 }
    setNodes(ns => [...ns, { id: idn, type, position: p, data: { label: NODE_META[type].label, config: JSON.parse(JSON.stringify(DEFAULT_CONFIG[type])) } }])
    setSelected(idn); setDirty(true)
  }
  function patchNode(nid: string, f: (d: any) => any) { setDirty(true); setNodes(ns => ns.map(n => n.id === nid ? { ...n, data: f(n.data) } : n)) }

  async function save(silent = false): Promise<boolean> {
    setSaving(true)
    try {
      const r = await api.put(`/api/workflows/${id}`, { name, definition: toDefinition() })
      setIssues(r.issues); setDirty(false)
      if (!silent) setToast({ kind: 'ok', text: 'Draft saved' })
      return true
    } catch (e: any) { setToast({ kind: 'err', text: e.message }); return false } finally { setSaving(false) }
  }
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 3500); return () => clearTimeout(t) }, [toast])

  if (wf.error) return <ErrorState error={wf.error} retry={wf.reload} />
  if (wf.loading || !meta.data || !agents.data) return <Loading />
  const w = wf.data

  return (
    <div className="flex h-full flex-col">
      <header className="glass-flat relative z-10 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2 rounded-xl border border-line bg-ink/70 py-1 pl-3 pr-1 shadow-[inset_0_1px_2px_rgba(0,0,0,.4)] max-sm:w-full">
          <span className="font-mono text-[11px] text-accent" aria-hidden>❯</span>
          <input aria-label="Workflow name" value={name} onChange={e => { setName(e.target.value); setDirty(true) }} className="font-display min-w-0 w-[300px] rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[15px] font-semibold tracking-tight focus:outline-none max-sm:w-full" />
          <span className="mono hidden pr-2 text-[10.5px] text-faint lg:inline">{id}</span>
        </div>
        {w.is_sample && <Tag tone="mock">sample</Tag>}
        <span className={`flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[.12em] ${dirty ? 'text-warn' : 'text-ok'}`}><span className={`size-1.5 rounded-full bg-current ${dirty ? 'signal-dot' : ''}`} />{dirty ? 'Unsaved changes' : 'Saved'}<span className="text-faint normal-case tracking-normal">{w.latest_version ? ` · latest v${w.latest_version}` : ''}</span></span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button onClick={() => setVerOpen(true)}><GitCommitHorizontal className="size-4" />Versions</Button>
          <a href={`/api/workflows/${id}/export`} download={`${name}.flowpilot.json`}><Button aria-label="Export workflow"><Download className="size-4" />Export</Button></a>
          <Button onClick={() => save()} busy={saving} data-testid="save"><Save className="size-4" />Save</Button>
          <Button variant="primary" onClick={() => setRunOpen(true)} data-testid="run-open"><Play className="size-4" />Run</Button>
        </div>
      </header>
      <div className="relative flex min-h-0 flex-1">
        <aside className={`glass-flat flex shrink-0 flex-col border-r border-line transition-[width] duration-300 ease-[cubic-bezier(.22,1,.36,1)] ${palOpen ? 'w-[178px]' : 'w-[52px]'}`} aria-label="Node palette">
          <div className={`flex items-center border-b border-line py-1.5 ${palOpen ? 'justify-between pl-3 pr-1.5' : 'justify-center'}`}>
            {palOpen && <span className="kicker text-[9.5px] text-faint">Modules</span>}
            <button onClick={() => setPalOpen(o => !o)} aria-label={palOpen ? 'Collapse palette' : 'Expand palette'} title={palOpen ? 'Collapse palette' : 'Expand palette'} className="grid size-7 place-items-center rounded-md text-faint transition-colors hover:bg-white/[.06] hover:text-fg">{palOpen ? <PanelLeftClose className="size-4" /> : <PanelLeftOpen className="size-4" />}</button>
          </div>
          <div className={`min-h-0 flex-1 overflow-y-auto overflow-x-hidden ${palOpen ? 'p-2' : 'px-1.5 py-2'}`}>
          {['Flow', 'AI', 'Actions', 'Data', 'Safety'].map(cat => (
            <div key={cat} className="mb-2.5">{palOpen ? <div className="kicker px-1.5 pb-1 text-[9px] text-faint">{cat}</div> : <div className="mx-auto mb-1 h-px w-5 bg-line" />}
              {Object.entries(NODE_META).filter(([t, m]) => m.category === cat && t !== 'start').map(([t, m]) => (
                <button key={t} draggable onDragStart={e => e.dataTransfer.setData('application/fp-node', t)} onClick={() => addNode(t)} title={palOpen ? m.blurb : `${m.label} — ${m.blurb}`} data-testid={`palette-${t}`}
                  className={`group flex w-full items-center gap-2 rounded-lg py-1.5 text-left text-[12.5px] text-muted transition-all hover:bg-white/[.06] hover:text-fg ${palOpen ? 'px-1.5 hover:translate-x-0.5' : 'justify-center px-0'}`}>
                  <span className="grid size-6 shrink-0 place-items-center rounded-md transition-shadow group-hover:shadow-[0_0_12px_-2px_currentColor]" style={{ background: m.color + '22', color: m.color, boxShadow: `inset 0 0 0 1px ${m.color}40` }}><m.icon className="size-3.5" /></span><span className={palOpen ? 'truncate' : 'sr-only'}>{m.label}</span></button>))}
            </div>))}
          {palOpen && <p className="px-1.5 text-[11px] text-faint">Click or drag onto the canvas.</p>}
          </div>
        </aside>
        <div className="relative min-w-0 flex-1" onDragOver={e => e.preventDefault()} onDrop={e => {
          const t = e.dataTransfer.getData('application/fp-node'); if (!t) return
          const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); addNode(t, { x: e.clientX - r.left - 100, y: e.clientY - r.top - 20 })
        }}>
          <ReactFlow nodes={viewNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={c => { onNodesChange(c); if (c.some(x => x.type === 'position' && (x as any).dragging === false || x.type === 'remove')) setDirty(true) }}
            onEdgesChange={c => { onEdgesChange(c); if (c.some(x => x.type === 'remove')) setDirty(true) }} onConnect={onConnect} onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)}
            fitView fitViewOptions={{ padding: 0.25, maxZoom: 1 }} minZoom={0.2} maxZoom={1.6} deleteKeyCode={['Backspace', 'Delete']} proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ type: 'smoothstep' }}>
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.3} color="#262c40" />
            <Controls showInteractive={false} position="top-left" /><MiniMap position="top-right" pannable zoomable style={{ width: 150, height: 96, background: "rgba(12,14,22,.92)", border: "1px solid #1d2232" }} nodeColor={n => NODE_META[n.type as string]?.color ?? "#555"} nodeBorderRadius={6} maskColor="rgba(7,8,13,.72)" />
          </ReactFlow>
          {toast && <div role="status" className={`glass fade-in absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded-lg px-3 py-1.5 font-mono text-[12px] ${toast.kind === 'ok' ? 'border-ok/40 text-ok shadow-[0_0_30px_-10px_var(--color-ok)]' : 'border-bad/40 text-bad shadow-[0_0_30px_-10px_var(--color-bad)]'}`}>{toast.text}</div>}
          <div className={`glass hud absolute bottom-3 left-1/2 z-10 w-[min(600px,calc(100%-24px))] -translate-x-1/2 rounded-xl ${errors.length ? 'hud-bad' : ''}`} data-testid="issues">
            <button className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px]" onClick={() => setShowIssues(s => !s)}>
              {errors.length ? <AlertTriangle className="size-4 text-bad" /> : <CheckCircle2 className="size-4 text-ok" />}
              <span className="font-medium">{errors.length ? `${errors.length} error${errors.length > 1 ? 's' : ''} block running` : 'Workflow is valid'}</span>
              {issues.length - errors.length > 0 && <span className="text-warn">{issues.length - errors.length} warning{issues.length - errors.length > 1 ? 's' : ''}</span>}
              <ChevronDown className={`ml-auto size-4 text-faint transition-transform ${showIssues ? '' : '-rotate-90'}`} />
            </button>
            {showIssues && issues.length > 0 && <ul className="max-h-32 overflow-auto border-t border-line px-3 py-1.5">{issues.map((i, n) => (
              <li key={n}><button className="flex w-full items-baseline gap-2 py-0.5 text-left text-[12px] hover:text-fg" onClick={() => i.node_id && setSelected(i.node_id)}>
                <span className={i.level === 'error' ? 'text-bad' : 'text-warn'}>{i.level}</span><span className="text-muted">{i.node_id && <span className="mono mr-1.5 text-faint">{i.node_id}</span>}{i.message}</span></button></li>))}</ul>}
          </div>
        </div>
        {!propsOpen && <button onClick={() => setPropsOpen(true)} aria-label="Show properties" title="Show properties" className="glass-flat grid w-[40px] shrink-0 place-items-start justify-center border-l border-line pt-2 text-faint hover:text-fg"><PanelRightOpen className="size-4" /></button>}
        <aside className={`glass-flat relative shrink-0 overflow-auto border-l border-line max-md:absolute max-md:inset-y-0 max-md:right-0 max-md:z-20 max-md:w-[min(340px,90vw)] max-md:bg-[#0b0d15]/95 ${propsOpen ? 'w-[320px]' : 'hidden'}`} aria-label="Properties">
          <button onClick={() => setPropsOpen(false)} aria-label="Hide properties" title="Hide properties" className="absolute right-2 top-2.5 z-10 grid size-7 place-items-center rounded-md text-faint transition-colors hover:bg-white/[.06] hover:text-fg"><PanelRightClose className="size-4" /></button>
          {sel ? <Properties key={sel.id} node={sel} meta={meta.data} agents={agents.data} workflows={(allWf.data ?? []).filter((x: any) => x.id !== id)} issues={issues}
            onChange={(cfg: any) => patchNode(sel.id, d => ({ ...d, config: cfg }))} onRename={(l: string) => patchNode(sel.id, d => ({ ...d, label: l }))}
            onDelete={() => { setNodes(ns => ns.filter(n => n.id !== sel.id)); setEdges(es => es.filter(e => e.source !== sel.id && e.target !== sel.id)); setSelected(null); setDirty(true) }} />
            : <WorkflowPanel w={w} reload={() => wf.reload(true)} />}
        </aside>
      </div>
      {runOpen && <RunDialog id={id!} start={nodes.find(n => n.type === 'start')} blocked={errors.length > 0} save={() => save(true)} onClose={() => setRunOpen(false)} nav={nav} />}
      {verOpen && <VersionsDialog w={w} id={id!} dirty={dirty} save={() => save(true)} onClose={() => { setVerOpen(false); wf.reload(true) }} />}
    </div>
  )
}

function WorkflowPanel({ w, reload }: { w: any; reload: () => void }) {
  const [tok, setTok] = useState<string | null>(null)
  const [pub, setPub] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const hook = w.trigger === 'webhook'
  const sch = w.schedule ?? { configured: false }
  async function toggle() { setErr(''); try { await api.post(`/api/workflows/${w.id}/schedule`, { enabled: !sch.enabled }); reload() } catch (e: any) { setErr(e.message) } }
  return (
    <div className="space-y-3 p-4">
      <SysLabel tone="violet">MODULE // WORKFLOW</SysLabel><div className="font-display -mt-1 text-[16px] font-semibold">Workflow</div>
      <p className="text-[12.5px] text-muted">Select a node to configure it. Drag from a node's right-hand handle to another node's left handle to connect them.</p>
      <dl className="grid grid-cols-2 gap-y-1 text-[12.5px]"><dt className="text-faint">Nodes</dt><dd className="tabular">{w.node_count}</dd><dt className="text-faint">Latest version</dt><dd>{w.latest_version ? `v${w.latest_version}` : 'none yet'}</dd><dt className="text-faint">Trigger</dt><dd>{w.trigger}</dd></dl>
      {sch.configured && <div className="space-y-2 rounded-md border border-line p-3" data-testid="schedule-panel"><div className="flex items-center gap-1.5 font-medium">Schedule <span className="mono text-[11px] text-muted">{sch.cron}</span></div>
        <div className="text-[12px] text-muted">{sch.enabled ? (sch.next_fire ? `Next run ${new Date(sch.next_fire).toLocaleString()} (UTC ${sch.next_fire.slice(11, 16)})` : 'Enabled') : 'Disabled'}</div>
        <Button size="sm" variant={sch.enabled ? 'secondary' : 'primary'} onClick={toggle} data-testid="schedule-toggle">{sch.enabled ? 'Disable schedule' : 'Enable schedule'}</Button>{err && <div role="alert" className="text-[12px] text-bad">{err}</div>}</div>}
      {hook && <div className="space-y-2 rounded-md border border-line p-3"><div className="flex items-center gap-1.5 font-medium"><Webhook className="size-3.5" />Webhook</div>
        <div className="mono break-all text-[11px] text-muted">POST {location.origin}/api/hooks/{w.id}</div>
        <Button size="sm" onClick={async () => setTok((await api.post(`/api/workflows/${w.id}/webhook-token`)).token)}>{w.webhook_enabled ? 'Rotate token' : 'Create token'}</Button>
        {tok && <div className="mono break-all rounded border border-warn/30 bg-warn/5 p-2 text-[11px] text-warn">{tok}<div className="mt-1 text-faint">Shown once. Send as X-FlowPilot-Webhook-Token.</div></div>}</div>}
      <div className="space-y-2 rounded-md border border-line p-3" data-testid="publish-panel"><div className="font-medium">Publish as API</div>
        <div className="mono break-all text-[11px] text-muted">POST {location.origin}/api/v1/workflows/{w.id}/invoke</div>
        <div className="flex gap-2"><Button size="sm" onClick={async () => { setPub((await api.post(`/api/workflows/${w.id}/publish-token`)).token); reload() }} data-testid="publish-create">{w.publish_enabled ? 'Rotate key' : 'Create API key'}</Button>
          {w.publish_enabled && <Button size="sm" variant="danger" onClick={async () => { await api.del(`/api/workflows/${w.id}/publish-token`); setPub(null); reload() }}>Revoke</Button>}</div>
        {pub && <div className="mono break-all rounded border border-warn/30 bg-warn/5 p-2 text-[11px] text-warn" data-testid="publish-key">{pub}<div className="mt-1 text-faint">Shown once. Send as X-FlowPilot-Api-Key. Body: {'{"input":{…},"wait_seconds":20}'}</div></div>}</div>
    </div>
  )
}

function Modal({ title, onClose, children, wide }: any) {
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [onClose])
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#03040a]/70 p-4 backdrop-blur-sm" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={title} className={`glass hud hud-violet hairline fade-in max-h-[88vh] w-full overflow-auto rounded-2xl shadow-[0_30px_80px_-20px_rgba(0,0,0,.9)] ${wide ? 'max-w-2xl' : 'max-w-lg'}`}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3"><h2 className="font-display text-[16px] font-semibold">{title}</h2><button aria-label="Close" onClick={onClose} className="text-muted hover:text-fg"><X className="size-4" /></button></header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}

function RunDialog({ id, start, blocked, save, onClose, nav }: any) {
  const schema = start?.data?.config?.input_schema ?? {}
  const sample = Object.fromEntries((schema.required ?? Object.keys(schema.properties ?? {})).map((k: string) => [k, schema.properties?.[k]?.type === 'number' ? 0 : '']))
  const [text, setText] = useState(JSON.stringify(sample, null, 2))
  const [err, setErr] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  async function go() {
    let input: any
    try { input = JSON.parse(text) } catch { setErr({ message: 'Input is not valid JSON' }); return }
    setBusy(true); setErr(null)
    try {
      if (!(await save())) return
      const r = await api.post(`/api/workflows/${id}/run`, { input })
      nav(`/runs/${r.run_id}`)
    } catch (e: any) { setErr(e instanceof ApiError ? e.detail : { message: e.message }) } finally { setBusy(false) }
  }
  return (
    <Modal title="Run workflow" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-muted">Starts a <b>live</b> run. Mock tools and the mock model run locally; side-effecting tools will pause for approval.</p>
        <Field label="Input (JSON)"><textarea className={`${inputCls} mono`} rows={7} value={text} onChange={e => setText(e.target.value)} aria-label="Run input" /></Field>
        {blocked && <div className="rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-[12.5px] text-bad">Fix the validation errors first.</div>}
        {err && <div role="alert" className="rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-[12.5px] text-bad">{err.message}{err.issues?.filter((i: any) => i.level === 'error').map((i: any, n: number) => <div key={n} className="mt-0.5 text-muted">{i.node_id ? `${i.node_id}: ` : ''}{i.message}</div>)}</div>}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} disabled={blocked} onClick={go} data-testid="run-start"><Play className="size-4" />Start run</Button></div>
      </div>
    </Modal>
  )
}

function VersionsDialog({ w, id, dirty, save, onClose }: any) {
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState('')
  const nav = useNavigate()
  async function snap() {
    try { if (dirty && !(await save())) return; const r = await api.post(`/api/workflows/${id}/versions`, { note }); setMsg(r.reused ? `No changes since v${r.version_no}; nothing new created.` : `Created v${r.version_no}.`); setNote('') }
    catch (e: any) { setMsg(e.message) }
  }
  return (
    <Modal title="Versions" onClose={onClose} wide>
      <div className="space-y-4">
        <p className="text-[12.5px] text-muted">Versions are immutable snapshots. Every run records the exact version it used, so editing the draft never changes history.</p>
        <div className="flex items-end gap-2"><div className="flex-1"><Field label="Note for the new version"><input className={inputCls} value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. raise retries to 3" aria-label="Version note" /></Field></div><Button variant="primary" onClick={snap} data-testid="snapshot">Save as version</Button></div>
        {msg && <div role="status" className="text-[12.5px] text-ok">{msg}</div>}
        <table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint"><th className="py-1.5">Version</th><th>Note</th><th>Hash</th><th /></tr></thead>
          <tbody>{w.versions.map((v: any) => <tr key={v.id} className="border-b border-line/60"><td className="py-1.5 font-medium">v{v.version_no}</td><td className="text-muted">{v.note || '—'}</td><td className="mono text-faint">{v.content_hash}</td>
            <td className="text-right"><Button size="sm" variant="ghost" onClick={() => nav(`/compare?workflow=${id}&b=${v.id}`)}>Compare</Button></td></tr>)}{!w.versions.length && <tr><td colSpan={4} className="py-3 text-muted">No versions yet — the first run or "Save as version" creates v1.</td></tr>}</tbody></table>
      </div>
    </Modal>
  )
}

export default function Studio() { return <ReactFlowProvider><Inner /></ReactFlowProvider> }
