/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

/**
 * True when the build uses the in-browser mock backend. Substituted at build
 * time by vite.config.ts, so the mock branch disappears entirely in production.
 */
declare const __USE_MOCK__: boolean

interface ImportMetaEnv {
  readonly VITE_USE_MOCK: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
