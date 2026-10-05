<script setup lang="ts">
import StageRail from './StageRail.vue'
import type { RailItem } from './StageRail.vue'
import { isMock } from '@/api'

defineProps<{
  items: RailItem[]
  railLabel?: string
}>()
</script>

<template>
  <header class="hdr">
    <div class="shell hdr__bar">
      <RouterLink to="/" class="hdr__brand" aria-label="Go to the home screen">
        <svg class="hdr__mark" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="1.25" y="4.75" width="21.5" height="14.5" rx="3.25" fill="none"
                stroke="currentColor" stroke-width="1.5" />
          <circle cx="8.4" cy="12" r="3.1" fill="none" stroke="currentColor" stroke-width="1.5" />
          <circle cx="15.6" cy="12" r="3.1" fill="none" stroke="currentColor" stroke-width="1.5" />
          <circle cx="12" cy="12" r="1.05" fill="currentColor" />
        </svg>
        <span class="hdr__word">D435i Capture</span>
      </RouterLink>

      <div class="hdr__meta">
        <span v-if="isMock" class="hdr__badge" title="No camera is attached. Data is simulated.">
          Mock
        </span>
        <slot name="meta" />
      </div>
    </div>

    <div class="shell">
      <StageRail :items="items" :label="railLabel" />
    </div>
  </header>
</template>

<style scoped>
.hdr {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
  padding-bottom: var(--s3);
}

.hdr__bar {
  height: var(--header-h);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s5);
}

.hdr__brand {
  display: inline-flex;
  align-items: center;
  gap: var(--s3);
  text-decoration: none;
  color: var(--ink-900);
}

.hdr__mark {
  width: 22px;
  height: 22px;
  color: var(--accent);
  flex: none;
}

.hdr__word {
  font-family: var(--font-display);
  font-size: 1.3rem;
  letter-spacing: -0.005em;
}

.hdr__meta {
  display: flex;
  align-items: center;
  gap: var(--s4);
  font-size: var(--t-sm);
  color: var(--ink-500);
}

.hdr__badge {
  font-size: var(--t-2xs);
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--warn);
  background: var(--warn-bg);
  border-radius: var(--r-full);
  padding: 3px var(--s3);
}
</style>
