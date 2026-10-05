import { createRouter, createWebHistory } from 'vue-router'
import { useApp } from '@/composables/useApp'
import { useSession, rememberedSessionId } from '@/composables/useSession'
import { useBootstrap } from '@/composables/useBootstrap'
import { locationForSession } from '@/utils/flow'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/guides',
      name: 'guides',
      component: () => import('@/views/GuidesView.vue'),
    },
    {
      path: '/guide/:index(\\d+)',
      name: 'guide',
      component: () => import('@/views/StageGuideView.vue'),
      props: (route) => ({ index: Number(route.params.index) }),
    },
    {
      path: '/capture/:index(\\d+)',
      name: 'capture',
      component: () => import('@/views/CaptureView.vue'),
      props: (route) => ({ index: Number(route.params.index) }),
    },
    {
      path: '/finish',
      name: 'finish',
      component: () => import('@/views/FinishView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: { name: 'home' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

/**
 * Guards read the session from the backend rather than trusting the URL, so a
 * hand-typed address can never land the collector on a stage that is not
 * actually reachable. They also reload the session when the store is empty,
 * which is what makes a refresh mid-session survive.
 */
router.beforeEach(async (to) => {
  const { ensure } = useBootstrap()
  await ensure()

  if (to.name === 'home' || to.name === 'guides') {
    return true
  }

  const { health } = useApp()
  const { session, fetch } = useSession()

  if (!session.value) {
    const sid = health.value?.active_session?.session_id ?? rememberedSessionId()
    if (sid) {
      await fetch(sid)
    }
  }

  const current = session.value
  if (!current) {
    return { name: 'home' }
  }

  // A finished session has no stage flow left, only the summary.
  if (current.status === 'finished') {
    return to.name === 'finish' ? true : { name: 'finish' }
  }

  if (to.name === 'finish') {
    return locationForSession(current)
  }

  const index = Number(to.params.index)
  const stage = current.stages.find((s) => s.index === index)
  if (!stage) {
    return locationForSession(current)
  }

  if (to.name === 'guide') {
    // Earlier stages may be revisited, later ones may not.
    return index <= current.current_stage ? true : locationForSession(current)
  }

  if (to.name === 'capture') {
    if (index !== current.current_stage) {
      return locationForSession(current)
    }
    // A saved stage has nothing left to capture.
    if (stage.state === 'saved') {
      return { name: 'guide', params: { index: String(index) } }
    }
    return true
  }

  return true
})

export default router
