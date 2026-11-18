const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: path.join(__dirname),
  typedRoutes: false,
  async rewrites() {
    const apiServerUrl = (process.env.API_SERVER_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
    return [
      { source: '/health', destination: `${apiServerUrl}/health` },
      { source: '/api/:path*', destination: `${apiServerUrl}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
