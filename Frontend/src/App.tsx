import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/layout'
import {
  Monitor,
  Strategies,
  Reconcile,
  Backtests,
  Reports,
  Chat,
  Audit,
  Replay,
  Data,
  Research,
  PortfolioAllocation,
  PortfolioAutopilot,
  CryptoRiskOps,
} from './pages'

function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/monitor" replace />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="/data" element={<Data />} />
        <Route path="/research" element={<Research />} />
        <Route path="/strategies" element={<Strategies />} />
        <Route path="/reconcile" element={<Reconcile />} />
        <Route path="/backtests" element={<Backtests />} />
        <Route path="/portfolio-allocation" element={<PortfolioAllocation />} />
        <Route path="/portfolio-autopilot" element={<PortfolioAutopilot />} />
        <Route path="/crypto-risk" element={<CryptoRiskOps />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/replay" element={<Replay />} />
      </Routes>
    </AppShell>
  )
}

export default App
