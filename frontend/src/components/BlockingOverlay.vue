<script setup lang="ts">
withDefaults(
  defineProps<{
    visible: boolean
    label: string
    detail?: string
  }>(),
  { detail: undefined },
)
</script>

<template>
  <div v-if="visible" class="veil fade-in">
    <div class="veil__inner">
      <span class="veil__spinner" aria-hidden="true" />
      <div>
        <p class="veil__label">{{ label }}</p>
        <p v-if="detail" class="veil__detail">{{ detail }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
 * Covers the preview while the backend restarts the pipeline. The blackout is
 * expected and brief, this only makes sure the collector is not looking at an
 * unexplained blank frame.
 */
.veil {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: grid;
  place-items: center;
  background: rgba(12, 16, 18, 0.76);
  backdrop-filter: blur(3px);
  border-radius: inherit;
}

.veil__inner {
  display: flex;
  align-items: center;
  gap: var(--s4);
  color: #eef2f3;
  padding: var(--s4) var(--s5);
}

.veil__spinner {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(238, 242, 243, 0.28);
  border-top-color: #eef2f3;
  border-radius: var(--r-full);
  animation: veil-spin 700ms linear infinite;
  flex: none;
}

.veil__label {
  font-size: var(--t-base);
  font-weight: 500;
}

.veil__detail {
  font-size: var(--t-sm);
  color: rgba(238, 242, 243, 0.66);
  margin-top: 2px;
}

@keyframes veil-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
