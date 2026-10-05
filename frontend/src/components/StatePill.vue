<script setup lang="ts">
import type { StageState } from '@/api'
import { stageStateLabel } from '@/utils/format'

withDefaults(
  defineProps<{
    state: StageState
    size?: 'sm' | 'md'
    compact?: boolean
  }>(),
  { size: 'md', compact: false },
)
</script>

<template>
  <span class="pill" :class="[`pill--${state}`, `pill--${size}`]">
    <span class="pill__dot" aria-hidden="true" />
    <span v-if="!compact">{{ stageStateLabel(state) }}</span>
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  border: 1px solid transparent;
  border-radius: var(--r-full);
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.pill--sm {
  padding: 2px var(--s2);
  font-size: var(--t-2xs);
}

.pill--md {
  padding: 4px var(--s3);
  font-size: var(--t-sm);
}

.pill__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--r-full);
  background: currentColor;
  flex: none;
}

.pill--idle {
  background: var(--idle-bg);
  color: var(--idle-fg);
}

.pill--recording {
  background: var(--rec-bg);
  border-color: var(--rec-line);
  color: var(--rec-fg);
}

.pill--recording .pill__dot {
  animation: pill-pulse 1.4s var(--ease) infinite;
}

.pill--saved {
  background: var(--saved-bg);
  color: var(--saved-fg);
}

@keyframes pill-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}
</style>
