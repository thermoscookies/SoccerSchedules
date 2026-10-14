import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return {
      // Render the combined schedule at the root without first fetching the
      // event list in a client page and then performing a second navigation.
      beforeFiles: [
        {
          source: '/',
          destination: '/events/1?events=all',
        },
      ],
      afterFiles: [
        {
          source: '/api/v1/events',
          destination: 'http://backend:8000/api/v1/events/',
        },
        {
          source: '/api/:path*',
          destination: 'http://backend:8000/api/:path*',
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
