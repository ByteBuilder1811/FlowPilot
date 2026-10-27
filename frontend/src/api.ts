export class ApiError extends Error {
  status: number; code: string; detail: any
  constructor(status: number, detail: any) {
    super(typeof detail === 'string' ? detail : detail?.message || `HTTP ${status}`)
    this.status = status; this.code = detail?.code || 'error'; this.detail = detail
  }
}

let tokenPromise: Promise<string> | null = null
const token = () => (tokenPromise ??= fetch('/api/session').then(r => r.json()).then(j => j.token as string))

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-FlowPilot-Token'] = await token()
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new ApiError(r.status, data?.detail ?? data)
  return data as T
}

export const api = {
  get: <T = any>(p: string) => req<T>('GET', p),
  post: <T = any>(p: string, b: unknown = {}) => req<T>('POST', p, b),
  put: <T = any>(p: string, b: unknown) => req<T>('PUT', p, b),
  patch: <T = any>(p: string, b: unknown) => req<T>('PATCH', p, b),
  del: <T = any>(p: string) => req<T>('DELETE', p),
}
export const TERMINAL = ['succeeded', 'failed', 'cancelled', 'timed_out']
