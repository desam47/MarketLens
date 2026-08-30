const { BundleAnalyzerPlugin } = require('webpack-bundle-analyzer');

module.exports = {
  // Override webpack config injected by react-scripts
  webpack: {
    plugins: [
      // Only activate the bundle analyzer when ANALYZE=true is passed.
      // Run: ANALYZE=true npm run build
      new BundleAnalyzerPlugin({
        analyzerMode: process.env.ANALYZE ? 'static' : 'disabled',
        reportFilename: 'stats.html',
        openAnalyzer: !!process.env.ANALYZE,
        generateStatsFile: !!process.env.ANALYZE,
        statsFilename: 'stats.json',
      }),
    ],
  },
};
