import { computed, onBeforeUnmount, ref } from 'vue'

/**
 * Local elapsed-time counter for the recording status bar.
 *
 * Purely presentational. It never drives button availability, so it can never
 * disagree with the backend about stage state.
 */
export function useCountdown() {
  const elapsed = ref(0)
  const limit = ref<number | null>(null)
  let timer: number | null = null
  let startedAt = 0

  const remaining = computed(() =>
    limit.value === null ? null : Math.max(0, limit.value - elapsed.value),
  )

  const expired = computed(() => remaining.value !== null && remaining.value <= 0)

  function tick(): void {
    elapsed.value = Math.max(0, (Date.now() - startedAt) / 1000)
  }

  function stop(): void {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  /** Starts from zero, for a take this browser just kicked off. */
  function start(limitSeconds: number | null): void {
    stop()
    startedAt = Date.now()
    elapsed.value = 0
    limit.value = limitSeconds
    timer = window.setInterval(tick, 100)
  }

  /**
   * Rebuilds the counter from the server timestamp. Needed after a page
   * refresh, where the take is already partway through.
   */
  function resumeFrom(startIso: string, limitSeconds: number | null): void {
    stop()
    const parsed = new Date(startIso).getTime()
    startedAt = Number.isNaN(parsed) ? Date.now() : parsed
    limit.value = limitSeconds
    tick()
    timer = window.setInterval(tick, 100)
  }

  function reset(): void {
    stop()
    elapsed.value = 0
    limit.value = null
  }

  onBeforeUnmount(stop)

  return { elapsed, remaining, limit, expired, start, resumeFrom, reset, stop }
}
