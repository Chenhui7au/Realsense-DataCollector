import type { StageState } from '@/api'

const KIB = 1024

/** 184320512 -> "175.8 MB" */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) {
    return '--'
  }
  if (bytes < KIB) {
    return `${bytes} B`
  }
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / KIB
  let unit = 0
  while (value >= KIB && unit < units.length - 1) {
    value /= KIB
    unit += 1
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`
}

/** 12.4 -> "12.4 s" */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) {
    return '--'
  }
  return `${seconds.toFixed(1)} s`
}

/** 83.4 -> "01:23.4", for the live recording timer */
export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds)
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

/** ISO string -> "2026-09-20 14:31:02" in the viewer's local time */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return '--'
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return '--'
  }
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) {
    return '--'
  }
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) {
    return '--'
  }
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) {
    return 'just now'
  }
  if (minutes < 60) {
    return `${minutes} min ago`
  }
  const hours = Math.round(minutes / 60)
  if (hours < 24) {
    return `${hours} h ago`
  }
  return `${Math.round(hours / 24)} d ago`
}

export const STAGE_STATE_LABEL: Record<StageState, string> = {
  idle: 'Not recorded',
  recording: 'Recording',
  saved: 'Saved',
}

export function stageStateLabel(state: StageState): string {
  return STAGE_STATE_LABEL[state]
}

/** "Stage 03" */
export function stageNumber(index: number): string {
  return `Stage ${String(index).padStart(2, '0')}`
}
