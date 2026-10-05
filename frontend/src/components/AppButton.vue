<script setup lang="ts">
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    disabled?: boolean
    loading?: boolean
    block?: boolean
    type?: 'button' | 'submit'
  }>(),
  {
    variant: 'secondary',
    size: 'md',
    disabled: false,
    loading: false,
    block: false,
    type: 'button',
  },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

function onClick(event: MouseEvent) {
  if (props.disabled || props.loading) {
    event.preventDefault()
    return
  }
  emit('click', event)
}
</script>

<template>
  <button
    :type="type"
    class="btn"
    :class="[`btn--${variant}`, `btn--${size}`, { 'btn--block': block, 'btn--busy': loading }]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    @click="onClick"
  >
    <span v-if="loading" class="btn__spinner" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--s2);
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  font-weight: 500;
  white-space: nowrap;
  transition:
    background-color var(--dur) var(--ease),
    border-color var(--dur) var(--ease),
    color var(--dur) var(--ease);
}

.btn--block {
  width: 100%;
}

/* Sizes. Minimum hit target stays at 40px so this works on a wall display. */
.btn--sm {
  min-height: 32px;
  padding: 0 var(--s3);
  font-size: var(--t-sm);
}

.btn--md {
  min-height: 40px;
  padding: 0 var(--s4);
  font-size: var(--t-base);
}

.btn--lg {
  min-height: 52px;
  padding: 0 var(--s6);
  font-size: var(--t-md);
}

.btn--primary {
  background: var(--accent);
  color: var(--accent-ink);
}

.btn--primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn--secondary {
  background: var(--surface);
  border-color: var(--line-strong);
  color: var(--ink-900);
}

.btn--secondary:hover:not(:disabled) {
  background: var(--surface-hover);
  border-color: var(--ink-400);
}

.btn--ghost {
  background: transparent;
  color: var(--ink-500);
}

.btn--ghost:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--ink-900);
}

.btn--danger {
  background: var(--surface);
  border-color: var(--danger-line);
  color: var(--danger);
}

.btn--danger:hover:not(:disabled) {
  background: var(--danger-bg);
}

.btn:disabled {
  cursor: not-allowed;
}

/* Disabled reads as flat and recedes, rather than merely faded. */
.btn:disabled:not(.btn--busy) {
  background: var(--surface-sunken);
  border-color: var(--line);
  color: var(--ink-300);
}

.btn--busy {
  cursor: progress;
  opacity: 0.75;
}

.btn__spinner {
  width: 13px;
  height: 13px;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: var(--r-full);
  animation: btn-spin 620ms linear infinite;
}

@keyframes btn-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
