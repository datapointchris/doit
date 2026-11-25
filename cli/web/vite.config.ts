import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// The build lands where go:embed reads it, over the committed placeholder.
const embedded = fileURLToPath(new URL('../internal/panelpage/dist', import.meta.url))

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: embedded,
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.spec.ts'],
  },
})
