import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  children?: ReactNode
  className?: string
}

export function PageHeader({ title, children, className }: PageHeaderProps) {
  return (
    <div className={`sticky top-0 z-10 border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm ${className ?? ''}`}>
      <div className="flex items-center justify-between px-6 py-4">
        <h1 className="text-xl font-semibold text-white tracking-tight">{title}</h1>
        {children && <div className="flex items-center gap-3">{children}</div>}
      </div>
    </div>
  )
}
