import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tailwind v4 is wired via PostCSS (postcss.config.js), not @tailwindcss/vite:
// the Vite plugin no-opped on this Vite 5.4 setup (empty utilities layer), so
// the new utility-based UI rendered unstyled. PostCSS runs inside Vite's CSS
// pipeline reliably.
export default defineConfig({
  plugins: [react()],
  build: {
    target: 'esnext',
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
        }
      }
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5173
  }
})
