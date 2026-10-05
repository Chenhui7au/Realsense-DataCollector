<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import AppButton from './AppButton.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    message?: string
    confirmLabel?: string
    cancelLabel?: string
    tone?: 'default' | 'danger'
    busy?: boolean
  }>(),
  {
    confirmLabel: 'Confirm',
    cancelLabel: 'Cancel',
    tone: 'default',
    busy: false,
  },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open && !props.busy) {
    emit('cancel')
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="open" class="scrim fade-in" @click.self="!busy && emit('cancel')">
    <div class="sheet" role="dialog" aria-modal="true" :aria-label="title">
      <h2 class="sheet__title">{{ title }}</h2>
      <p v-if="message" class="sheet__msg">{{ message }}</p>
      <slot />
      <div class="sheet__actions">
        <AppButton variant="ghost" :disabled="busy" @click="emit('cancel')">
          {{ cancelLabel }}
        </AppButton>
        <AppButton
          :variant="tone === 'danger' ? 'danger' : 'primary'"
          :loading="busy"
          @click="emit('confirm')"
        >
          {{ confirmLabel }}
        </AppButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: var(--s5);
  background: rgba(20, 24, 27, 0.42);
  backdrop-filter: blur(2px);
}

.sheet {
  width: min(440px, 100%);
  background: var(--surface);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-3);
  padding: var(--s6) var(--s6) var(--s5);
}

.sheet__title {
  font-family: var(--font-display);
  font-size: var(--t-xl);
  font-weight: 400;
  letter-spacing: -0.01em;
}

.sheet__msg {
  margin-top: var(--s3);
  color: var(--ink-500);
  font-size: var(--t-base);
}

.sheet__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--s3);
  margin-top: var(--s6);
}
</style>
