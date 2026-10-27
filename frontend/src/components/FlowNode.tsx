import { Handle, Position, type NodeProps } from '@xyflow/react'
import { NODE_META, summary } from './nodeMeta'
import { statusColor } from './ui'

const STATUS_LABEL: Record<string, string> = { running: 'running', waiting: 'waiting', waiting_for_approval: 'awaiting', retrying: 'retrying', succeeded: 'done', failed: 'failed', cancelled: 'cancelled', skipped: 'skipped' }

export default function FlowNode({ data, type, selected, id }: NodeProps) {
  const m = NODE_META[type as string]
  if (!m) return null
  const d = data as any
  const Icon = m.icon
  const outs = m.outputs
  const status: string | undefined = d.__status
  const live = status === 'running' || status === 'retrying'
  const wait = status === 'waiting' || status === 'waiting_for_approval'
  const fx = status === 'failed' ? 'fp-node-bad' : live ? 'fp-node-live' : wait ? 'fp-node-wait' : status === 'succeeded' ? 'fp-node-ok' : selected ? 'fp-node-sel' : ''
  const border = status ? 'border-transparent' : d.__issue === 'error' ? 'border-bad/70' : d.__issue === 'warning' ? 'border-warn/60' : 'border-line-strong'
  const rail = status ? statusColor(status) : d.__issue === 'error' ? 'var(--color-bad)' : d.__issue === 'warning' ? 'var(--color-warn)' : m.color
  const dim = d.__run && !status
  return (
    <div data-testid={`node-${id}`} className={`fp-node relative w-[224px] rounded-xl border bg-[#0d1019] ${border} ${fx} ${dim ? 'opacity-50' : ''}`}
      style={{ boxShadow: fx ? undefined : '0 14px 30px -18px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.04)' }}>
      {type !== 'start' && <Handle type="target" position={Position.Left} id="in" />}
      {/* status / type rail */}
      <span className="pointer-events-none absolute inset-y-2 left-0 w-[3px] rounded-r-full" style={{ background: rail, boxShadow: status ? `0 0 10px ${rail}` : undefined }} aria-hidden />
      <div className="flex items-center gap-2 overflow-hidden rounded-t-xl border-b border-white/[.06] py-2 pl-3 pr-2.5" style={{ background: `linear-gradient(100deg, ${m.color}2e, ${m.color}0a 55%, transparent)` }}>
        <span className="grid size-6 shrink-0 place-items-center rounded-lg" style={{ background: m.color + '26', color: m.color, boxShadow: `inset 0 0 0 1px ${m.color}55` }}><Icon className="size-3.5" /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-display text-[13px] font-semibold leading-tight">{d.label || m.label}</span>
          <span className="block truncate font-mono text-[9.5px] uppercase tracking-[.12em]" style={{ color: m.color }}>{type as string}</span>
        </span>
        {d.__issue && !status && <span title="Has validation issues" className={`size-2 rounded-full ${d.__issue === 'error' ? 'bg-bad shadow-[0_0_8px_var(--color-bad)]' : 'bg-warn shadow-[0_0_8px_var(--color-warn)]'}`} />}
        {status && <span className="flex items-center gap-1 rounded-full px-1.5 py-px font-mono text-[9.5px] uppercase tracking-wider" style={{ color: statusColor(status), background: 'rgba(0,0,0,.35)', boxShadow: `inset 0 0 0 1px ${statusColor(status)}55` }}>
          <span className={`size-1.5 rounded-full bg-current ${live || wait ? 'signal-dot' : ''}`} />{STATUS_LABEL[status] ?? status}</span>}
      </div>
      <div className="truncate py-2 pl-3 pr-2.5 font-mono text-[11px] text-muted">{summary(type as string, d.config ?? {}) || m.label}</div>
      {outs.length === 1 && outs[0] === 'out' && <Handle type="source" position={Position.Right} id="out" />}
      {outs.length > 1 && (
        <div className="border-t border-white/[.06] py-1 pl-3 pr-2.5">
          {outs.map(o => (
            <div key={o} className="relative flex h-[19px] items-center justify-end font-mono text-[9.5px] uppercase tracking-[.12em] text-faint">
              {o === 'error' ? <span className="text-bad/90">on error</span> : o}
              <Handle type="source" position={Position.Right} id={o} style={{ top: '50%', right: -16 }} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
