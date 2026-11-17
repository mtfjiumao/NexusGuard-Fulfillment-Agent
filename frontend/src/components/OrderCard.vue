<script setup lang="ts">
import { computed } from 'vue';
import type { OrderCard } from '../api/types';

const props = defineProps<{ order: OrderCard; title: string }>();
const paymentLabels = { unpaid: '待付款', paid: '已付款', closed: '已关闭' };
const fulfillmentLabels = {
  unshipped: '未发货', processing: '出库处理中', shipped: '运输中',
  delivered: '已签收', cancelled: '已取消',
};
const amount = computed(() => {
  // The API currently serializes BIGINT as JSON number. Never display a rounded amount as exact.
  if (!Number.isSafeInteger(props.order.paid_amount_cents)) return '金额暂不可显示';
  const cents = BigInt(props.order.paid_amount_cents);
  return `${props.order.currency} ${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`;
});
const orderedDate = computed(() => new Date(props.order.ordered_at).toLocaleDateString('zh-CN'));
</script>

<template>
  <article class="order">
    <span class="order-title">{{ title }}</span>
    <div class="order-number">订单 {{ order.order_number }}</div>
    <p class="order-meta">
      {{ paymentLabels[order.payment_status] }} · {{ fulfillmentLabels[order.fulfillment_status] }}
      · {{ amount }} · {{ orderedDate }}
      <span v-if="order.has_open_aftersales"> · 售后处理中</span>
    </p>
    <slot />
  </article>
</template>
