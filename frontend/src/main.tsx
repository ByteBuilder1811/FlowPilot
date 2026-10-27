import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import '@xyflow/react/dist/style.css'
import './index.css'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Workflows from './pages/Workflows'
import Studio from './pages/Studio'
import Agents from './pages/Agents'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import Lab from './pages/Lab'
import Compare from './pages/Compare'
import Evals from './pages/Evals'
import Insights from './pages/Insights'
import Templates from './pages/Templates'
import Knowledge from './pages/Knowledge'
import Integrations from './pages/Integrations'
import SettingsPage from './pages/Settings'
import { Empty } from './components/ui'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="workflows" element={<Workflows />} />
          <Route path="workflows/:id" element={<Studio />} />
          <Route path="agents" element={<Agents />} />
          <Route path="runs" element={<Runs />} />
          <Route path="runs/:id" element={<RunDetail />} />
          <Route path="lab" element={<Lab />} />
          <Route path="compare" element={<Compare />} />
          <Route path="evals" element={<Evals />} />
          <Route path="insights" element={<Insights />} />
          <Route path="templates" element={<Templates />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Empty title="Page not found">That route does not exist.</Empty>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
