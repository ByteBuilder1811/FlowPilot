import { useEffect, useId, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { LayoutDashboard, Workflow, Bot, Activity, FlaskConical, GitCompareArrows, Plug, Settings, ShieldAlert, PanelLeftClose, PanelLeftOpen, BookOpen, Gauge, BarChart3, LayoutTemplate } from 'lucide-react'
import { api } from '../api'
import { useAsync } from '../hooks'
import { SysLabel } from './ui'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/workflows', label: 'Workflows', icon: Workflow },
  { to: '/templates', label: 'Templates', icon: LayoutTemplate },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/knowledge', label: 'Knowledge', icon: BookOpen },
  { to: '/runs', label: 'Runs', icon: Activity },
  { to: '/lab', label: 'Failure Lab', icon: FlaskConical },
  { to: '/compare', label: 'Compare', icon: GitCompareArrows },
  { to: '/evals', label: 'Evals', icon: Gauge },
  { to: '/insights', label: 'Insights', icon: BarChart3 },
]
const NAV2 = [
  { to: '/integrations', label: 'Integrations', icon: Plug },
  { to: '/settings', label: 'Settings', icon: Settings },
]

/** Animated gradient logo mark: a flight path with a signal beacon. */
export function LogoMark({ size = 30 }: { size?: number }) {
  const gid = 'fpl' + useId().replace(/[^a-zA-Z0-9]/g, '')
  return (
    <div className="logo-mark shrink-0" style={{ width: size, height: size }} aria-hidden>
      <div className="grid size-full place-items-center">
        <svg viewBox="0 0 24 24" width={Math.round(size * 0.62)} height={Math.round(size * 0.62)}>
          <defs><linearGradient id={gid} x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="#a194ff" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
          <path d="M3 18.5 10.2 4.5l3.2 9.2 6.1-3.1" fill="none" stroke={`url(#${gid})`} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="19.6" cy="10.6" r="2.3" fill="#22d3ee" />
        </svg>
      </div>
    </div>
  )
}

function Item({ to, label, icon: Icon, end, badge, collapsed }: any) {
  return (
    <NavLink to={to} end={end} title={collapsed ? label : undefined}
      className={({ isActive }) => `group relative flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] transition-all max-md:shrink-0 max-md:flex-col max-md:gap-0.5 max-md:px-2.5 max-md:py-1.5 ${collapsed ? 'md:justify-center md:px-0' : ''} ${isActive ? 'bg-gradient-to-r from-accent/20 to-accent/[.04] text-fg shadow-[inset_0_0_0_1px_rgba(161,148,255,.25)]' : 'text-muted hover:bg-white/[.05] hover:text-fg'}`}>
      {({ isActive }) => <>
        {isActive && <span className="absolute -left-2.5 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-signal shadow-[0_0_10px_rgba(34,211,238,.9)] max-md:hidden" aria-hidden />}
        <Icon className={`size-[17px] shrink-0 ${isActive ? 'text-accent' : ''}`} strokeWidth={1.75} />
        <span className={`flex-1 truncate max-md:sr-only ${collapsed ? 'md:sr-only' : ''}`}>{label}</span>
        {badge ? <span className={`rounded-full bg-signal px-1.5 font-mono text-[10px] font-bold text-[#04131a] shadow-[0_0_12px_rgba(34,211,238,.7)] ${collapsed ? 'md:absolute md:right-1 md:top-0.5' : ''} max-md:absolute max-md:right-0.5 max-md:top-0`}>{badge}</span> : null}
      </>}
    </NavLink>
  )
}

const readPref = () => { try { return localStorage.getItem('fp.sidebar') } catch { return null } }

export default function Layout() {
  const { data, error } = useAsync(() => api.get('/api/dashboard'), [], 4000)
  const { pathname } = useLocation()
  const [pref, setPref] = useState<string | null>(readPref)
  // Studio and run pages get the full canvas by default; an explicit choice always wins.
  const collapsed = pref ? pref === 'collapsed' : /^\/(workflows|runs)\/[^/]+/.test(pathname)
  useEffect(() => { try { if (pref) localStorage.setItem('fp.sidebar', pref) } catch { /* storage unavailable */ } }, [pref])
  const active = data ? ['running', 'retrying', 'queued', 'waiting_for_approval'].reduce((n, k) => n + (data.run_counts?.[k] ?? 0), 0) : 0

  return (
    <div className="flex h-full max-md:flex-col">
      {/* mobile top bar */}
      <div className="glass-flat flex h-12 shrink-0 items-center gap-2.5 border-b border-line px-4 md:hidden">
        <LogoMark size={26} /><span className="font-display text-[15px] font-semibold tracking-tight">FlowPilot</span>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-wider text-ok"><span className="signal-dot size-1.5 rounded-full bg-ok" />local</span>
      </div>
      <aside className={`glass-flat z-40 flex shrink-0 flex-col border-r border-line transition-[width] duration-200 max-md:fixed max-md:inset-x-0 max-md:bottom-0 max-md:h-[60px] max-md:w-full max-md:flex-row max-md:border-r-0 max-md:border-t ${collapsed ? 'md:w-[64px]' : 'md:w-[232px]'}`}>
        <div className={`flex h-[60px] items-center gap-2.5 border-b border-line max-md:hidden ${collapsed ? 'justify-center px-0' : 'px-4'}`}>
          <LogoMark />
          {!collapsed && <div className="min-w-0"><div className="font-display text-[16px] font-bold leading-none tracking-tight">Flow<span className="text-signal">Pilot</span></div><div className="kicker mt-1.5 text-[9px] leading-none text-faint">agent flight deck</div></div>}
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 p-2.5 max-md:flex-row max-md:items-center max-md:justify-between max-md:overflow-x-auto max-md:p-1.5" aria-label="Primary">
          {!collapsed && <div className="kicker px-2.5 pb-1.5 pt-1 text-[9.5px] text-faint max-md:hidden">Operate</div>}
          {NAV.map(n => <Item key={n.to} {...n} collapsed={collapsed} badge={n.to === '/runs' && data?.pending_approvals ? data.pending_approvals : 0} />)}
          <div className="my-2.5 border-t border-line max-md:hidden" />
          {!collapsed && <div className="kicker px-2.5 pb-1.5 text-[9.5px] text-faint max-md:hidden">Configure</div>}
          {NAV2.map(n => <Item key={n.to} {...n} collapsed={collapsed} />)}
        </nav>
        <div className={`border-t border-line p-2.5 text-[11px] text-faint max-md:hidden`}>
          {!collapsed && <div className="mb-2 rounded-lg border border-line bg-white/[.02] p-2.5">
            <div className="mb-1.5 flex items-center justify-between">{error ? <SysLabel tone="bad">SYS // NO SIGNAL</SysLabel> : data ? <SysLabel tone="ok" live>SYS // ONLINE</SysLabel> : <SysLabel tone="faint">SYS // BOOT</SysLabel>}
              {active > 0 && <span className="font-mono text-[10px] text-signal">{active} in flight</span>}</div>
            <div className="flex items-center gap-2 text-ok"><span className="font-medium">Local workspace</span></div>
            <div className="mt-1.5 flex items-start gap-1.5"><ShieldAlert className="mt-px size-3.5 shrink-0" />Single-user mode. Not hardened for multi-tenant use.</div>
          </div>}
          <button onClick={() => setPref(collapsed ? 'expanded' : 'collapsed')} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-muted transition-colors hover:bg-white/[.05] hover:text-fg ${collapsed ? 'justify-center px-0' : ''}`}>
            {collapsed ? <PanelLeftOpen className="size-4" /> : <><PanelLeftClose className="size-4" /><span>Collapse</span></>}
          </button>
        </div>
      </aside>
      <main className="min-h-0 min-w-0 flex-1 overflow-auto max-md:pb-[60px]">
        {/* keyed by top-level section only, so in-section navigation never remounts a page */}
        <div key={pathname.split('/')[1] || 'root'} className="route-in h-full"><Outlet /></div>
      </main>
      <div className="boot-scan" aria-hidden />
    </div>
  )
}
