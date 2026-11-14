import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
// Phase 6 retired the legacy 8080 API process. The unified Control API is the
// only backend: authenticated trading commands, research/history, performance,
// audit and the compatibility read views all live on this port.
const apiTarget = process.env.QTS_CONTROL_API_TARGET || 'http://localhost:8090';
const proxy = {
    '/v1': {
        target: apiTarget,
        changeOrigin: true,
    },
    '/api': {
        target: apiTarget,
        changeOrigin: true,
    },
    '/health': {
        target: apiTarget,
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
