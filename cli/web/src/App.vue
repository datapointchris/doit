<template>
  <div
    v-if="!panel || !view"
    class="empty-board">
    <p>the watch is starting</p>
    <p
      v-if="state.unanswered"
      class="muted">
      {{ state.unanswered }}
    </p>
  </div>
  <main
    v-else
    class="board"
    :class="{ stale: marked }">
    <header class="topline full">
      <span class="date">{{ dateText }}</span>
      <span>
        <span v-if="minutes">{{ minutesText(minutes.done) }} of {{ minutesText(minutes.need) }} ·</span>
        habits {{ habitsDone }} of {{ view.habits.rows.length }}
        <span
          class="read"
          :class="{ stale: marked }"
          :title="state.unanswered ?? panel.stale_reason?.detail">
          · {{ readText }}
        </span>
      </span>
    </header>
    <SuggestionCard
      class="wide"
      :view="view"
      :picks="panel.picks"
      :answers="panel.nudge?.answers ?? []" />
    <TodayCard :view="view" />
    <HabitsCard
      :view="view"
      :picks="panel.picks"
      :picked="panel.picked" />
    <PursuitsCard
      :view="view"
      :picks="panel.picks" />
    <div class="stack">
      <LaneCard
        v-for="lane in lanes"
        :key="lane.name"
        :lane="lane"
        :picks="panel.picks" />
    </div>
    <footer class="foot full">
      <span>
        A click completes a row in a few seconds, and a second click takes it back.
        <span
          v-if="view.problems.length"
          class="problems">
          {{ view.problems.join(' · ') }}
        </span>
      </span>
      <span>
        <kbd>Tab</kbd>
        move
        <kbd>Space</kbd>
        pick
        <kbd>Esc</kbd>
        close
      </span>
    </footer>
  </main>
  <div
    v-if="state.notice"
    class="notice"
    role="status">
    {{ state.notice }}
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { ageText, clockText, dayOf, minutesText } from './panel'
import { createPanelStore, panelStoreKey, ROLLOVER_POLL_MS } from './store'
import { closeWindow } from './watch'
import HabitsCard from './components/HabitsCard.vue'
import LaneCard from './components/LaneCard.vue'
import PursuitsCard from './components/PursuitsCard.vue'
import SuggestionCard from './components/SuggestionCard.vue'
import TodayCard from './components/TodayCard.vue'

const store = createPanelStore()
provide(panelStoreKey, store)
const state = store.state

const panel = computed(() => state.panel)
const view = computed(() => state.panel?.view ?? null)
const minutes = computed(() => view.value?.totals.minutes ?? null)
const habitsDone = computed(() => view.value?.habits.rows.filter((row) => row.done).length ?? 0)
// Lanes a pick completes come first, as layout C stacks review and practice over upcoming.
const lanes = computed(() => {
  const all = view.value?.lanes ?? []
  const pickable = (lane: (typeof all)[number]) => lane.rows.some((row) => row.key !== null)
  return [...all.filter(pickable), ...all.filter((lane) => !pickable(lane))]
})

const clock = ref(store.now())
const dateText = computed(() =>
  new Date(`${state.day}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short' }),
)
// The rows are marked where the watch's latest read failed, where the watch
// stopped answering, or where they were read on an earlier day.
const marked = computed(() => {
  const shown = panel.value
  if (!shown) return false
  const readDay = shown.read_at ? dayOf(new Date(shown.read_at)) : null
  return shown.stale_since !== null || state.unanswered !== null || readDay !== state.day
})
const readText = computed(() => {
  const shown = panel.value
  if (!shown?.read_at) return 'not read yet'
  if (state.unanswered) return `the watch stopped answering · rows read ${ageText(shown.read_at, clock.value)}`
  if (shown.stale_since) return `stale since ${clockText(shown.stale_since)} · rows from ${clockText(shown.read_at)}`
  if (dayOf(new Date(shown.read_at)) !== state.day) return `rows from ${dayOf(new Date(shown.read_at))}`
  return `read ${ageText(shown.read_at, clock.value)}`
})

function onVisible() {
  if (document.visibilityState === 'visible') void store.load()
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeWindow()
  }
}

let rollover: ReturnType<typeof setInterval> | undefined
let ticking: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  document.addEventListener('visibilitychange', onVisible)
  window.addEventListener('keydown', onKey)
  // A window that never goes hidden fires no visibility change at midnight.
  rollover = setInterval(() => void store.syncDay(), ROLLOVER_POLL_MS)
  ticking = setInterval(() => (clock.value = store.now()), 30_000)
  void store.load()
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisible)
  window.removeEventListener('keydown', onKey)
  clearInterval(rollover)
  clearInterval(ticking)
  store.stop()
})
</script>
