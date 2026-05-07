import { useState, useCallback, useEffect, type ReactNode } from 'react'
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

function useMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth < 768 : false
  )

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  return isMobile
}

export function AppShell({ children }: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getInitialCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)
  const isMobile = useMobile()

  const toggleSidebar = useCallback(() => {
    if (isMobile) {
      setMobileOpen(prev => !prev)
      return
    }
    setSidebarCollapsed(prev => {
      const next = !prev
      try {
        window.localStorage.setItem('sidebar_collapsed', String(next))
      } catch {
        // ignore storage errors
      }
      return next
    })
  }, [isMobile])

  const closeMobile = useCallback(() => setMobileOpen(false), [])

  return (
    <div className="min-h-screen bg-gray-900">
      <Topbar sidebarCollapsed={isMobile ? true : sidebarCollapsed} onToggleSidebar={toggleSidebar} />
      <GlobalStatusRibbon />
      <Sidebar
        collapsed={isMobile ? false : sidebarCollapsed}
        mobileOpen={isMobile ? mobileOpen : undefined}
        onCloseMobile={isMobile ? closeMobile : undefined}
      />
      <main
        className={`pt-16 transition-[margin] duration-200 ${
          isMobile ? 'ml-0' : sidebarCollapsed ? 'ml-16' : 'ml-64'
        }`}
      >
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
      </main>
    </div>
  )
}
