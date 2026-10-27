import { Play, Flag, Sparkles, Wrench, Globe, Shuffle, Variable, GitBranch, Repeat, UserCheck, Timer, RotateCcw, Network } from 'lucide-react'

export const NODE_META: Record<string, { label: string; icon: any; color: string; outputs: string[]; blurb: string; category: string }> = {
  start: { label: 'Start / Trigger', icon: Play, color: '#3ecf8e', outputs: ['out'], blurb: 'Entry point. Manual or webhook.', category: 'Flow' },
  end: { label: 'End / Output', icon: Flag, color: '#9aa1b5', outputs: [], blurb: 'Produces the run output.', category: 'Flow' },
  llm: { label: 'LLM / Agent', icon: Sparkles, color: '#8b7cf6', outputs: ['out', 'error'], blurb: 'Model call with optional tool loop.', category: 'AI' },
  tool: { label: 'Tool Call', icon: Wrench, color: '#5aa9f5', outputs: ['out', 'error'], blurb: 'Invoke a registered tool.', category: 'Actions' },
  http: { label: 'HTTP Request', icon: Globe, color: '#5aa9f5', outputs: ['out', 'error'], blurb: 'SSRF-protected HTTP call.', category: 'Actions' },
  transform: { label: 'Transform', icon: Shuffle, color: '#f0b429', outputs: ['out', 'error'], blurb: 'Safe, declarative data operations.', category: 'Data' },
  set_variable: { label: 'Set Variable', icon: Variable, color: '#f0b429', outputs: ['out'], blurb: 'Store a value in vars.', category: 'Data' },
  condition: { label: 'Condition / Branch', icon: GitBranch, color: '#e879a6', outputs: ['true', 'false'], blurb: 'Route on a boolean expression.', category: 'Flow' },
  loop: { label: 'Loop', icon: Repeat, color: '#e879a6', outputs: ['body', 'done'], blurb: 'Bounded iteration (strict max).', category: 'Flow' },
  approval: { label: 'Human Approval', icon: UserCheck, color: '#8b7cf6', outputs: ['approved', 'rejected'], blurb: 'Pause for a human decision.', category: 'Safety' },
  delay: { label: 'Delay', icon: Timer, color: '#9aa1b5', outputs: ['out'], blurb: 'Wait (cancellable).', category: 'Flow' },
  subworkflow: { label: 'Sub-workflow / Map', icon: Network, color: '#22d3ee', outputs: ['out', 'error'], blurb: 'Call another workflow, or fan out over a list in parallel.', category: 'Flow' },
  retry: { label: 'Retry / Recovery Policy', icon: RotateCcw, color: '#f0b429', outputs: ['out'], blurb: 'Retry policy for the next node.', category: 'Safety' },
}

export const DEFAULT_CONFIG: Record<string, any> = {
  start: { trigger: 'manual', input_schema: {} },
  end: { output: '{{ input }}' },
  llm: { provider: 'mock', model: 'mock-agent-1', system: '', prompt: 'Summarize: {{ input }}', tools: [], max_iterations: 4, timeout_s: 60 },
  tool: { tool: 'mock.search', args: { query: '{{ input.topic }}' } },
  http: { method: 'GET', url: 'https://example.org/', timeout_s: 15 },
  transform: { op: 'template', template: '{{ input }}' },
  set_variable: { name: 'result', value: '{{ input }}' },
  condition: { expression: 'input.value > 0' },
  loop: { mode: 'count', count: 3, max_iterations: 10, on_limit: 'fail' },
  approval: { title: 'Approval required', message: '', details: {} },
  delay: { seconds: 1 },
  subworkflow: { workflow_id: '', mode: 'single', input: {}, max_items: 10, on_item_error: 'fail' },
  retry: { max_attempts: 3, backoff_ms: 500, max_backoff_ms: 8000, safe_to_retry: false },
}

export function summary(type: string, c: any): string {
  switch (type) {
    case 'start': return c.trigger === 'webhook' ? 'Webhook trigger' : 'Manual trigger'
    case 'llm': return `${c.agent_snapshot?.agent_name ?? c.provider ?? '—'}${c.model ? ' · ' + c.model : ''}`
    case 'tool': return c.tool ?? 'no tool selected'
    case 'http': return `${c.method ?? 'GET'} ${c.url ?? ''}`
    case 'transform': return c.op ?? 'no operation'
    case 'set_variable': return `vars.${c.name ?? '?'}`
    case 'condition': return c.expression ?? ''
    case 'loop': return `${c.mode ?? 'count'} · max ${c.max_iterations ?? '?'}`
    case 'approval': return c.title ?? ''
    case 'delay': return `${c.seconds ?? 0}s`
    case 'subworkflow': return c.mode === 'foreach' ? `map · ${c.items ?? '?'}` : `call ${c.workflow_id ? c.workflow_id.slice(-6) : '?'}`
    case 'retry': return `${c.max_attempts ?? 1} attempts · ${c.backoff_ms ?? 0}ms backoff`
    default: return ''
  }
}
