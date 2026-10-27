import { useRef, useState } from 'react'
import { BookOpen, FileText, Plus, Search, Trash2, Upload } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, Empty, ErrorState, Field, inputCls, Loading, PageHeader, Panel, SysLabel, Tag } from '../components/ui'

function KbDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const kb = useAsync(() => api.get(`/api/knowledge/${id}`), [id])
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<any[] | null>(null)
  const [msg, setMsg] = useState<{ k: 'ok' | 'err'; t: string } | null>(null)
  const file = useRef<HTMLInputElement>(null)
  if (kb.error) return <ErrorState error={kb.error} retry={kb.reload} />
  if (!kb.data) return <Loading />
  async function add(t: string, body: string) {
    try { const r = await api.post(`/api/knowledge/${id}/documents`, { title: t, text: body }); setMsg({ k: 'ok', t: `Added “${t}” (${r.chunks} chunks)` }); setTitle(''); setText(''); kb.reload(true); onChanged() }
    catch (e: any) { setMsg({ k: 'err', t: e.message }) }
  }
  return (
    <div className="space-y-4 p-5">
      <div><SysLabel tone="violet">KNOWLEDGE // BM25 INDEX</SysLabel><h2 className="font-display mt-1 text-[18px] font-semibold">{kb.data.name}</h2>
        <p className="text-[12.5px] text-muted">Use in a workflow with the <span className="mono">knowledge.search</span> tool and <span className="mono">kb: "{kb.data.name}"</span>. Results carry <span className="mono">knowledge://</span> source ids so agents can cite them. Search is local keyword ranking (BM25) — no embeddings, no network.</p></div>
      <Panel title="Add a document">
        <div className="space-y-3">
          <Field label="Title"><input className={inputCls} value={title} onChange={e => setTitle(e.target.value)} aria-label="Document title" /></Field>
          <Field label="Text"><textarea className={inputCls} rows={6} value={text} onChange={e => setText(e.target.value)} aria-label="Document text" /></Field>
          <div className="flex items-center gap-3">
            <Button variant="primary" disabled={!title.trim() || !text.trim()} onClick={() => add(title, text)} data-testid="add-doc"><Plus className="size-4" />Add document</Button>
            <input ref={file} type="file" hidden accept=".txt,.md,.csv,.json,text/*" onChange={async e => { const f = e.target.files?.[0]; if (f) await add(f.name, await f.text()) }} />
            <Button onClick={() => file.current?.click()}><Upload className="size-4" />Upload text file</Button>
            {msg && <span role="status" className={msg.k === 'ok' ? 'text-ok' : 'text-bad'}>{msg.t}</span>}
          </div>
        </div>
      </Panel>
      <Panel title="Test search" actions={<SysLabel tone="faint">QUERY // LOCAL</SysLabel>}>
        <form className="flex gap-2" onSubmit={async e => { e.preventDefault(); setHits((await api.post(`/api/knowledge/${id}/search`, { query: q })).results) }}>
          <input className={inputCls} value={q} onChange={e => setQ(e.target.value)} placeholder="Ask something your documents answer…" aria-label="Search query" />
          <Button type="submit" disabled={!q.trim()}><Search className="size-4" />Search</Button>
        </form>
        {hits && <ul className="mt-3 space-y-2" data-testid="search-results">{hits.length ? hits.map((h, i) => (
          <li key={i} className="rounded-md border border-line bg-bg/60 p-2.5"><div className="flex items-center gap-2"><span className="font-medium">{h.title}</span><Tag>score {h.score}</Tag></div><p className="mt-1 text-[12.5px] text-muted">{h.text.slice(0, 280)}{h.text.length > 280 ? '…' : ''}</p></li>)) : <li className="text-muted">No matches.</li>}</ul>}
      </Panel>
      <Panel title={`Documents (${kb.data.documents.length})`} pad={false}>
        {kb.data.documents.length ? kb.data.documents.map((d: any) => (
          <div key={d.id} className="flex items-center gap-3 border-b border-line/60 px-4 py-2.5 last:border-0"><FileText className="size-4 text-faint" /><span className="flex-1 truncate">{d.title}</span><span className="text-[12px] text-faint">{d.chunks} chunks</span>
            <Button size="sm" variant="ghost" aria-label={`Delete ${d.title}`} onClick={async () => { await api.del(`/api/knowledge/${id}/documents/${d.id}`); kb.reload(true); onChanged() }}><Trash2 className="size-3.5" /></Button></div>)) : <Empty title="No documents yet">Paste text or upload a .txt/.md file above.</Empty>}
      </Panel>
    </div>
  )
}

export default function Knowledge() {
  const list = useAsync(() => api.get('/api/knowledge'), [])
  const [sel, setSel] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [mode, setMode] = useState('lexical')
  const [err, setErr] = useState('')
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />
  async function create() {
    setErr('')
    try { const r = await api.post('/api/knowledge', { name: name.trim(), embedding: mode }); setName(''); await list.reload(true); setSel(r.id) } catch (e: any) { setErr(e.message) }
  }
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Knowledge" subtitle="Give agents your own documents. Retrieval is local, cited and replayable." />
      <div className="flex min-h-0 flex-1">
        <aside className="w-[300px] shrink-0 overflow-auto border-r border-line">
          <form className="flex gap-2 border-b border-line p-3" onSubmit={e => { e.preventDefault(); create() }}>
            <input className={inputCls} value={name} onChange={e => setName(e.target.value)} placeholder="New knowledge base" aria-label="Knowledge base name" />
            <Button type="submit" variant="primary" disabled={!name.trim()} data-testid="create-kb"><Plus className="size-4" /></Button>
          </form>
          <div className="border-b border-line px-3 pb-3"><select className={inputCls} value={mode} onChange={e => setMode(e.target.value)} aria-label="Retrieval mode"><option value="lexical">Keyword (BM25)</option><option value="ngram">Fuzzy (BM25 + character n-grams — typo tolerant, not neural)</option><option value="remote">Remote embeddings (OpenAI-compatible endpoint)</option></select></div>
          {err && <div role="alert" className="px-3 py-2 text-[12.5px] text-bad">{err}</div>}
          {!list.data ? <Loading /> : !list.data.length ? <Empty icon={<BookOpen className="size-7" />} title="No knowledge bases">Create one, add documents, then search it from an agent.</Empty> :
            list.data.map((k: any) => <button key={k.id} onClick={() => setSel(k.id)} className={`block w-full border-b border-line/60 px-4 py-3 text-left hover:bg-raised/60 ${sel === k.id ? 'bg-accent/10' : ''}`}><div className="font-medium">{k.name}</div><div className="text-[12px] text-muted">{k.documents} docs · {k.chunks} chunks</div></button>)}
        </aside>
        <div className="min-w-0 flex-1 overflow-auto">{sel && list.data?.some((k: any) => k.id === sel) ? <KbDetail key={sel} id={sel} onChanged={() => list.reload(true)} /> : <Empty title="Select a knowledge base" />}</div>
      </div>
    </div>
  )
}
