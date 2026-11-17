<script setup lang="ts">
const text = defineModel<string>({ required: true });
defineProps<{ disabled: boolean; status: string }>();
defineEmits<{ send: [text: string] }>();
</script>

<template>
  <form class="composer" @submit.prevent="$emit('send', text)">
    <label for="message">也可以直接输入问题</label>
    <textarea
      id="message" v-model="text" rows="3" maxlength="4000" required
      placeholder="请输入你想咨询的内容…"
    />
    <div class="send-row">
      <span role="status">{{ status }}</span>
      <button class="send" type="submit" :disabled="disabled || !text.trim()">发送</button>
    </div>
  </form>
</template>
