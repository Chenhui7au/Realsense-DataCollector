import { readonly, ref } from 'vue'

export interface Toast {
  id: number
  tone: 'info' | 'success' | 'danger'
  title: string
  detail?: string
}

const toasts = ref<Toast[]>([])
let nextId = 1

function push(tone: Toast['tone'], title: string, detail?: string, ttl = 0): number {
  const id = nextId++
  toasts.value.push({ id, tone, title, detail })
  if (ttl > 0) {
    window.setTimeout(() => dismiss(id), ttl)
  }
  return id
}

function dismiss(id: number): void {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

/**
 * Transient messages. Errors stay until dismissed because a collector may be
 * away from the screen when one fires.
 */
export function useToast() {
  return {
    toasts: readonly(toasts),
    info: (title: string, detail?: string) => push('info', title, detail, 6000),
    success: (title: string, detail?: string) => push('success', title, detail, 4000),
    danger: (title: string, detail?: string) => push('danger', title, detail),
    dismiss,
    clear: () => {
      toasts.value = []
    },
  }
}
