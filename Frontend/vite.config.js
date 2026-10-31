import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
// The new Control API (runtime/control_main.py) serves /v1/research/* and
// /v1/data/history/*; the legacy API keeps every other /v1 route. Routing the
// two path families separately avoids switching the whole console to a port
// that does not implement the legacy endpoints.
//
// - Nothing set: both families go to the legacy default (current behaviour).
// - QTS_CONTROL_API_TARGET=http://127.0.0.1:8090: research + history go to the
//   Control API, everything else keeps the legacy target.
const legacyApiTarget = process.env.QTS_LEGACY_API_TARGET ||
    process.env.QTS_CONTROL_API_TARGET ||
    'http://localhost:8080';
const controlApiTarget = process.env.QTS_CONTROL_API_TARGET || legacyApiTarget;
const proxy = {
    '/v1/research': {
        target: controlApiTarget,
        changeOrigin: true,
    },
    '/v1/data/history': {
        target: controlApiTarget,
        changeOrigin: true,
    },
    '/v1': {
        target: legacyApiTarget,
        changeOrigin: true,
    },
    '/api': {
        target: legacyApiTarget,
        changeOrigin: true,
    },
    '/health': {
        target: legacyApiTarget,
        changeOrigin: true,
    },
};
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    server: {
        port: 5173,
        proxy,
    },
    build: {
        outDir: 'dist',
        sourcemap: true,
    },
});
