<template>
  <section class="card today-card">
    <h2 class="card-title">Today</h2>
    <div
      v-for="meter in meters"
      :key="meter.name"
      class="meter">
      <span class="name">{{ meter.name }}</span>
      <div
        class="bar"
        :class="{ minutes: meter.name === 'minutes' }">
        <i :style="{ width: `${meter.share}%` }"></i>
      </div>
      <span
        class="value"
        :class="{ late: meter.unavailable }"
        :title="meter.unavailable ?? undefined">
        {{ meter.text }}
      </span>
    </div>
    <div
      v-if="totals.minutes || totals.checkoffs"
      class="behind">
      Behind overall
      <strong class="num">{{ minutesText(totals.minutes?.behind ?? 0) }}</strong>
      and
      <strong class="num">{{ Math.round(totals.checkoffs?.behind ?? 0) }} checkoffs</strong>
    </div>
    <p
      v-if="totals.unavailable"
      class="failed">
      {{ totals.unavailable }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { minutesText, type View } from '../panel'

const props = defineProps<{ view: View }>()
const totals = computed(() => props.view.totals)

interface Meter {
  name: string
  share: number
  text: string
  unavailable: string | null
}

const meters = computed<Meter[]>(() => {
  const minutes = totals.value.minutes
  const drawn: Meter[] = [
    minutes
      ? {
          name: 'minutes',
          share: minutes.need ? Math.min(100, (minutes.done / minutes.need) * 100) : 0,
          text: `${minutesText(minutes.done)} of ${minutesText(minutes.need)}`,
          unavailable: null,
        }
      : { name: 'minutes', share: 0, text: 'unread', unavailable: totals.value.unavailable || null },
  ]
  for (const checklist of totals.value.checklists) {
    const { done, total } = checklist
    drawn.push(
      done === null || total === null
        ? { name: checklist.name, share: 0, text: 'unread', unavailable: checklist.unavailable || null }
        : {
            name: checklist.name,
            share: total ? Math.min(100, (done / total) * 100) : 0,
            text: `${done} of ${total}`,
            unavailable: null,
          },
    )
  }
  return drawn
})
</script>
