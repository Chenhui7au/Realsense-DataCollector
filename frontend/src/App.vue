<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import ToastStack from '@/components/ToastStack.vue'
import { useApp } from '@/composables/useApp'
import { useGuides } from '@/composables/useGuides'
import { useSession } from '@/composables/useSession'
import type { RailItem } from '@/components/StageRail.vue'

const route = useRoute()
const { totalStages, stageName } = useApp()
const { byIndex, uploaded } = useGuides()
const { session, stages, currentIndex } = useSession()

const pad = (n: number) => String(n).padStart(2, '0')

/*
 * The rail is the one piece of chrome that never moves. On the setup screen it
 * tracks configured diagrams, everywhere else it tracks recorded stages.
 */
const railItems = computed<RailItem[]>(() => {
  const count = totalStages.value
  const onGuides = route.name === 'guides'

  return Array.from({ length: count }, (_, i) => {
    const index = i + 1
    const name = stageName(index)

    if (onGuides) {
      const configured = byIndex.value.get(index)?.configured === true
      return { index, name, tone: configured ? ('done' as const) : ('pending' as const) }
    }

    const stage = stages.value.find((s) => s.index === index)
    if (stage?.state === 'recording') {
      return { index, name, tone: 'recording' as const }
    }
    if (stage?.state === 'saved') {
      return { index, name, tone: 'done' as const }
    }
    if (index === currentIndex.value && stages.value.length > 0) {
      return { index, name, tone: 'current' as const }
    }
    return { index, name, tone: 'pending' as const }
  })
})

const headerMeta = computed<string | null>(() => {
  const name = session.value?.name ?? null

  if (route.name === 'capture' || route.name === 'guide') {
    const stage = `Stage ${pad(Number(route.params.index))} / ${pad(totalStages.value)}`
    return name ? `${name} \u00b7 ${stage}` : stage
  }
  if (route.name === 'finish') {
    return name ?? 'Session complete'
  }
  if (route.name === 'guides') {
    return `${uploaded.value} of ${totalStages.value} configured`
  }
  return null
})
</script>

<template>
  <AppHeader :items="railItems" rail-label="Stage progress">
    <template #meta>
      <span v-if="headerMeta" class="mono">{{ headerMeta }}</span>
    </template>
  </AppHeader>

  <main>
    <RouterView />
  </main>

  <ToastStack />
</template>
