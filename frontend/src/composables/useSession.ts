import { computed, ref, watch } from 'vue'
import { api } from '@/api'
import type { Session, SessionCreateBody, Stage, StageAction } from '@/api'
import { describeError } from '@/api/errorText'
import { useToast } from './useToast'

/*
 * The session is the single source of truth for stage state. Nothing here
 * updates optimistically. Every action replaces the local session with whatever
 * the backend returned, so a refresh or a second tab can never diverge.
 */

const session = ref<Session | null>(null)
const loading = ref(false)
const busyAction = ref<StageAction | null>(null)
const lastError = ref<{ code: string; message: string } | null>(null)

let pollTimer: number | null = null

/*
 * Remembers which session this browser was working on, so a refresh does not
 * lose the end-of-session summary. This is not used to decide readiness: the
 * backend always has the final say on that.
 */
const LAST_SID_KEY = 'd435i.lastSessionId'

function rememberSession(sid: string | null): void {
  try {
    if (sid) {
      window.sessionStorage.setItem(LAST_SID_KEY, sid)
    } else {
      window.sessionStorage.removeItem(LAST_SID_KEY)
    }
  } catch {
    // Storage can be unavailable in private mode. Losing this is harmless.
  }
}

export function rememberedSessionId(): string | null {
  try {
    return window.sessionStorage.getItem(LAST_SID_KEY)
  } catch {
    return null
  }
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

export function useSession() {
  const toast = useToast()

  const stages = computed<Stage[]>(() => session.value?.stages ?? [])
  const currentIndex = computed(() => session.value?.current_stage ?? 1)
  const isFinished = computed(() => session.value?.status === 'finished')

  const currentStage = computed<Stage | null>(
    () => stages.value.find((s) => s.index === currentIndex.value) ?? null,
  )

  const savedCount = computed(() => stages.value.filter((s) => s.state === 'saved').length)

  const recordingStage = computed<Stage | null>(
    () => stages.value.find((s) => s.state === 'recording') ?? null,
  )

  const stageAt = (index: number): Stage | null =>
    stages.value.find((s) => s.index === index) ?? null

  const can = (stage: Stage | null, action: StageAction): boolean =>
    stage?.allowed_actions.includes(action) === true

  function setSession(next: Session): void {
    session.value = next
    rememberSession(next.session_id)
  }

  async function create(body: SessionCreateBody): Promise<Session | null> {
    loading.value = true
    lastError.value = null
    try {
      const created = await api.createSession(body)
      setSession(created)
      return created
    } catch (error) {
      lastError.value = describeError(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function fetch(sid: string): Promise<Session | null> {
    loading.value = true
    try {
      const found = await api.getSession(sid)
      setSession(found)
      return found
    } catch (error) {
      lastError.value = describeError(error)
      session.value = null
      rememberSession(null)
      return null
    } finally {
      loading.value = false
    }
  }

  async function discard(sid: string): Promise<boolean> {
    loading.value = true
    try {
      await api.discardSession(sid)
      stopPolling()
      session.value = null
      rememberSession(null)
      return true
    } catch (error) {
      const described = describeError(error)
      lastError.value = described
      toast.danger('Could not discard the session', described.message)
      return false
    } finally {
      loading.value = false
    }
  }

  /** Wraps a stage action so busy state and error reporting are consistent. */
  async function run<T>(action: StageAction, task: () => Promise<T>): Promise<T | null> {
    busyAction.value = action
    lastError.value = null
    try {
      return await task()
    } catch (error) {
      lastError.value = describeError(error)
      throw error
    } finally {
      busyAction.value = null
    }
  }

  /**
   * Polling exists for one reason. The backend can change stage state on its
   * own when a take hits the maximum duration, and the browser has no other way
   * to learn about that.
   */
  function startPolling(sid: string, intervalMs = 2000): void {
    stopPolling()
    pollTimer = window.setInterval(() => {
      void api
        .getSession(sid)
        .then((next) => setSession(next))
        .catch(() => stopPolling())
    }, intervalMs)
  }

  // Never leave a timer running behind the session it belongs to.
  watch(session, (next) => {
    if (!next || next.status === 'finished') {
      stopPolling()
    }
  })

  return {
    session,
    stages,
    currentIndex,
    currentStage,
    currentStageName: computed(() => currentStage.value?.name ?? ''),
    savedCount,
    recordingStage,
    isFinished,
    loading,
    busyAction,
    lastError,
    stageAt,
    can,
    setSession,
    create,
    fetch,
    discard,
    run,
    startPolling,
    stopPolling,
  }
}
