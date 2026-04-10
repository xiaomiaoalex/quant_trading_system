import { Routes, Route, Navigate } from 'react-router-dom'
import { Monitor } from './pages/Monitor'

function App() {
  return (
    <div className="min-h-screen bg-gray-900">
      <Routes>
        <Route path="/" element={<Navigate to="/monitor" replace />} />
        <Route path="/monitor" element={<Monitor />} />
        {/* Future routes:
        <Route path="/strategies" element={<Strategies />} />
        <Route path="/reconcile" element={<Reconcile />} />
        <Route path="/backtests" element={<Backtests />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/replay" element={<Replay />} />
        */}
      </Routes>
    </div>
  )
}

export default App
