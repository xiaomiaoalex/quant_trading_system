/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Surface colors — panel/background density scale
        surface: {
          1: 'var(--surface-1)',
          2: 'var(--surface-2)',
          3: 'var(--surface-3)',
          4: 'var(--surface-4)',
          5: 'var(--surface-5)',
        },
        // Accent colors — border/text neutral scale
        accent: {
          1: 'var(--accent-1)',
          2: 'var(--accent-2)',
          3: 'var(--accent-3)',
          4: 'var(--accent-4)',
          5: 'var(--accent-5)',
        },
        // Status colors — DO NOT MODIFY (locked by contract)
        status: {
          healthy: 'var(--status-healthy)',
          degraded: 'var(--status-degraded)',
          down: 'var(--status-down)',
          stale: 'var(--status-stale)',
          blocked: 'var(--status-blocked)',
        },
        // Severity colors — DO NOT MODIFY (locked by contract)
        severity: {
          low: 'var(--severity-low)',
          medium: 'var(--severity-medium)',
          high: 'var(--severity-high)',
          critical: 'var(--severity-critical)',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [require('@tailwindcss/forms')],
};
