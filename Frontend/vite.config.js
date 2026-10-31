import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
// Set QTS_CONTROL_API_TARGET=http://127.0.0.1:8090 for the unified sandbox runtime.
// The legacy console retains its existing default until explicitly switched.
const controlApiTarget = process.env.QTS_CONTROL_API_TARGET || 'http://localhost:8080';
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    server: {
        port: 5173,
        proxy: {
            '/v1': {
                target: controlApiTarget,
                changeOrigin: true,
            },
            '/api': {
                target: controlApiTarget,
                changeOrigin: true,
            },
            '/health': {
                target: controlApiTarget,
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: true,
    },
});
