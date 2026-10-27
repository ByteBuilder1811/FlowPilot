import { memo, useMemo } from 'react'
import { BaseEdge, Background, BackgroundVariant, Controls, EdgeLabelRenderer, ReactFlow, ReactFlowProvider, getSmoothStepPath, type Edge, type EdgeProps, type Node } from '@xyflow/react'
import FlowNode from './FlowNode'
import { NODE_META } from './nodeMeta'

const nodeTypes = Object.fromEntries(Object.keys(NODE_META).map(k => [k, FlowNode]))
const REDUCED = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Smoothstep edge; on the active execution path it carries travelling energy pulses. */
function EnergyEdge(p: EdgeProps) {
  const [path, lx, ly] = getSmoothStepPath(p)
  const live = (p as any).className?.includes?.('fp-edge-live') || (p.data as any)?.live
  return (
    <>
      <BaseEdge id={p.id} path={path} markerEnd={p.markerEnd} style={p.style} />
      {live && !REDUCED && [0, 0.6].map(d => (
        <circle key={d} r="3.2" fill="#b9f3fb" style={{ filter: 'drop-shadow(0 0 4px #22d3ee) drop-shadow(0 0 10px #22d3ee)' }}>
          <animateMotion dur="1.2s" begin={`${d}s`} repeatCount="indefinite" path={path} />
        </circle>))}
      {p.label && <EdgeLabelRenderer><div className="nodrag nopan pointer-events-none absolute rounded-md border border-line bg-[#0d1019]/95 px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[.12em] text-muted" style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)` }}>{p.label as string}</div></EdgeLabelRenderer>}
    </>
  )
}
const edgeTypes = { energy: EnergyEdge }
const LIVE = ['running', 'retrying', 'waiting', 'waiting_for_approval']

function RunGraph({ definition, steps, current, onSelect, selected }: { definition: any; steps: any[]; current?: string | null; onSelect: (id: string) => void; selected?: string | null }) {
  const status = useMemo(() => {
    const m: Record<string, string> = {}
    steps.forEach(s => { m[s.node_id] = s.status === 'failed' && s.handle === 'error' ? 'succeeded' : s.status })
    return m
  }, [steps])
  const defKey = JSON.stringify(definition)
  const sig = JSON.stringify(status) + (current ?? '') + (selected ?? '')
  const { nodes, edges } = useMemo(() => {
    const st = (id: string) => status[id] ?? (current === id ? 'running' : undefined)
    return {
      nodes: definition.nodes.map((n: any) => ({ id: n.id, type: n.type, position: n.position, selected: n.id === selected, draggable: false, connectable: false, data: { ...n.data, __run: true, __status: st(n.id) } })) as Node[],
      edges: definition.edges.map((e: any) => {
        const s = st(e.source), t = st(e.target)
        const live = s === 'succeeded' && !!t && LIVE.includes(t)
        const cls = live ? 'fp-edge-live' : s === 'succeeded' && t === 'failed' ? 'fp-edge-failed' : s === 'succeeded' && t ? 'fp-edge-done' : 'fp-edge-idle'
        return { id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? 'out', targetHandle: 'in', type: 'energy', data: { live }, label: e.sourceHandle && e.sourceHandle !== 'out' ? e.sourceHandle : undefined, animated: live, className: cls }
      }) as Edge[],
    }
  }, [defKey, sig]) // eslint-disable-line
  return (
    <ReactFlowProvider>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView fitViewOptions={{ padding: 0.08, maxZoom: 1.1 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable
        onNodeClick={(_, n) => onSelect(n.id)} proOptions={{ hideAttribution: true }} minZoom={0.2}>
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.3} color="#262c40" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </ReactFlowProvider>
  )
}

export default memo(RunGraph)
