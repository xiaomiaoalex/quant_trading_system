import { useState, useCallback, type ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { GlobalStatusRibbon } from './GlobalStatusRibbon'
import { ErrorBoundary } from './ErrorBoundary'

interface AppShellProps {
  children: ReactNode
}

function getInitialCollapsed(): boolean {
  try {
    return window.localStorage.getItem('sidebar_collapsed') === 'true'
  } catch {
    return false
  }
}

export function AppShell({ children }: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialCollapsed)

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed(prev => {
      const next = !prev
      try {
        window.localStorage.setItem('sidebar_collapsed', String(next))
      } catch {
        // ignore storage errors
      }
      return next
    })
  }, [])

  return (
    <div className="min-h-screen bg-gray-900">
      <Topbar sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
      <GlobalStatusRibbon />
      <Sidebar collapsed={sidebarCollapsed} />
      <main className={`pt-16 transition-all duration-200 ${sidebarCollapsed ? 'ml-16' : 'ml-64'}`}>
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
      </main>
    </div>
  )
}
