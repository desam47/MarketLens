const fs = require('fs');
const path = require('path');
const dotenv = require('dotenv');
const { BundleAnalyzerPlugin } = require('webpack-bundle-analyzer');

// Keep configuration in the repository-root .env. CRA normally reads only
// frontend/.env, while the backend already uses the root file. Parse only the
// frontend-safe variable so provider credentials never enter the frontend
// build process.
const rootEnvPath = path.resolve(__dirname, '..', '.env');
if (fs.existsSync(rootEnvPath) && !process.env.REACT_APP_API_BASE_URL) {
  const rootEnv = dotenv.parse(fs.readFileSync(rootEnvPath));
  if (rootEnv.REACT_APP_API_BASE_URL) {
    process.env.REACT_APP_API_BASE_URL = rootEnv.REACT_APP_API_BASE_URL;
  }
}

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
