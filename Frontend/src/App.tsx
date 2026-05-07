import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Suspense } from 'react'
import { AppShell } from '@/components/layout'
import { LoadingSpinner } from '@/components/ui'
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

function PageFallback() {
  return (
    <div className="flex h-full items-center justify-center p-12">
      <LoadingSpinner size="md" />
    </div>
  )
}

function App() {
  const location = useLocation()
  return (
    <AppShell>
      <div key={location.pathname} className="animate-page-enter">
        <Suspense fallback={<PageFallback />}>
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
        </Suspense>
      </div>
    </AppShell>
  )
}

export default App