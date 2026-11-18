<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { catalogImageUrl, loadProduct, productPrice } from '../api/catalog';
import { ApiError } from '../api/reception';
import type { ProductCard, UUID } from '../api/types';

const props = defineProps<{ skuId: UUID }>();
const product = ref<ProductCard | null>(null);
const status = ref('正在加载商品…');

onMounted(async () => {
  try {
    product.value = await loadProduct(props.skuId);
    document.title = product.value.title;
    status.value = '';
  } catch (error) {
    const code = error instanceof ApiError ? error.status : 0;
    status.value = code === 404 ? '商品已下架或暂不可查看。'
      : code === 401 || code === 403 ? '请登录店铺账号后查看商品。'
        : '暂时无法加载商品，请刷新页面重试。';
  }
});
</script>

<template>
  <main class="product-detail">
    <article v-if="product" class="product-detail-layout">
      <img v-if="product.image_url" :src="catalogImageUrl(product.image_url)" :alt="product.title" />
      <div>
        <h1>{{ product.title }}</h1>
        <p class="product-price">{{ productPrice(product) }}</p>
        <dl v-if="product.specification">
          <dt>规格</dt>
          <dd>{{ product.specification }}</dd>
        </dl>
        <a :href="`/customer-service?sku_id=${encodeURIComponent(product.sku_id)}`">
          咨询此商品
        </a>
      </div>
    </article>
    <p v-else role="status">{{ status }}</p>
  </main>
</template>
