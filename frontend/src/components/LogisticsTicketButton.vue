<script setup lang="ts">
import { ref } from 'vue';
import { logisticsTickets } from '../api/logisticsTickets';
import { ApiError } from '../api/reception';
import type { UUID } from '../api/types';

const props = defineProps<{
  sessionId: UUID; orderId: UUID; replyId: UUID; orderNumber: string; disabled: boolean;
}>();
const confirming = ref(false);
const busy = ref(false);
const submitted = ref(false);
const notice = ref('');

async function submit() {
  if (busy.value || submitted.value || props.disabled) return;
  busy.value = true;
  notice.value = '';
  try {
    const ticket = await logisticsTickets.submit(props.sessionId, props.orderId, props.replyId);
    submitted.value = true;
    confirming.value = false;
    notice.value = ticket.resolved_at
      ? '这次查件已处理，请查看会话通知。'
      : '人工查件正在处理，办结后会通知你。';
  } catch (error) {
    if (error instanceof ApiError && error.code === 'logistics_exception_cleared') {
      notice.value = '物流状态已有更新，请重新查询包裹进度。';
      confirming.value = false;
    } else if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
      notice.value = '当前订单不可用，请重新登录或选择订单。';
    } else {
      notice.value = '暂时无法确认提交结果，请重试。';
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div>
    <div v-if="confirming" class="context">
      <p>提交订单 {{ orderNumber }} 的物流记录，请工作人员查件。</p>
      <button class="send" type="button" :disabled="disabled || busy" @click="submit">确认提交</button>
      <button type="button" :disabled="busy" @click="confirming = false">取消</button>
    </div>
    <button v-else-if="!submitted" type="button" :disabled="disabled" @click="confirming = true">请人工查件</button>
    <p v-if="notice" role="status">{{ notice }}</p>
  </div>
</template>
