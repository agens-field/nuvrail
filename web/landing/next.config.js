/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export capable — uncomment to enable full static export (disables API routes)
  // output: 'export',
  turbopack: {
    root: __dirname,
  },
};

module.exports = nextConfig;
