<script setup lang="ts">
import { computed } from 'vue'
import type { Stage, StageAction } from '@/api'
import AppButton from './AppButton.vue'

const props = defineProps<{
  stage: Stage | null
  busy: StageAction | null
  isLastStage: boolean
}>()

const emit = defineEmits<{
  start: []
  stop: []
  discard: []
  advance: []
}>()

const canStart = computed(() => props.stage?.allowed_actions.includes('start') === true)
const canStop = computed(() => props.stage?.allowed_actions.includes('stop') === true)
const canDiscard = computed(() => props.stage?.allowed_actions.includes('discard') === true)
const canAdvance = computed(() => props.stage?.allowed_actions.includes('advance') === true)

const busy = (action: StageAction) => props.busy === action
const locked = computed(() => props.busy !== null)

/*
 * Why a button is unavailable. Shown on hover so the grid layout can stay fixed
 * instead of reshuffling as the stage state changes.
 */
function hint(action: StageAction): string | undefined {
  const stage = props.stage
  if (!stage) {
    return undefined
  }
  switch (action) {
    case 'start':
      if (canStart.value) return undefined
      if (stage.state === 'recording') return 'A take is already running.'
      return 'This stage is already saved. Use Re-record to take it again.'
    case 'stop':
      if (canStop.value) return undefined
      return 'Nothing is recording right now.'
    case 'discard':
      if (canDiscard.value) return undefined
      return 'Record and save this stage first.'
    case 'advance':
      if (canAdvance.value) return undefined
      return 'Save this stage before moving on.'
    default:
      return undefined
  }
}
</script>

<template>
  <div class="ctl">
    <AppButton
      variant="primary"
      size="lg"
      block
      :disabled="!canStart || locked"
      :loading="busy('start')"
      :title="hint('start')"
      @click="emit('start')"
    >
      Start
    </AppButton>

    <AppButton
      class="ctl__stop"
      variant="secondary"
      size="lg"
      block
      :disabled="!canStop || locked"
      :loading="busy('stop')"
      :title="hint('stop')"
      @click="emit('stop')"
    >
      Stop
    </AppButton>

    <AppButton
      variant="secondary"
      :disabled="!canDiscard || locked"
      :loading="busy('discard')"
      :title="hint('discard')"
      @click="emit('discard')"
    >
      Re-record
    </AppButton>

    <AppButton
      variant="primary"
      :disabled="!canAdvance || locked"
      :loading="busy('advance')"
      :title="hint('advance')"
      @click="emit('advance')"
    >
      {{ isLastStage ? 'Finish session' : 'Next stage' }}
    </AppButton>
  </div>
</template>

<style scoped>
/*
 * Fixed 2x2 grid. Positions never move, so muscle memory holds on a touch
 * screen even as availability changes.
 */
.ctl {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s3);
}

.ctl__stop:not(:disabled) {
  background: var(--rec-fg);
  border-color: var(--rec-fg);
  color: #fff;
}

.ctl__stop:not(:disabled):hover {
  background: #87300f;
  border-color: #87300f;
}
</style>
