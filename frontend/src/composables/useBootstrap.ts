import { ref } from 'vue'
import { useApp } from '@/composables/useApp'
import { useGuides } from '@/composables/useGuides'

/*
 * One promise for the whole app. Route guards await this before deciding where
 * to send the browser, so a deep link can render with real data instead of
 * flashing an empty shell.
 */

const pending = ref<Promise<void> | null>(null)

export function useBootstrap() {
  const app = useApp()
  const guides = useGuides()

  function ensure(): Promise<void> {
    if (!pending.value) {
      pending.value = Promise.all([app.load(), guides.load()]).then(() => undefined)
    }
    return pending.value
  }

  /** Forces a reload of everything, used after actions that change readiness. */
  async function reload(): Promise<void> {
    pending.value = null
    await app.load()
    await guides.load()
  }

  return { ensure, reload }
}
