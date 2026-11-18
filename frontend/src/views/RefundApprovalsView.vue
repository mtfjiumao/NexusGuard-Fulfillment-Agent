<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { formatAmount } from '../api/damageReview';
import { ApiError, requestJson } from '../api/reception';
import type { UUID } from '../api/types';

interface Approval {
  approval_id: UUID;
  order_number: string;
  user_id: UUID;
  amount_cents: number;
  currency: string;
  reasons: string[];
  requested_resolution: string;
  resolution_reason: string;
  resolution: string;
  expires_at: string;
}

const path = '/api/v1/approvals/refunds';
const queue = ref<Approval[]>([]);
const busy = ref(false);
const notice = ref('');
const failed = ref(false);
const dateTime = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
const reasons: Record<string, string> = {
  amount_over_200_cny: '退款金额超过 ¥200',
  return_rate_over_30_percent: '历史退货率超过 30%',
  return_history_unavailable: '缺少有效退货率',
  currency_requires_review: '非人民币订单，需人工确认金额',
};

async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  notice.value = '';
  failed.value = false;
  try {
    await action();
  } catch (error) {
    failed.value = true;
    const code = error instanceof ApiError ? error.status : 0;
    notice.value = code === 401 || code === 403 ? '请使用具备退款审批权限的主管账号。'
      : code === 409 ? '此申请状态已变化，请刷新队列。' : '暂时无法完成请求，请重试。';
  } finally {
    busy.value = false;
  }
}

function refresh() {
  return perform(async () => { queue.value = await requestJson<Approval[]>(path); });
}

async function decide(event: SubmitEvent, id: UUID) {
  const form = event.currentTarget as HTMLFormElement;
  const fields = new FormData(form);
  const reason = String(fields.get('reason') ?? '').trim();
  const decision = fields.get('decision');
  if (!reason || (decision !== 'approved' && decision !== 'rejected')) {
    notice.value = '请选择审批结论并填写原因。';
    failed.value = true;
    return;
  }
  await perform(async () => {
    const result = await requestJson<{ status: string }>(`${path}/${encodeURIComponent(id)}/decision`, {
      decision, reason,
    });
    queue.value = queue.value.filter(item => item.approval_id !== id);
    notice.value = result.status === 'expired' ? '这份审批已超时。' : '审批结论已保存。';
  });
}

onMounted(refresh);
</script>

<template>
  <main class="review-workspace" aria-labelledby="approval-title" :aria-busy="busy">
    <header class="review-header">
      <h1 id="approval-title">退款审批</h1>
      <p class="notice" :class="{ error: failed }" role="status">{{ notice }}</p>
    </header>
    <button type="button" :disabled="busy" @click="refresh">刷新待办</button>
    <p v-if="!queue.length && !busy && !failed">当前没有待审批的退款。</p>
    <article v-for="item in queue" :key="item.approval_id" class="context">
      <h2>{{ item.order_number }} · {{ formatAmount(item.amount_cents, item.currency) }}</h2>
      <p class="order-meta">顾客标识 {{ item.user_id }} · {{ dateTime.format(new Date(item.expires_at)) }} 截止</p>
      <dl>
        <dt>触发原因</dt><dd>{{ item.reasons.map(code => reasons[code] ?? code).join('、') }}</dd>
        <dt>顾客诉求</dt><dd>{{ item.requested_resolution }}</dd>
        <dt>售后处理结论</dt><dd>{{ item.resolution_reason }} · {{ item.resolution }}</dd>
      </dl>
      <form class="review-form" @submit.prevent="decide($event, item.approval_id)">
        <label>
          审批结论
          <select name="decision" required :disabled="busy">
            <option value="">请选择</option>
            <option value="approved">同意执行退款</option>
            <option value="rejected">驳回退款</option>
          </select>
        </label>
        <label>审批原因（驳回时顾客可见） <textarea name="reason" maxlength="500" required :disabled="busy" rows="2" /></label>
        <button type="submit" class="send" :disabled="busy">保存审批结论</button>
      </form>
    </article>
  </main>
</template>
