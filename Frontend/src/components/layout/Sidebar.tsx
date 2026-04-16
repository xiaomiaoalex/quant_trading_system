import { NavLink, useLocation } from 'react-router-dom'
import { clsx } from 'clsx'

interface NavItem {
  path: string
  label: string
  phase: 'P0' | 'P1' | 'P2'
}

const navItems: NavItem[] = [
  { path: '/monitor', label: 'Monitor', phase: 'P0' },
  { path: '/strategies', label: 'Strategies', phase: 'P0' },
  { path: '/reconcile', label: 'Reconcile', phase: 'P0' },
  { path: '/chat', label: 'AI Chat', phase: 'P0' },
  { path: '/backtests', label: 'Backtests', phase: 'P1' },
  { path: '/reports', label: 'Reports', phase: 'P1' },
  { path: '/audit', label: 'Audit', phase: 'P2' },
  { path: '/replay', label: 'Replay', phase: 'P2' },
]

interface SidebarProps {
  collapsed?: boolean
}

export function Sidebar({ collapsed = false }: SidebarProps) {
  const location = useLocation()

  return (
    <aside
      className={clsx(
        'fixed left-0 top-16 h-[calc(100vh-4rem)] bg-gray-800 border-r border-gray-700 transition-all duration-200',
        collapsed ? 'w-16' : 'w-64'
      )}
    >
      <nav className="p-3 space-y-1">
        {navItems.map(item => {
          const isActive = location.pathname === item.path
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={clsx(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors',
                isActive
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:bg-gray-700/50 hover:text-gray-200'
              )}
              title={item.label}
            >
              <span
                className={clsx(
                  'w-2 h-2 rounded-full',
                  item.phase === 'P0' && 'bg-blue-500',
                  item.phase === 'P1' && 'bg-yellow-500',
                  item.phase === 'P2' && 'bg-purple-500'
                )}
              />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          )
        })}
      </nav>
    </aside>
  )
}