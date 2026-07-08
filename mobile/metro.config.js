// Monorepo-aware Metro config: mobile/lib/api.ts imports shared code from
// ../../trading_api_kit/ts_client (outside the mobile/ project root). Metro's
// default config only watches/resolves within projectRoot, so both watchFolders
// and nodeModulesPaths need to be extended to the monorepo root.
const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, '..');

const config = getDefaultConfig(projectRoot);

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, 'node_modules'),
  path.resolve(workspaceRoot, 'node_modules'),
];

module.exports = config;
