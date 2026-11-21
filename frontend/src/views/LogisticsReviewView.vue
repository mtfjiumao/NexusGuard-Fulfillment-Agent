<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { logisticsTickets } from '../api/logisticsTickets';
import type { StaffTicket } from '../api/logisticsTickets';
import { ApiError } from '../api/reception';

const tickets = ref<StaffTicket[]>([]);
const resolved = ref(false);
const busy = ref(false);
const notice = ref('');
const offset = ref(0);
const hasMore = ref(false);
const shipmentStatus: Record<string, string> = {
  pending: '待发运', shipped: '已发运', in_transit: '运输中', exception: '异常', delivered: '已签收',
};
const formatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
const date = (iso: string | null) => iso ? formatter.format(new Date(iso)) : '暂无记录';

async function refresh(next = 0) {
  if (busy.value) return;
  busy.value = true;
  notice.value = '';
  try {
    tickets.value = await logisticsTickets.queue(resolved.value, next);
    offset.value = next;
    hasMore.value = tickets.value.length === 20;
  } catch (error) {
    tickets.value = [];
    offset.value = 0;
    hasMore.value = false;
    notice.value = error instanceof ApiError && (error.status === 401 || error.status === 403)
      ? '请使用有物流查件权限的员工账号。' : '暂时无法加载工单，请重试。';
  } finally {
    busy.value = false;
  }
}

async function finish(event: SubmitEvent, ticket: StaffTicket) {
  if (busy.value) return;
  const fields = new FormData(event.currentTarget as HTMLFormElement);
  const result = String(fields.get('result') ?? '').trim();
  if (!result) {
    notice.value = '请填写查件结果。';
    return;
  }
  busy.value = true;
  try {
    await logisticsTickets.resolve(ticket.id, result);
    tickets.value = tickets.value.filter(row => row.id !== ticket.id);
    notice.value = '已办结，结果会发回顾客的客服会话。';
  } catch (error) {
    notice.value = error instanceof ApiError && error.status === 409
      ? '工单已由其他员工处理，请刷新队列。' : '暂时无法确认保存结果，请重试。';
  } finally {
    busy.value = false;
  }
}

onMounted(() => refresh());
</script>

<template>
  <main class="review-workspace" aria-labelledby="logistics-title" :aria-busy="busy">
    <header class="review-header">
      <h1 id="logistics-title">人工查件</h1>
      <p class="notice" role="status">{{ notice }}</p>
    </header>
    <div class="review-toolbar">
      <label>工单状态
        <select v-model="resolved" :disabled="busy" @change="refresh()">
          <option :value="false">待处理</option>
          <option :value="true">已办结</option>
        </select>
      </label>
      <button type="button" :disabled="busy" @click="refresh(offset)">刷新</button>
    </div>
    <p v-if="!tickets.length && !busy && !notice">当前没有工单。</p>
    <article v-for="ticket in tickets" :key="ticket.id" class="context">
      <h2>订单 {{ ticket.order_number }}</h2>
      <p>创建于 {{ date(ticket.created_at) }} · 物流记录截至 {{ date(ticket.facts.observed_at) }}</p>
      <details v-for="shipment in ticket.facts.shipments" :key="shipment.shipment_id" open>
        <summary>{{ shipment.carrier }} · {{ shipment.tracking_no ?? '暂无运单号' }} · {{ shipmentStatus[shipment.status] ?? shipment.status }}</summary>
        <p>承诺送达 {{ date(shipment.promised_delivery_at) }} · 最近更新 {{ date(shipment.last_update_at) }}</p>
        <ul v-if="shipment.recent_events.length">
          <li v-for="(item, index) in shipment.recent_events" :key="index">{{ date(item.at) }} · {{ item.type }}</li>
        </ul>
      </details>
      <template v-if="ticket.resolved_at">
        <p>处理于 {{ date(ticket.resolved_at) }} · {{ ticket.reviewer_id }}</p>
        <p class="review-description">{{ ticket.result }}</p>
      </template>
      <form v-else class="review-form" @submit.prevent="finish($event, ticket)">
        <label>查件结果（顾客可见）<textarea name="result" rows="3" maxlength="1000" required :disabled="busy" /></label>
        <button class="send" type="submit" :disabled="busy">办结并通知顾客</button>
      </form>
    </article>
    <div class="actions">
      <button v-if="offset > 0" type="button" :disabled="busy" @click="refresh(offset - 20)">上一页</button>
      <button v-if="hasMore" type="button" :disabled="busy" @click="refresh(offset + 20)">下一页</button>
    </div>
  </main>
</template>
