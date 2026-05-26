/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Semantic tokens — auto-switch via CSS variables in index.css
        bg:           'var(--bg)',
        surface:      'var(--surface)',
        'surface-hi': 'var(--surface-hi)',
        edge:         'var(--edge)',        // border color
        fg:           'var(--fg)',          // primary text
        'fg-2':       'var(--fg-2)',        // secondary text
        'fg-3':       'var(--fg-3)',        // muted/placeholder text
        accent:       'var(--accent)',      // teal
        'accent-hi':  'var(--accent-hi)',   // teal hover
      },
      fontFamily: {
        display: ['Montserrat', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
