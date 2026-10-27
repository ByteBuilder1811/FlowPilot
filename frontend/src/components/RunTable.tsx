import { Link } from 'react-router-dom'
import { StatusBadge, Tag, statusColor } from './ui'
import { ago, fmtDur, runDuration } from '../hooks'

export default function RunTable({ runs, compact = false }: { runs: any[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <thead><tr className="border-b border-line font-mono text-[10px] uppercase tracking-[.12em] text-faint">
          <th className="py-2.5 pl-5 pr-4 font-medium">Run</th><th className="px-3 py-2.5 font-medium">Workflow</th><th className="px-3 py-2.5 font-medium">Status</th>
          {!compact && <th className="px-3 py-2.5 font-medium">Mode</th>}
          <th className="px-3 py-2.5 text-right font-medium">Duration</th><th className="px-4 py-2.5 text-right font-medium">Started</th>
        </tr></thead>
        <tbody>
          {runs.map(r => (
            <tr key={r.id} className="group border-b border-line/50 transition-colors last:border-0 hover:bg-white/[.03]" data-testid="run-row">
              <td className="relative py-2.5 pl-5 pr-4">
                <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full opacity-70 transition-opacity group-hover:opacity-100" style={{ background: statusColor(r.status) }} aria-hidden />
                <Link to={`/runs/${r.id}`} className="mono text-accent hover:text-signal hover:underline">{r.id.slice(4, 14)}</Link></td>
              <td className="max-w-[260px] truncate px-3 py-2.5 font-medium">{r.workflow_name ?? r.workflow_id}{r.version_no ? <span className="ml-1.5 font-mono text-[11px] font-normal text-faint">v{r.version_no}</span> : null}</td>
              <td className="px-3 py-2.5"><StatusBadge status={r.status} /></td>
              {!compact && <td className="px-3 py-2.5">{r.mode === 'fixture_replay' ? <Tag tone="accent">fixture replay</Tag> : <span className="text-muted">live</span>}{r.trigger === 'webhook' && <span className="ml-1.5"><Tag>webhook</Tag></span>}</td>}
              <td className="tabular px-3 py-2.5 text-right font-mono text-[12px] text-muted">{fmtDur(runDuration(r))}</td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right text-muted">{ago(r.started_at ?? r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
