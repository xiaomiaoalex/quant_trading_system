import { Component, type ReactNode, type ErrorInfo } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log to console for debugging; do NOT swallow the error
    console.error('ErrorBoundary caught render error:', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      const errorMessage = this.state.error?.message ?? 'Unknown render error'

      return (
        <div className="flex h-screen w-screen items-center justify-center bg-gray-900 p-6">
          <div className="w-full max-w-lg rounded-lg border border-red-900/40 bg-surface-3/60 p-6">
            <div className="flex items-center gap-3">
              <div className="rounded-full bg-red-900/20 p-1.5">
                <svg className="h-5 w-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-red-300">Render Error</h2>
            </div>
            <p className="mt-3 text-sm text-accent-3 font-mono leading-relaxed break-all">
              {errorMessage}
            </p>
            <p className="mt-2 text-xs text-accent-2">
              This is a rendering layer failure. API errors, Zod validation failures,
              and request_id details are handled separately and are not affected by this boundary.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-4 rounded-md bg-red-900/20 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-900/40 focus-visible:ring-2 focus-visible:ring-red-500"
            >
              Reload Page
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
