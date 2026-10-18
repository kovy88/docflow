import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false, // keeps screenshots and demos clean; errors still show
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
