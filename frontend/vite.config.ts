import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  /*
   * The backend is chosen by aliasing rather than by a runtime ternary. A
   * ternary leaves both modules in the import graph, and because the mock seeds
   * state at module scope it survives tree shaking. Aliasing means the mock is
   * never imported at all in a production build.
   */
  const useMock = env.VITE_USE_MOCK === 'true'
  const backend = useMock ? './src/api/mock.ts' : './src/api/real.ts'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
        '@api-backend': fileURLToPath(new URL(backend, import.meta.url)),
      },
    },
    server: {
      // The collector drives this from a second machine on the LAN, so listen on all interfaces.
      host: true,
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      // The FastAPI app serves this directory in production, so keep paths relative to the root.
      assetsDir: 'assets',
    },
    define: {
      __USE_MOCK__: JSON.stringify(useMock),
    },
  }
})
