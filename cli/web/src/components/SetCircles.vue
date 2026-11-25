<template>
  <div
    class="set-row"
    :class="{ full: sets >= item.target }">
    <span>{{ item.name }}</span>
    <span class="circles">
      <button
        v-for="circle in circles"
        :key="circle.index"
        type="button"
        class="circle"
        :class="{ filled: circle.filled, pending: circle.pick !== null }"
        :data-key="circle.key"
        :disabled="circle.key === null || committing"
        :aria-label="circle.label"
        @click="circle.key && emit('pick', circle.key)">
        <PickDrain
          v-if="circle.pick?.state === 'pending'"
          :key="circle.pick.id"
          :commit-at="circle.pick.commit_at" />
      </button>
    </span>
    <span class="count">{{ sets }}/{{ item.target }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { pickOf, setsCounted, underWay, type ChecklistItem, type ShownPick } from '../panel'
import PickDrain from './PickDrain.vue'

const props = defineProps<{ item: ChecklistItem; picks: ShownPick[] }>()
const emit = defineEmits<{ pick: [key: string] }>()

const sets = computed(() => setsCounted(props.item, props.picks))
const waiting = (key: string | null) => {
  const pick = pickOf(props.picks, key)
  return pick && underWay(pick) ? pick : null
}
const tick = computed(() => waiting(props.item.key))
const untick = computed(() => waiting(props.item.untick_key))
const committing = computed(() => tick.value?.state === 'committing' || untick.value?.state === 'committing')

// Each circle is one set: a filled one takes the latest set back, and an empty
// one adds a set. A tick waiting shows on the first empty circle, and an
// untick waiting on the last filled one.
const circles = computed(() => {
  const count = Math.max(props.item.target, sets.value + (tick.value ? 1 : 0))
  return Array.from({ length: count }, (_, index) => {
    const filled = index < sets.value
    let pick: ShownPick | null = null
    if (tick.value && index === sets.value) pick = tick.value
    if (untick.value && index === sets.value - 1) pick = untick.value
    const key = filled ? props.item.untick_key : props.item.key
    const label = `${props.item.name}: set ${index + 1} of ${props.item.target}, ${filled ? 'take it back' : 'tick it'}`
    return { index, filled, pick, key, label }
  })
})
</script>
