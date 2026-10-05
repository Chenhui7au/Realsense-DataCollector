<script setup lang="ts">
import { useToast } from '@/composables/useToast'

const { toasts, dismiss } = useToast()
</script>

<template>
  <div class="stack" aria-live="polite">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="toast rise-in"
      :class="`toast--${toast.tone}`"
    >
      <div class="toast__body">
        <p class="toast__title">{{ toast.title }}</p>
        <p v-if="toast.detail" class="toast__detail">{{ toast.detail }}</p>
      </div>
      <button class="toast__close" aria-label="Dismiss" @click="dismiss(toast.id)">
        <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
          <path d="M2 2 L10 10 M10 2 L2 10" stroke="currentColor" stroke-width="1.6"
                stroke-linecap="round" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.stack {
  position: fixed;
  right: var(--s5);
  bottom: var(--s5);
  z-index: 80;
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  width: min(400px, calc(100vw - var(--s8)));
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  display: flex;
  gap: var(--s3);
  align-items: flex-start;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink-400);
  border-radius: var(--r-sm);
  box-shadow: var(--shadow-2);
  padding: var(--s3) var(--s3) var(--s3) var(--s4);
}

.toast--success {
  border-left-color: var(--saved-fg);
}

.toast--danger {
  border-left-color: var(--danger);
}

.toast__body {
  flex: 1;
  min-width: 0;
}

.toast__title {
  font-weight: 600;
  font-size: var(--t-sm);
}

.toast__detail {
  margin-top: 2px;
  font-size: var(--t-sm);
  color: var(--ink-500);
}

.toast--danger .toast__detail {
  color: var(--danger);
}

.toast__close {
  flex: none;
  border: 0;
  background: transparent;
  color: var(--ink-400);
  padding: 2px;
  border-radius: var(--r-xs);
}

.toast__close:hover {
  color: var(--ink-900);
  background: var(--surface-hover);
}
</style>
