/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output bundles only what's needed to run — ideal for Docker.
  // Keeps LOOPS_API_KEY server-side (never exposed to browser).
  output: 'standalone',
  turbopack: {
    root: __dirname,
  },
};

module.exports = nextConfig;
