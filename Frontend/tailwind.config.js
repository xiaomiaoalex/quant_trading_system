/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Surface colors — panel/background density scale
        surface: {
          1: '#0b0f19',
          2: '#111827',
          3: '#1a2234',
          4: '#1e293b',
          5: '#243447',
        },
        // Accent colors — border/text neutral scale
        accent: {
          1: '#334155',
          2: '#475569',
          3: '#64748b',
          4: '#94a3b8',
          5: '#cbd5e1',
        },
        // Status colors — DO NOT MODIFY (locked by contract)
        status: {
          healthy: '#22c55e',
          degraded: '#f59e0b',
          down: '#ef4444',
          stale: '#6b7280',
          blocked: '#dc2626',
        },
        // Severity colors — DO NOT MODIFY (locked by contract)
        severity: {
          low: '#3b82f6',
          medium: '#f59e0b',
          high: '#f97316',
          critical: '#ef4444',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [require('@tailwindcss/forms')],
};
