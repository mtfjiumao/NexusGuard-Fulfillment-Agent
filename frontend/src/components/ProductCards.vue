<script setup lang="ts">
import { catalogImageUrl, productPrice } from '../api/catalog';
import type { ProductCard } from '../api/types';

defineProps<{ products: ProductCard[] }>();
</script>

<template>
  <ul v-if="products.length" class="product-cards" aria-label="匹配的在售商品">
    <li v-for="product in products" :key="product.sku_id">
      <a class="product-card" :href="product.url" target="_blank" rel="noopener noreferrer">
        <img v-if="product.image_url" :src="catalogImageUrl(product.image_url)" alt="" loading="lazy" />
        <span class="product-card-text">
          <strong>{{ product.title }}</strong>
          <span v-if="product.specification" class="order-meta">{{ product.specification }}</span>
          <span class="product-price">{{ productPrice(product) }}</span>
        </span>
        <span class="product-card-link">查看详情 <span aria-hidden="true">↗</span></span>
      </a>
    </li>
  </ul>
</template>
