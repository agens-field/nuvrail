// Theme bootstrap — applies the saved light/dark theme before first paint to
// avoid a flash of the wrong theme (FOUC).
//
// WHY THIS IS A SEPARATE FILE (not an inline <script>):
//   Served from 'self' as a static asset, this lets the web CSP drop
//   script-src 'unsafe-inline' (see web/nginx.conf and GitHub #63). 'unsafe-inline'
//   would otherwise permit ANY injected inline script to run — and the SPA keeps
//   its bearer token in localStorage, so that is a token-exfiltration vector.
//
// WHY IT IS A BLOCKING <script> IN <head> (no defer/module):
//   It must run and apply the `.dark` class BEFORE the browser paints, exactly
//   like the old inline snippet did. A render-blocking classic script in <head>
//   preserves that timing; defer/module would run too late and flash.
;(function () {
  const saved = localStorage.getItem('nuvrail-theme')
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  if (saved === 'dark' || (!saved && prefersDark)) {
    document.documentElement.classList.add('dark')
  }
})()
