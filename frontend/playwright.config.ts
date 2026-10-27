import { defineConfig } from '@playwright/test'
import os from 'os'
import path from 'path'

const data = path.join(os.tmpdir(), `fp_e2e_${Date.now()}`)
const root = path.resolve(process.cwd(), '..')

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5174', viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'off' },
  webServer: [
    { command: `"${root}/.venv/bin/python" -m uvicorn backend.main:app --port 8766 --app-dir "${root}"`, cwd: root, port: 8766, reuseExistingServer: false, timeout: 60_000,
      env: { FLOWPILOT_DATA_DIR: data, FLOWPILOT_DB_URL: `sqlite:///${data}/e2e.db`, FLOWPILOT_API_TOKEN: 'e2e-token' } },
    { command: 'npm run dev -- --port 5174', port: 5174, reuseExistingServer: false, env: { FP_API: 'http://127.0.0.1:8766' } },
  ],
})
