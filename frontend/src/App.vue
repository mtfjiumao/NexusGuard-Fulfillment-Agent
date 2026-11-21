<script setup lang="ts">
import DamageReviewView from './views/DamageReviewView.vue';
import LogisticsReviewView from './views/LogisticsReviewView.vue';
import ProductDetailView from './views/ProductDetailView.vue';
import ReceptionView from './views/ReceptionView.vue';
import RefundApprovalsView from './views/RefundApprovalsView.vue';

const path = window.location.pathname;
const isReview = path === '/damage-review';
const isRefundApproval = window.location.pathname === '/refund-approvals';
const productId = /^\/products\/([^/]+)$/.exec(window.location.pathname)?.[1];
</script>

<template>
  <DamageReviewView v-if="isReview" />
  <LogisticsReviewView v-else-if="path === '/logistics-review'" />
  <RefundApprovalsView v-else-if="isRefundApproval" />
  <ProductDetailView v-else-if="productId" :sku-id="productId" />
  <ReceptionView v-else />
</template>
