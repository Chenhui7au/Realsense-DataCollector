<script setup lang="ts">
import { ref, watch } from 'vue'
import BlockingOverlay from './BlockingOverlay.vue'

const props = withDefaults(
  defineProps<{
    status: 'off' | 'starting' | 'live'
    streamUrl: string
    overlayVisible?: boolean
    overlayLabel?: string
    overlayDetail?: string
  }>(),
  {
    overlayVisible: false,
    overlayLabel: '',
    overlayDetail: undefined,
  },
)

const streamFailed = ref(false)

/*
 * A fresh stream clears the previous failure. Without this the fallback would
 * stick after the camera recovered, because the element is unmounted on error and
 * the browser never gets a second chance at the same src.
 */
watch(
  () => props.status,
  (status) => {
    if (status === 'live') {
      streamFailed.value = false
    }
  },
)
</script>

<template>
  <div class="pane">
    <!-- Live feed -->
    <template v-if="status === 'live'">
      <img
        v-if="!streamFailed"
        class="pane__layer"
        :src="streamUrl"
        alt="Live camera preview"
        @error="streamFailed = true"
      />
      <div v-else class="pane__fallback">
        <p class="pane__fallback-title">Preview stream interrupted</p>
        <p class="pane__fallback-body">The camera may be reconfiguring. This usually resolves itself.</p>
      </div>

      <slot name="hud" />
    </template>

    <!-- Idle and starting states -->
    <div v-else class="pane__idle">
      <span v-if="status === 'starting'" class="pane__spin" aria-hidden="true" />
      <svg v-else class="pane__icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="1.25" y="4.75" width="21.5" height="14.5" rx="3.25" fill="none"
              stroke="currentColor" stroke-width="1.4" />
        <circle cx="8.4" cy="12" r="3.1" fill="none" stroke="currentColor" stroke-width="1.4" />
        <circle cx="15.6" cy="12" r="3.1" fill="none" stroke="currentColor" stroke-width="1.4" />
      </svg>
      <p class="pane__idle-title">
        {{ status === 'starting' ? 'Starting preview' : 'Preview is off' }}
      </p>
      <p class="pane__idle-body">
        {{
          status === 'starting'
            ? 'Opening the camera stream.'
            : 'The live feed starts when you open a capture stage.'
        }}
      </p>
    </div>

    <BlockingOverlay
      :visible="overlayVisible"
      :label="overlayLabel"
      :detail="overlayDetail"
    />
  </div>
</template>

<style scoped>
/*
 * The feed owns this box. Fixed 4:3 matches the sensor output so nothing is
 * letterboxed, and the aspect is locked before the stream arrives so the page
 * does not jump when it connects.
 */
.pane {
  position: relative;
  aspect-ratio: 4 / 3;
  width: 100%;
  overflow: hidden;
  border-radius: var(--r-md);
  background: #0c1012;
  border: 1px solid var(--line);
}

.pane__layer {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.pane__idle {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s3);
  text-align: center;
  padding: var(--s5);
  color: rgba(226, 234, 236, 0.5);
}

.pane__icon {
  width: 42px;
  height: 42px;
  color: rgba(226, 234, 236, 0.24);
}

.pane__idle-title {
  font-size: var(--t-base);
  color: rgba(226, 234, 236, 0.78);
}

.pane__idle-body {
  font-size: var(--t-sm);
  max-width: 30ch;
  color: rgba(226, 234, 236, 0.42);
}

.pane__spin {
  width: 22px;
  height: 22px;
  border: 2px solid rgba(226, 234, 236, 0.2);
  border-top-color: rgba(226, 234, 236, 0.8);
  border-radius: var(--r-full);
  animation: pane-spin 700ms linear infinite;
}

@keyframes pane-spin {
  to {
    transform: rotate(360deg);
  }
}

.pane__fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s2);
  padding: var(--s5);
  text-align: center;
}

.pane__fallback-title {
  color: rgba(226, 234, 236, 0.85);
}

.pane__fallback-body {
  font-size: var(--t-sm);
  color: rgba(226, 234, 236, 0.45);
  max-width: 34ch;
}
</style>
