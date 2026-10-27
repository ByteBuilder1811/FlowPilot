import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { KeyRound, Link2, Trash2 } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { Button, Field, inputCls, Panel, SysLabel, Tag } from './ui'

export default function Credentials({ tools, onChanged }: { tools: any[]; onChanged: () => void }) {
  const creds = useAsync(() => api.get('/api/credentials'), [])
  const apps = useAsync(() => api.get('/api/oauth/apps'), [])
  const [sp, setSp] = useSearchParams()
  const names = [...new Set<string>(tools.flatMap((t: any) => t.credentials ?? []))]
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [msg, setMsg] = useState<{ k: 'ok' | 'err'; t: string } | null>(null)
  const [app, setApp] = useState({ name: '', auth_url: '', token_url: '', client_id: '', client_secret: '', scopes: '', credential_name: 'GOOGLE_SHEETS_TOKEN' })
  const [redirect, setRedirect] = useState('')
  const flash = (k: 'ok' | 'err', t: string) => { setMsg({ k, t }); setTimeout(() => setMsg(null), 5000) }
  async function save() {
    try { await api.put(`/api/credentials/${name}`, { value }); setValue(''); setName(''); flash('ok', 'Saved to the encrypted vault. The value will not be shown again.'); creds.reload(true); onChanged() } catch (e: any) { flash('err', e.message) }
  }
  async function addApp() {
    try { const r = await api.post('/api/oauth/apps', app); setRedirect(r.redirect_uri); setApp({ ...app, name: '', client_secret: '' }); apps.reload(true) } catch (e: any) { flash('err', e.message) }
  }
  async function connect(n: string) { try { window.location.href = (await api.post(`/api/oauth/apps/${n}/start`)).url } catch (e: any) { flash('err', e.message) } }
  const connected = sp.get('connected'), oerr = sp.get('oauth_error')
  return (
    <Panel title={<span className="flex items-center gap-2"><KeyRound className="size-3.5" />Credentials vault</span>} actions={<SysLabel tone="faint">AES // FERNET</SysLabel>} hud="violet">
      <div className="space-y-4" data-testid="credentials">
        {connected && <div role="status" className="rounded-md border border-ok/30 bg-ok/5 px-3 py-2 text-ok">Connected “{connected}”. The token is stored encrypted and refreshes automatically.<button className="ml-3 underline" onClick={() => setSp({})}>dismiss</button></div>}
        {oerr && <div role="alert" className="rounded-md border border-bad/30 bg-bad/5 px-3 py-2 text-bad">OAuth failed ({oerr}).<button className="ml-3 underline" onClick={() => setSp({})}>dismiss</button></div>}
        <p className="text-[12.5px] text-muted">Secrets are encrypted at rest, never shown again, never included in tool arguments, traces, fixtures or exports. Connectors check the vault first, then environment variables.</p>
        <div className="grid items-end gap-3 md:grid-cols-[1fr_1.4fr_auto]">
          <Field label="Credential name"><input className={inputCls} list="cred-names" value={name} onChange={e => setName(e.target.value.toUpperCase())} placeholder="SLACK_WEBHOOK_URL" aria-label="Credential name" /><datalist id="cred-names">{names.map(n => <option key={n} value={n} />)}</datalist></Field>
          <Field label="Secret value"><input className={inputCls} type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} aria-label="Secret value" /></Field>
          <Button variant="primary" disabled={!name || !value} onClick={save} data-testid="save-credential">Save secret</Button>
        </div>
        {msg && <div role="status" className={msg.k === 'ok' ? 'text-ok' : 'text-bad'}>{msg.t}</div>}
        {creds.data?.length ? <ul className="divide-y divide-line/60 rounded-md border border-line" data-testid="credential-list">{creds.data.filter((c: any) => !c.name.endsWith('__REFRESH') && !c.name.endsWith('__CLIENT_SECRET')).map((c: any) => (
          <li key={c.name} className="flex items-center gap-3 px-3 py-2 text-[12.5px]"><span className="mono">{c.name}</span><Tag tone={c.kind === 'oauth' ? 'accent' : 'neutral'}>{c.kind}</Tag>{c.meta?.expires_at && <span className="text-faint">expires {new Date(c.meta.expires_at).toLocaleString()}</span>}<span className="ml-auto text-faint">{'•'.repeat(10)}</span>
            <Button size="sm" variant="ghost" aria-label={`Remove ${c.name}`} onClick={async () => { await api.del(`/api/credentials/${c.name}`); creds.reload(true); onChanged() }}><Trash2 className="size-3.5" /></Button></li>))}</ul> : <p className="text-[12.5px] text-faint">No credentials in the vault yet.</p>}

        <div className="border-t border-line pt-4"><div className="mb-2 flex items-center gap-2 font-medium"><Link2 className="size-4" />OAuth apps</div>
          <p className="mb-2 text-[12px] text-muted">Authorization-code flow with PKCE. Register this redirect URI with the provider: <span className="mono">{redirect || `${location.origin}/api/oauth/callback`}</span></p>
          {apps.data?.map((a: any) => <div key={a.name} className="mb-1.5 flex items-center gap-2 text-[12.5px]"><span className="mono">{a.name}</span><span className="text-faint">→ {a.credential_name}</span>{a.connected ? <Tag tone="ok">connected</Tag> : <Tag tone="warn">not connected</Tag>}
            <Button size="sm" className="ml-auto" onClick={() => connect(a.name)}>{a.connected ? 'Reconnect' : 'Connect'}</Button><Button size="sm" variant="ghost" aria-label={`Delete app ${a.name}`} onClick={async () => { await api.del(`/api/oauth/apps/${a.name}`); apps.reload(true); creds.reload(true) }}><Trash2 className="size-3.5" /></Button></div>)}
          <details className="mt-2"><summary className="cursor-pointer text-[12.5px] text-accent">Add an OAuth app</summary>
            <div className="mt-2 grid gap-2 md:grid-cols-2">{(['name', 'auth_url', 'token_url', 'client_id', 'client_secret', 'scopes', 'credential_name'] as const).map(k => (
              <Field key={k} label={k.replace('_', ' ')}><input className={inputCls} type={k === 'client_secret' ? 'password' : 'text'} value={(app as any)[k]} onChange={e => setApp({ ...app, [k]: e.target.value })} aria-label={`OAuth ${k}`} /></Field>))}</div>
            <div className="mt-2"><Button onClick={addApp} disabled={!app.name || !app.auth_url || !app.token_url || !app.client_id}>Save OAuth app</Button></div></details></div>
      </div>
    </Panel>
  )
}
