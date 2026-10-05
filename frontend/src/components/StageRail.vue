<script setup lang="ts">
export type RailTone = 'pending' | 'current' | 'done' | 'recording'

export interface RailItem {
  index: number
  name: string
  tone: RailTone
}

defineProps<{
  items: RailItem[]
  label?: string
}>()
</script>

<template>
  <div class="rail" role="img" :aria-label="label ?? 'Stage progress'">
    <span
      v-for="item in items"
      :key="item.index"
      class="rail__seg"
      :class="`rail__seg--${item.tone}`"
      :title="`Stage ${String(item.index).padStart(2, '0')} \u00b7 ${item.name}`"
    />
  </div>
</template>

<style scoped>
/*
 * Eight segments, one per stage. Carries the whole progress story for the app,
 * so it appears on every screen and never moves.
 */
.rail {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 4px;
  height: 5px;
}

.rail__seg {
  border-radius: var(--r-full);
  background: var(--line-strong);
  transition: background-color var(--dur-slow) var(--ease);
}

.rail__seg--pending {
  background: var(--line-strong);
}

.rail__seg--current {
  background: var(--ink-300);
}

.rail__seg--done {
  background: var(--saved-fg);
}

.rail__seg--recording {
  background: var(--rec-fg);
  animation: rail-pulse 1.6s var(--ease) infinite;
}

@keyframes rail-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}
</style>
