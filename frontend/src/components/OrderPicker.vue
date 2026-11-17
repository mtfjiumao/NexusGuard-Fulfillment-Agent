<script setup lang="ts">
import type { OrderCard as Order } from '../api/types';
import OrderCard from './OrderCard.vue';
defineProps<{
  orders: Order[]; disabled: boolean; hasMore: boolean; failed: boolean;
}>();
defineEmits<{ select: [orderId: string]; loadMore: []; retry: [] }>();
</script>

<template>
  <section class="context" aria-labelledby="orders-title">
    <h2 id="orders-title">选择咨询订单</h2>
    <div class="order-list">
      <OrderCard v-for="order in orders" :key="order.order_id" :order="order" title="历史订单">
        <button type="button" :disabled="disabled" @click="$emit('select', order.order_id)">
          咨询这笔订单
        </button>
      </OrderCard>
      <p v-if="!orders.length && !failed">暂无可选择的订单，你可以直接咨询商品或其他问题。</p>
    </div>
    <p v-if="failed" role="status">暂时无法加载订单列表。</p>
    <button v-if="failed" type="button" :disabled="disabled" @click="$emit('retry')">重试加载</button>
    <button v-else-if="hasMore" type="button" :disabled="disabled" @click="$emit('loadMore')">
      查看更多订单
    </button>
  </section>
</template>
