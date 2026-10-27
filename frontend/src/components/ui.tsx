import { type ReactNode, type ButtonHTMLAttributes, useEffect, useId, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Loader2, AlertTriangle, Inbox } from 'lucide-react'

const STATUS: Record<string, { c: string; label: string; pulse?: boolean }> = {
  queued: { c: 'text-muted bg-muted/10 border-muted/25', label: 'Queued' },
  running: { c: 'text-signal bg-signal/10 border-signal/35', label: 'Running', pulse: true },
  retrying: { c: 'text-warn bg-warn/10 border-warn/35', label: 'Retrying', pulse: true },
  waiting_for_approval: { c: 'text-accent bg-accent/10 border-accent/40', label: 'Needs approval', pulse: true },
  waiting_for_child: { c: 'text-accent bg-accent/10 border-accent/40', label: 'Waiting on children', pulse: true },
  succeeded: { c: 'text-ok bg-ok/10 border-ok/35', label: 'Succeeded' },
  failed: { c: 'text-bad bg-bad/10 border-bad/40', label: 'Failed' },
  cancelled: { c: 'text-muted bg-muted/10 border-muted/25', label: 'Cancelled' },
  timed_out: { c: 'text-warn bg-warn/10 border-warn/35', label: 'Timed out' },
  passed: { c: 'text-ok bg-ok/10 border-ok/35', label: 'Passed' },
  inconclusive: { c: 'text-warn bg-warn/10 border-warn/35', label: 'Inconclusive' },
  pending: { c: 'text-accent bg-accent/10 border-accent/40', label: 'Pending', pulse: true },
  approved: { c: 'text-ok bg-ok/10 border-ok/35', label: 'Approved' },
  rejected: { c: 'text-bad bg-bad/10 border-bad/40', label: 'Rejected' },
  waiting: { c: 'text-accent bg-accent/10 border-accent/40', label: 'Waiting', pulse: true },
}

/** CSS colour for a run/step status — used by rails, graph nodes and glyphs. */
export const statusColor = (s?: string | null) =>
  !s ? 'var(--color-line-strong)'
    : ['succeeded', 'passed', 'approved'].includes(s) ? 'var(--color-ok)'
      : ['failed', 'rejected'].includes(s) ? 'var(--color-bad)'
        : ['running'].includes(s) ? 'var(--color-signal)'
          : ['retrying', 'timed_out', 'inconclusive'].includes(s) ? 'var(--color-warn)'
            : ['waiting_for_approval', 'waiting_for_child', 'pending', 'waiting'].includes(s) ? 'var(--color-accent)'
              : 'var(--color-faint)'

export function StatusBadge({ status, className = '', size = 'sm' }: { status: string; className?: string; size?: 'sm' | 'lg' }) {
  const s = STATUS[status] ?? { c: 'text-muted bg-muted/10 border-muted/25', label: status }
  return (
    <span data-testid="status-badge" data-status={status} className={`badge-glow inline-flex items-center gap-1.5 rounded-full border font-medium whitespace-nowrap ${size === 'lg' ? 'px-2.5 py-1 text-[12px]' : 'px-2 py-[3px] text-[11px]'} ${s.c} ${className}`}>
      <span className={`size-1.5 rounded-full bg-current ${s.pulse ? 'signal-dot' : ''}`} />{s.label}
    </span>
  )
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'mock' | 'warn' | 'accent' | 'ok' | 'bad' }) {
  const t = { neutral: 'border-line-strong text-muted bg-white/[.03]', mock: 'border-warn/40 text-warn bg-warn/10', warn: 'border-warn/40 text-warn bg-warn/10', accent: 'border-accent/40 text-accent bg-accent/10', ok: 'border-ok/40 text-ok bg-ok/10', bad: 'border-bad/40 text-bad bg-bad/10' }[tone]
  return <span className={`inline-flex items-center rounded-md border px-1.5 py-px font-mono text-[10px] font-semibold uppercase tracking-[.08em] ${t}`}>{children}</span>
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'sm' | 'md'; busy?: boolean }
export function Button({ variant = 'secondary', size = 'md', busy, className = '', children, disabled, ...p }: BtnProps) {
  const v = {
    primary: 'btn-primary border-transparent',
    secondary: 'bg-white/[.045] text-fg border-line-strong hover:bg-white/[.09] hover:border-[#3a4160] shadow-[inset_0_1px_0_rgba(255,255,255,.05)]',
    ghost: 'bg-transparent text-muted hover:text-fg hover:bg-white/[.06] border-transparent',
    danger: 'bg-bad/10 text-bad hover:bg-bad/20 border-bad/35 hover:border-bad/60',
  }[variant]
  return (
    <button {...p} disabled={disabled || busy} className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg border font-medium transition-all duration-150 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-45 ${size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-[13px]'} ${v} ${className}`}>
      {busy && <Loader2 className="size-3.5 animate-spin" />}{children}
    </button>
  )
}

/** mono "command path" derived from the route — purely decorative breadcrumb. */
function RoutePath() {
  const { pathname } = useLocation()
  const segs = pathname.split('/').filter(Boolean)
  return (
    <div className="kicker flex items-center gap-1.5 text-faint" aria-hidden>
      <span className="text-accent">❯</span><span>flowpilot</span>
      {(segs.length ? segs : ['overview']).map((s, i) => <span key={i} className="flex items-center gap-1.5"><span className="text-line-strong">/</span><span className={i === segs.length - 1 || !segs.length ? 'text-muted' : ''}>{s}</span></span>)}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3 border-b border-line/80 px-6 pb-4 pt-5 max-sm:px-4">
      <div className="min-w-0">
        <RoutePath />
        <h1 className="font-display mt-1.5 text-[22px] font-semibold leading-tight tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-[13px] text-muted">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  )
}

export function Panel({ title, actions, children, className = '', pad = true, hero = false, hud = 'signal' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean; hero?: boolean; hud?: 'signal' | 'violet' | 'warn' | 'bad' | false }) {
  return (
    <section className={`glass rounded-xl ${hero ? 'hairline' : ''} ${hud ? `hud ${hud !== 'signal' ? `hud-${hud}` : ''}` : ''} ${className}`}>
      {title && <header className="flex items-center justify-between gap-3 border-b border-line/80 px-4 py-2.5"><h2 className="flex items-center gap-2 font-mono text-[10.5px] font-semibold uppercase tracking-[.14em] text-muted"><span className="h-3 w-[3px] shrink-0 rounded-full bg-signal" aria-hidden />{title}</h2>{actions}</header>}
      <div className={pad ? 'p-4' : ''}>{children}</div>
    </section>
  )
}

/** Decorative radar illustration used by empty states. */
export function EmptyArt({ icon }: { icon?: ReactNode }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  return (
    <div className="relative grid size-28 place-items-center" aria-hidden>
      <svg viewBox="0 0 112 112" className="absolute inset-0 size-full">
        <defs>
          <radialGradient id={`rg${id}`} cx="50%" cy="50%" r="50%"><stop offset="0" stopColor="#6d5cf5" stopOpacity=".28" /><stop offset="1" stopColor="#6d5cf5" stopOpacity="0" /></radialGradient>
          <linearGradient id={`lg${id}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#a194ff" /><stop offset="1" stopColor="#22d3ee" /></linearGradient>
        </defs>
        <circle cx="56" cy="56" r="54" fill={`url(#rg${id})`} />
        {[52, 40, 28].map(r => <circle key={r} cx="56" cy="56" r={r} fill="none" stroke="#2b3147" strokeWidth="1" strokeDasharray={r === 40 ? '2 4' : undefined} />)}
        <path d="M56 2v108M2 56h108" stroke="#1d2232" strokeWidth="1" />
        <g className="orbit"><circle cx="56" cy="4" r="3" fill="#22d3ee" /><path d="M56 56 L56 4" stroke={`url(#lg${id})`} strokeWidth="1.2" opacity=".55" /></g>
        <circle cx="96" cy="70" r="2" fill="#a194ff" /><circle cx="22" cy="38" r="1.6" fill="#7d849c" />
      </svg>
      <div className="glass relative grid size-12 place-items-center rounded-2xl text-accent">{icon ?? <Inbox className="size-6" />}</div>
    </div>
  )
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="fade-in flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <EmptyArt icon={icon} />
      <div className="font-display mt-2 text-[15px] font-semibold">{title}</div>
      {children && <div className="max-w-md text-[13px] text-muted">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  const denied = (error as any).status === 401 || (error as any).status === 403
  return (
    <div role="alert" className="glass m-6 flex items-start gap-3 rounded-xl border-bad/35 p-4 shadow-[0_0_40px_-18px_rgba(248,113,113,.8)]">
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-bad/15 text-bad"><AlertTriangle className="size-4" /></span>
      <div className="min-w-0 flex-1">
        <div className="font-display font-semibold text-bad">{denied ? 'Permission denied' : 'Something went wrong'}</div>
        <div className="mt-0.5 text-muted">{error.message}</div>
        {denied && <div className="mt-1 text-faint">FlowPilot only issues its admin token to requests from this machine.</div>}
      </div>
      {retry && <Button size="sm" onClick={retry}>Retry</Button>}
    </div>
  )
}

export const Loading = ({ label = 'Loading…' }: { label?: string }) => (
  <div className="flex flex-col items-center justify-center gap-3 p-12 text-muted">
    <div className="flex items-center gap-2"><Loader2 className="size-4 animate-spin text-signal" />{label}</div>
    <div className="scanline h-px w-40 rounded-full bg-line" aria-hidden />
  </div>
)

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11.5px] font-medium text-muted">{label}</span>{children}
      {hint && <span className="mt-1 block text-[11px] text-faint">{hint}</span>}
    </label>
  )
}
export const inputCls = 'w-full rounded-lg border border-line-strong/80 bg-ink/85 px-2.5 py-1.5 text-[13px] text-fg placeholder:text-faint shadow-[inset_0_1px_2px_rgba(0,0,0,.4)] transition-colors hover:border-[#3a4160] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20'

export function Json({ value, max = 280 }: { value: unknown; max?: number }) {
  if (value === undefined || value === null) return <span className="text-faint">—</span>
  return <pre style={{ maxHeight: max }} className="mono overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-ink/90 p-2.5 text-[11.5px] leading-relaxed text-[#c5cae0]">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
}

/** Small label/value cell for dense "flight strip" metadata rows. */
export const Meta = ({ k, v }: { k: string; v: ReactNode }) => (
  <div className="min-w-0"><div className="kicker text-[9.5px] text-faint">{k}</div><div className="mt-1 truncate text-[13px]">{v}</div></div>
)

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Counts smoothly to a real value (no invented data — it only animates the transition). */
export function AnimatedNumber({ value, className = '' }: { value: number; className?: string }) {
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (reduced() || from.current === value) { setShown(value); from.current = value; return }
    const a = from.current, start = performance.now(), dur = 700
    let raf = 0
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / dur), e = 1 - Math.pow(1 - k, 4)
      setShown(Math.round(a + (value - a) * e))
      if (k < 1) raf = requestAnimationFrame(tick); else from.current = value
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); from.current = value }
  }, [value])
  return <span className={`tabular ${className}`}>{shown}</span>
}

/** Segmented LED meter. `on` segments light up; `chase` animates them (live states only). */
export function Led({ on, total = 5, color = 'var(--color-signal)', chase = false }: { on: number; total?: number; color?: string; chase?: boolean }) {
  return <span className={`inline-flex items-center gap-[3px] ${chase ? 'led-chase' : ''}`} style={{ color }} aria-hidden>{Array.from({ length: total }, (_, i) => <span key={i} className={`led ${i < on ? 'led-on' : ''}`} />)}</span>
}

/** Robotic system chrome label, e.g. SYS // LIVE. Decorative only. */
export function SysLabel({ children, tone = 'signal', live = false }: { children: ReactNode; tone?: 'signal' | 'ok' | 'warn' | 'bad' | 'violet' | 'faint'; live?: boolean }) {
  const c = { signal: 'text-signal', ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', violet: 'text-accent', faint: 'text-faint' }[tone]
  return <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] font-medium uppercase tracking-[.16em] ${c}`} aria-hidden><span className={`size-1.5 rounded-[2px] bg-current ${live ? 'signal-dot' : ''}`} />{children}</span>
}
