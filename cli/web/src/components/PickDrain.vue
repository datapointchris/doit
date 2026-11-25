<template>
  <span
    class="drain"
    :style="{ animationDuration: `${left}ms` }"
    aria-hidden="true"></span>
</template>

<script setup lang="ts">
// The time left to take a pick back, drawn as a line that drains to its commit_at.
const props = defineProps<{ commitAt: string }>()
const left = Math.max(Date.parse(props.commitAt) - Date.now(), 0)
</script>
