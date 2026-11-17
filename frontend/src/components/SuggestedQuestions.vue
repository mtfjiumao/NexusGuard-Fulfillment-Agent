<script setup lang="ts">
import type { Suggestion } from '../api/types';
defineProps<{ questions: Suggestion[]; disabled: boolean }>();
defineEmits<{ select: [text: string] }>();
</script>

<template>
  <section v-if="questions.length" aria-labelledby="question-title">
    <h2 id="question-title">你可能想问</h2>
    <div class="suggestions">
      <button
        v-for="question in questions" :key="question.key" type="button" :disabled="disabled"
        @click="$emit('select', question.text)"
      >{{ question.text }}</button>
    </div>
  </section>
</template>
