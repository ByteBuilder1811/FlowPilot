import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LayoutTemplate } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, ErrorState, Loading, PageHeader, Panel, SysLabel, Tag } from '../components/ui'

export default function Templates() {
  const meta = useAsync(() => api.get('/api/meta'), [])
  const nav = useNavigate()
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  if (meta.error) return <ErrorState error={meta.error} retry={meta.reload} />
  if (!meta.data) return <Loading />
  const cats = [...new Set<string>(meta.data.examples.map((e: any) => e.category))]
  async function load(key: string) { setBusy(key); setErr(''); try { nav(`/workflows/${(await api.post(`/api/examples/${key}/load`)).id}`) } catch (e: any) { setErr(e.message) } finally { setBusy('') } }
  return (
    <>
      <PageHeader title="Templates" subtitle="Runnable starting points. Every template runs on mock tools and the mock model, so you can try it without credentials." />
      <div className="space-y-6 p-6" data-testid="templates">
        {err && <div role="alert" className="rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-bad">{err}</div>}
        {cats.map(cat => (
          <section key={cat}><SysLabel tone="violet">{cat.toUpperCase()}</SysLabel>
            <div className="mt-2 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{meta.data.examples.filter((e: any) => e.category === cat).map((e: any) => (
              <Panel key={e.key}><div className="flex items-start gap-2"><LayoutTemplate className="mt-0.5 size-4 shrink-0 text-accent" /><div className="min-w-0 flex-1"><div className="font-medium">{e.name.replace(' (sample)', '')} <Tag tone="mock">sample</Tag></div><p className="mt-1 text-[12.5px] text-muted">{e.description}</p></div></div>
                <div className="mt-3"><Button variant="primary" size="sm" busy={busy === e.key} onClick={() => load(e.key)} data-testid={`template-${e.key}`}>Use template</Button></div></Panel>))}</div></section>))}
      </div>
    </>
  )
}
