import { useState } from 'react'
import { CheckCircle2, CircleAlert } from 'lucide-react'
import { api } from '../api'
import { useAsync, ago } from '../hooks'
import { Button, ErrorState, Led, Loading, PageHeader, Panel, Tag } from '../components/ui'

import Credentials from '../components/Credentials'

export default function Integrations() {
  const meta = useAsync(() => api.get('/api/meta'), [])
  const outbox = useAsync(() => api.get('/api/mock/outbox?limit=10'), [], 5000)
  const [res, setRes] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState('')
  if (meta.error) return <ErrorState error={meta.error} retry={meta.reload} />
  if (!meta.data) return <Loading />
  async function test(n: string) { setBusy(n); try { setRes({ ...res, [n]: await api.post(`/api/providers/${n}/test`) }) } finally { setBusy('') } }
  return (
    <>
      <PageHeader title="Integrations" subtitle="Model providers and tools. FlowPilot never stores API keys — providers read them from environment variables." />
      <div className="boot space-y-5 p-6 max-sm:p-4">
        <Panel title="Model providers" pad={false}>
          {meta.data.providers.map((p: any) => (
            <div key={p.name} className="relative flex flex-wrap items-start gap-4 border-b border-line/60 py-3.5 pl-5 pr-4 last:border-0" data-testid={`provider-${p.name}`}>
              <span className={`absolute inset-y-3 left-0 w-[3px] rounded-r-full ${p.configured ? 'bg-ok shadow-[0_0_8px_var(--color-ok)]' : 'bg-warn/70'}`} aria-hidden />
              <span className="mt-1"><Led on={p.configured ? 3 : 1} total={3} color={p.configured ? 'var(--color-ok)' : 'var(--color-warn)'} /></span>
              <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2 font-display text-[14.5px] font-semibold">{p.label}{p.mock && <Tag tone="mock">mock</Tag>}{p.configured ? <span className="flex items-center gap-1 text-[12px] text-ok"><CheckCircle2 className="size-3.5" />Ready</span> : <span className="flex items-center gap-1 text-[12px] text-warn"><CircleAlert className="size-3.5" />Not configured</span>}</div>
                <div className="mt-0.5 text-[12.5px] text-muted">{p.message}</div>
                <div className="mono mt-1 text-[11.5px] text-faint">models: {p.models.join(', ')}</div>
                {res[p.name] && <div role="status" className={`mt-1.5 text-[12.5px] ${res[p.name].ok ? 'text-ok' : 'text-warn'}`}>{res[p.name].message}</div>}</div>
              <Button busy={busy === p.name} onClick={() => test(p.name)}>Test connection</Button>
            </div>))}
          <div className="px-4 py-3 text-[12px] text-faint">The Anthropic adapter is implemented but has not been exercised against the live API in this build (no credentials were available). Set <span className="mono">ANTHROPIC_API_KEY</span> and use Test connection to verify.</div>
        </Panel>
        <Credentials tools={meta.data.tools} onChanged={meta.reload} />
        <Panel title="Tool catalog" pad={false}>
          <div className="overflow-x-auto"><table className="w-full text-left text-[12.5px]"><thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint"><th className="px-4 py-2.5">Tool</th><th>Kind</th><th>Side effect</th><th>Idempotency key</th><th>Replay</th></tr></thead><tbody>
            {meta.data.tools.map((t: any) => <tr key={t.name} className="border-b border-line/60 transition-colors last:border-0 hover:bg-white/[.025]"><td className="px-4 py-2.5"><div className="mono text-fg">{t.name}</div><div className="text-muted">{t.description}</div>{t.credentials?.length > 0 && <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid={`cred-${t.name}`}>{t.credentials.map((c: string) => <Tag key={c} tone={t.configured ? 'ok' : 'warn'}>{c}</Tag>)}<span className="text-[11.5px] text-muted">{t.configured ? 'configured' : 'not set — add to the backend environment and restart'}</span></div>}</td><td>{t.mock ? <Tag tone="mock">mock</Tag> : <Tag tone="ok">real</Tag>}</td><td>{t.side_effect ? <Tag tone="warn">yes · approval</Tag> : 'no'}</td><td>{t.supports_idempotency_key ? 'supported' : '—'}</td><td className="pr-4 text-muted">{t.replay_safe ? 'deterministic' : 'needs fixture'}</td></tr>)}</tbody></table></div>
        </Panel>
        <Panel title="Mock outbox — proof of what mock side-effect tools actually did" pad={false}>
          {outbox.data?.length ? <div className="overflow-x-auto"><table className="w-full text-left text-[12.5px]"><tbody>{outbox.data.map((o: any) => <tr key={o.id} className="border-b border-line/60 last:border-0"><td className="px-4 py-2 mono">{o.tool}</td><td className="mono max-w-[520px] truncate text-muted">{JSON.stringify(o.payload)}</td><td>{o.approval_id ? <Tag tone="ok">approved</Tag> : <Tag tone="bad">no approval</Tag>}</td><td className="px-4 text-right text-muted">{ago(o.created_at)}</td></tr>)}</tbody></table></div> : <div className="p-4 text-muted">Nothing has been “sent” yet. Fixture replays never write here.</div>}
        </Panel>
      </div>
    </>
  )
}
