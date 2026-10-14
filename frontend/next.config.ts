import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: '/api/v1/events',
        destination: 'http://backend:8000/api/v1/events/',
      },
      {
        source: '/api/:path*',
        destination: 'http://backend:8000/api/:path*',
      },
    ];
  },
};

export default nextConfig;
