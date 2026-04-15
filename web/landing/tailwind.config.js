/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Matches the Nuvrail approval app (slate + indigo palette)
        background: '#0f172a',   // slate-900
        surface: '#1e293b',      // slate-800
        border: '#334155',       // slate-700
        accent: '#818cf8',       // indigo-400 — matches app wordmark + active nav
        'accent-dark': '#4f46e5', // indigo-600 — matches app CTA buttons
        muted: '#94a3b8',        // slate-400
        subtle: '#475569',       // slate-600
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
