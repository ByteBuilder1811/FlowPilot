import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Copy, Download, Plus, Trash2, Upload, Webhook } from 'lucide-react'
import { api } from '../api'
import { useAsync, ago } from '../hooks'
import { Button, Empty, ErrorState, Field, inputCls, Loading, PageHeader, Panel, StatusBadge, Tag } from '../components/ui'

export default function Workflows() {
  const { data, error, loading, reload } = useAsync(() => api.get('/api/workflows'), [])
  const meta = useAsync(() => api.get('/api/meta'), [])
  const nav = useNavigate()
  const [name, setName] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const file = useRef<HTMLInputElement>(null)

  async function create() {
    const r = await api.post('/api/workflows', { name: name.trim() || 'Untitled workflow' })
    nav(`/workflows/${r.id}`)
  }
  async function importFile(f: File) {
    try { const r = await api.post('/api/workflows/import', JSON.parse(await f.text())); nav(`/workflows/${r.id}`) }
    catch (e: any) { setMsg(`Import failed: ${e.message}`) }
  }
  async function dup(id: string) { await api.post(`/api/workflows/${id}/duplicate`); reload() }
  async function del(w: any) { if (confirm(`Delete "${w.name}"? Its run history is kept.`)) { await api.del(`/api/workflows/${w.id}`); reload() } }

  if (error) return <ErrorState error={error} retry={reload} />
  return (
    <>
      <PageHeader title="Workflows" subtitle="Design, version and run automations." actions={<>
        <input ref={file} type="file" accept="application/json" hidden onChange={e => e.target.files?.[0] && importFile(e.target.files[0])} />
        <Button onClick={() => file.current?.click()}><Upload className="size-4" />Import</Button>
      </>} />
      <div className="boot space-y-5 p-6 max-sm:p-4">
        {msg && <div role="alert" className="rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-bad">{msg}</div>}
        <Panel title="New workflow">
          <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); create() }}>
            <div className="w-80 max-sm:w-full"><Field label="Name"><input className={inputCls} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Support triage" aria-label="Workflow name" /></Field></div>
            <Button variant="primary" type="submit"><Plus className="size-4" />Create</Button>
            <span className="mx-2 font-mono text-[10.5px] uppercase tracking-[.12em] text-faint">or load a sample</span>
            {(meta.data?.examples ?? []).map((e: any) => <Button key={e.key} type="button" onClick={async () => nav(`/workflows/${(await api.post(`/api/examples/${e.key}/load`)).id}`)} title={e.description}>{e.name.replace(' (sample)', '')}</Button>)}
          </form>
        </Panel>
        <Panel pad={false}>
          {loading && !data ? <Loading /> : !data?.length ? <Empty title="No workflows yet">Create one above, or load a sample to see a complete working flow.</Empty> : (
            <div className="overflow-x-auto"><table className="w-full text-left" data-testid="workflow-table">
              <thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Nodes</th><th className="px-3 py-2 font-medium">Version</th><th className="px-3 py-2 font-medium">Runs</th><th className="px-3 py-2 font-medium">Updated</th><th /></tr></thead>
              <tbody>{data.map((w: any) => (
                <tr key={w.id} className="group border-b border-line/60 transition-colors last:border-0 hover:bg-white/[.03]">
                  <td className="px-4 py-2.5"><Link to={`/workflows/${w.id}`} className="font-display text-[14px] font-semibold hover:text-accent">{w.name}</Link>
                    <span className="ml-2 inline-flex gap-1.5">{w.is_sample && <Tag tone="mock">sample</Tag>}{w.trigger === 'webhook' && <Tag><Webhook className="mr-1 size-3" />webhook</Tag>}</span>
                    <div className="mt-0.5 max-w-xl truncate text-[12.5px] text-muted">{w.description}</div></td>
                  <td className="tabular px-3 py-2.5 text-muted">{w.node_count}</td>
                  <td className="px-3 py-2.5 text-muted">{w.latest_version ? `v${w.latest_version}` : 'unsaved'}</td>
                  <td className="px-3 py-2.5"><div className="flex gap-1.5">{Object.entries(w.run_counts).map(([s, n]) => <span key={s} className="inline-flex items-center gap-1"><StatusBadge status={s} /><span className="tabular text-muted">{n as number}</span></span>)}{!Object.keys(w.run_counts).length && <span className="text-faint">never run</span>}</div></td>
                  <td className="px-3 py-2.5 text-muted">{ago(w.updated_at)}</td>
                  <td className="px-3 py-2.5"><div className="flex justify-end gap-1">
                    <a href={`/api/workflows/${w.id}/export`} download={`${w.name}.flowpilot.json`}><Button size="sm" variant="ghost" aria-label="Export"><Download className="size-3.5" /></Button></a>
                    <Button size="sm" variant="ghost" aria-label="Duplicate" onClick={() => dup(w.id)}><Copy className="size-3.5" /></Button>
                    <Button size="sm" variant="ghost" aria-label="Delete" onClick={() => del(w)}><Trash2 className="size-3.5" /></Button></div></td>
                </tr>))}</tbody>
            </table></div>)}
        </Panel>
      </div>
    </>
  )
}
