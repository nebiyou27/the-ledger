/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ledger: {
          ink: '#0f172a',
          slate: '#1e293b',
          smoke: '#e2e8f0',
          mist: '#f8fafc',
          teal: '#0f766e',
          tealSoft: '#ccfbf1',
          gold: '#f59e0b',
          rose: '#ef4444',
          blue: '#2563eb',
          green: '#16a34a'
        }
      },
      boxShadow: {
        ledger: '0 18px 50px rgba(15, 23, 42, 0.10)'
      }
    }
  },
  plugins: []
}
