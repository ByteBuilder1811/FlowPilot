import { expect, type Page, request } from '@playwright/test'

export const API = 'http://127.0.0.1:8766'
export async function apiCtx() {
  const ctx = await request.newContext({ baseURL: API, extraHTTPHeaders: { 'X-FlowPilot-Token': 'e2e-token' } })
  return ctx
}
export async function loadExample(key: string) {
  const c = await apiCtx()
  const r = await (await c.post(`/api/examples/${key}/load`)).json()
  return r as { id: string; version_id: string; sample_input: any }
}
export async function makeWorkflow(name: string, nodes: any[], edges: any[]) {
  const c = await apiCtx()
  const r = await c.post('/api/workflows', { data: { name, definition: { format: 'flowpilot.workflow/v1', nodes, edges, policy: {} } } })
  return (await r.json()).id as string
}
export const N = (id: string, type: string, label: string, config: any, x = 0, y = 0) => ({ id, type, position: { x, y }, data: { label, config } })
export const E = (s: string, t: string, h = 'out') => ({ id: `e_${s}_${h}_${t}`, source: s, sourceHandle: h, target: t })

export async function runFromStudio(page: Page, wid: string, input: any) {
  await page.goto(`/workflows/${wid}`)
  await page.getByTestId('run-open').click()
  await page.getByLabel('Run input').fill(JSON.stringify(input))
  await page.getByTestId('run-start').click()
  await expect(page).toHaveURL(/\/runs\/run_/)
}
export const badge = (page: Page) => page.getByTestId('status-badge').first()
