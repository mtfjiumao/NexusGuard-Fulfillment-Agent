<script setup lang="ts">
import DamageReviewView from './views/DamageReviewView.vue';
import ProductDetailView from './views/ProductDetailView.vue';
import ReceptionView from './views/ReceptionView.vue';
import RefundApprovalsView from './views/RefundApprovalsView.vue';

const isReview = window.location.pathname === '/damage-review';
const isRefundApproval = window.location.pathname === '/refund-approvals';
const productId = /^\/products\/([^/]+)$/.exec(window.location.pathname)?.[1];
</script>

<template>
  <DamageReviewView v-if="isReview" />
  <RefundApprovalsView v-else-if="isRefundApproval" />
  <ProductDetailView v-else-if="productId" :sku-id="productId" />
  <ReceptionView v-else />
</template>
