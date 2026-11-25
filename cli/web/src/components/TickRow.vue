<template>
  <button
    type="button"
    class="tick"
    :class="shown.look"
    :disabled="!pickable"
    :data-key="pickKey"
    @click="emit('pick')">
    <span class="box"></span>
    <span class="label">
      <template v-if="sub">
        <div>{{ label }}</div>
        <div class="sub">{{ sub }}</div>
      </template>
      <template v-else>{{ label }}</template>
    </span>
    <span
      v-if="shown.look === 'pending'"
      class="note undo">
      click again to undo
    </span>
    <span
      v-else-if="shown.said"
      class="said"
      :title="shown.said">
      {{ shown.said }}
    </span>
    <span
      v-else-if="note"
      class="note"
      :class="{ late: noteLate }">
      {{ note }}
    </span>
    <PickDrain
      v-if="pick?.state === 'pending'"
      :key="pick.id"
      :commit-at="pick.commit_at" />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { lookOf, type ShownPick } from '../panel'
import PickDrain from './PickDrain.vue'

const props = defineProps<{
  label: string
  pickKey: string | null
  pick: ShownPick | null
  done?: boolean
  sub?: string
  note?: string | null
  noteLate?: boolean
}>()
const emit = defineEmits<{ pick: [] }>()

const shown = computed(() => lookOf(props.pick, props.done ?? false))
// A row with no key, or one done or committing, takes no click, and Tab passes it.
const pickable = computed(() => props.pickKey !== null && (shown.value.look === 'idle' || shown.value.look === 'pending'))
</script>
