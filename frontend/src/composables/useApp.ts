import { computed, ref } from 'vue'
import { api } from '@/api'
import type { AppConfig, Health, ProjectInfo, SessionSummary } from '@/api'
import { describeError } from '@/api/errorText'
import { checkProjectRoot } from '@/utils/paths'
import { useToast } from './useToast'

/*
 * Module level refs give the app a single shared instance without pulling in
 * Pinia. There is only ever one session and one camera, so a store per concern
 * is more ceremony than this needs.
 */

const config = ref<AppConfig | null>(null)
const health = ref<Health | null>(null)
const configError = ref<string | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const savingProject = ref(false)

let inflight: Promise<void> | null = null

async function load(): Promise<void> {
  if (inflight) {
    return inflight
  }
  inflight = (async () => {
    loading.value = true
    configError.value = null
    try {
      const [nextConfig, nextHealth] = await Promise.all([api.config(), api.health()])
      config.value = nextConfig
      health.value = nextHealth
    } catch (error) {
      configError.value = error instanceof Error ? error.message : 'Failed to reach the service.'
    } finally {
      loading.value = false
      inflight = null
    }
  })()
  return inflight
}

async function refreshHealth(): Promise<void> {
  refreshing.value = true
  try {
    health.value = await api.health()
  } catch {
    health.value = null
  } finally {
    refreshing.value = false
  }
}

export function useApp() {
  const toast = useToast()

  const totalStages = computed(() => config.value?.total_stages ?? health.value?.guides.total ?? 8)

  const deviceReady = computed(() => health.value?.device.connected === true)
  const guidesReady = computed(() => health.value?.guides.ready === true)

  /**
   * Audio is the only optional input. It is a problem worth showing but not one
   * that blocks a round unless `audio.required` is on.
   */
  const audio = computed(() => health.value?.audio ?? null)
  const audioEnabled = computed(() => audio.value?.enabled === true)
  const audioAvailable = computed(() => audio.value?.available === true)
  const audioBlocking = computed(
    () => audioEnabled.value && audio.value?.required === true && !audioAvailable.value,
  )

  const project = computed<ProjectInfo | null>(() => health.value?.project ?? null)
  const projectReady = computed(() => {
    const p = project.value
    return p !== null && p.configured && p.writable && p.enough
  })

  const activeSession = computed<SessionSummary | null>(
    () => health.value?.active_session ?? null,
  )

  /** Everything that has to be true before a session may be started. */
  const canStartSession = computed(
    () => deviceReady.value && guidesReady.value && projectReady.value && !audioBlocking.value,
  )

  const blockers = computed(() => {
    const list: { key: string; label: string; detail: string; met: boolean }[] = []
    const h = health.value

    list.push({
      key: 'camera',
      label: 'Camera connected',
      detail: h?.device.connected
        ? `${h.device.name ?? 'RealSense'} on USB ${h.device.usb_type ?? '?'}`
        : (h?.device.reason ?? 'No RealSense device detected'),
      met: h?.device.connected === true,
    })

    list.push({
      key: 'project',
      label: 'Project folder set',
      detail: project.value?.configured
        ? (project.value.error ?? `${project.value.root}`)
        : 'No project folder has been chosen yet',
      met: projectReady.value,
    })

    list.push({
      key: 'guides',
      label: 'Stage diagrams configured',
      detail: h?.guides.ready
        ? `All ${h.guides.total} diagrams ready`
        : `${h?.guides.uploaded ?? 0} of ${h?.guides.total ?? 8} uploaded`,
      met: h?.guides.ready === true,
    })

    // Only listed when it can actually block. Audio that is missing but optional
    // is reported on its own card instead, because a blocked round would be a
    // disproportionate response to a sidecar the dataset can live without.
    if (audioBlocking.value) {
      list.push({
        key: 'audio',
        label: 'Microphone available',
        detail: audio.value?.reason ?? 'The microphone could not be opened',
        met: false,
      })
    }

    return list
  })

  const stageName = (index: number): string =>
    config.value?.stages.find((s) => s.index === index)?.name ?? `Stage ${index}`

  /**
   * Saves a new project folder. The host validates writability and free space
   * and rejects the change if either fails, so this only reports the outcome.
   */
  async function saveProjectRoot(raw: string): Promise<boolean> {
    const check = checkProjectRoot(raw)
    if (!check.ok) {
      toast.danger('Project folder not saved', check.error ?? 'Invalid path.')
      return false
    }

    savingProject.value = true
    try {
      const updated = await api.updateProject(check.value)
      health.value = await api.health()
      toast.success('Project folder set', updated.root ?? undefined)
      return true
    } catch (error) {
      const { message } = describeError(error)
      toast.danger('Project folder not saved', message)
      return false
    } finally {
      savingProject.value = false
    }
  }

  return {
    config,
    health,
    configError,
    loading,
    refreshing,
    savingProject,
    totalStages,
    deviceReady,
    guidesReady,
    audio,
    audioEnabled,
    audioAvailable,
    audioBlocking,
    project,
    projectReady,
    activeSession,
    canStartSession,
    blockers,
    stageName,
    load,
    refreshHealth,
    saveProjectRoot,
  }
}
