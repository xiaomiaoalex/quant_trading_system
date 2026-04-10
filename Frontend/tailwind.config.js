/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Status colors
        status: {
          healthy: '#22c55e',
          degraded: '#f59e0b',
          down: '#ef4444',
          stale: '#6b7280',
          blocked: '#dc2626',
        },
        // Severity colors
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
