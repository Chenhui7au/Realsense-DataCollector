import { computed, ref } from 'vue'
import { api } from '@/api'
import type { GuideEntry, GuidesResponse } from '@/api'
import { describeError } from '@/api/errorText'
import { useToast } from './useToast'

const guides = ref<GuidesResponse | null>(null)
const loaded = ref(false)
const busyIndex = ref<number | null>(null)
const batchBusy = ref(false)

const byIndex = computed(() => {
  const map = new Map<number, GuideEntry>()
  for (const entry of guides.value?.guides ?? []) {
    map.set(entry.index, entry)
  }
  return map
})

const ready = computed(() => guides.value?.ready === true)
const uploaded = computed(() => guides.value?.uploaded ?? 0)
const total = computed(() => guides.value?.total ?? 8)
const progress = computed(() => (total.value ? uploaded.value / total.value : 0))

async function load(): Promise<void> {
  try {
    guides.value = await api.guides()
    loaded.value = true
  } catch (error) {
    const { message } = describeError(error)
    useToast().danger('Could not load stage diagrams', message)
  }
}

export function guideImageUrl(entry: GuideEntry | null | undefined): string {
  if (!entry || !entry.configured) {
    return ''
  }
  return api.guideImageUrl(entry.index, entry.sha256)
}

export function useGuides() {
  const toast = useToast()

  async function upload(index: number, file: File): Promise<{ ok: boolean; message?: string }> {
    busyIndex.value = index
    try {
      const result = await api.uploadGuide(index, file)
      await load()
      toast.success(`${result.name} updated`, result.original_filename ?? undefined)
      return { ok: true }
    } catch (error) {
      const { message } = describeError(error)
      // Also returned to the caller so the failure can be pinned on the card
      // that caused it, alongside the global notice.
      toast.danger('Upload rejected', message)
      return { ok: false, message }
    } finally {
      busyIndex.value = null
    }
  }

  /**
   * Batch drop. Returns which stages failed so the grid can pin the error on
   * the right cards instead of only reporting a count.
   */
  async function uploadBatch(
    files: Map<number, File>,
  ): Promise<{ applied: number[]; failed: Map<number, string> }> {
    batchBusy.value = true
    const failed = new Map<number, string>()
    let applied: number[] = []
    try {
      const result = await api.uploadGuidesBatch(files)
      applied = result.applied
      for (const item of result.failed) {
        failed.set(item.index, describeError({ code: item.code, message: item.message }).message)
      }
      guides.value = result.guides
      if (result.failed.length === 0) {
        toast.success(`${result.applied.length} diagrams updated`)
      } else {
        toast.info(
          `${result.applied.length} of ${files.size} diagrams updated`,
          'The rejected files are listed under their stage cards.',
        )
      }
    } catch (error) {
      const { message } = describeError(error)
      toast.danger('Batch upload failed', message)
    } finally {
      batchBusy.value = false
    }
    return { applied, failed }
  }

  async function remove(index: number): Promise<void> {
    busyIndex.value = index
    try {
      await api.deleteGuide(index)
      await load()
      toast.info(`Stage ${String(index).padStart(2, '0')} diagram removed`)
    } catch (error) {
      const { message } = describeError(error)
      toast.danger('Could not remove the diagram', message)
    } finally {
      busyIndex.value = null
    }
  }

  async function saveInstructions(index: number, text: string): Promise<{ ok: boolean; message?: string }> {
    busyIndex.value = index
    try {
      const result = await api.saveGuideInstructions(index, text)
      // The server reports the text it actually stored, which is the trimmed
      // value, so reload rather than trusting the local draft.
      await load()
      toast.success(
        result.instructions_custom ? `${result.name} description saved` : `${result.name} description reset`,
      )
      return { ok: true }
    } catch (error) {
      const { message } = describeError(error)
      toast.danger('Description not saved', message)
      return { ok: false, message }
    } finally {
      busyIndex.value = null
    }
  }

  /** Splits a drop of several files across stages by the number in the name. */
  function inferStageFromFilename(file: File): number | null {
    const match = file.name.match(/(\d{1,2})/)
    if (!match) {
      return null
    }
    const index = Number(match[1])
    return index >= 1 && index <= total.value ? index : null
  }

  return {
    guides,
    loaded,
    byIndex,
    ready,
    uploaded,
    total,
    progress,
    busyIndex,
    batchBusy,
    load,
    upload,
    uploadBatch,
    saveInstructions,
    remove,
    inferStageFromFilename,
  }
}
