import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

interface AppShellProps {
  children: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="min-h-screen bg-gray-900">
      <Topbar sidebarCollapsed={false} />
      <Sidebar collapsed={false} />
      <main className="ml-64 pt-16">{children}</main>
    </div>
  )
}