<script setup lang="ts">
import StageRail from './StageRail.vue'
import type { RailItem } from './StageRail.vue'

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
        <span class="hdr__word">RealSense Data-Collector</span>
      </RouterLink>

      <div class="hdr__meta">
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
  font-size: 1.15rem;
  letter-spacing: -0.005em;
  /*
   * The wordmark is a name, not a sentence. The bar is a fixed height, so the
   * name must stay on one line even at the narrowest widths; below that it
   * ellipsizes rather than pushing the bar wider.
   */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.hdr__meta {
  display: flex;
  align-items: center;
  gap: var(--s4);
  font-size: var(--t-sm);
  color: var(--ink-500);
}
</style>
