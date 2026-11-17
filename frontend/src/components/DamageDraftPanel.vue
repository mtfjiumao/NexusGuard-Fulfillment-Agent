<script setup lang="ts">
import type { DamageDraft, UUID } from '../api/types';

defineProps<{ damage: DamageDraft; disabled: boolean }>();
const emit = defineEmits<{
  discard: [pendingId: UUID];
  resolve: [payload: { itemId: UUID; pendingId: UUID }];
  select: [id: UUID]; remove: [id: UUID]; upload: [payload: { claimId: UUID; file: File }];
}>();
function choosePhoto(event: Event, claimId: UUID) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) emit('upload', { claimId, file });
  input.value = '';
}
</script>

<template>
  <section class="context damage-panel" aria-label="破损申报信息">
    <h2>破损申报 · {{ damage.status === 'ready_for_review' ? '材料已收齐，待核验' : '正在收集信息' }}</h2>
    <p role="status">{{ damage.prompt }}</p>
    <p class="order-meta">数量按订单购买单位填写。照片收到后仍需审核，当前没有执行退款。</p>
    <p v-if="damage.products.length === 0">此订单暂无可查询的商品明细，请联系店铺核实。</p>
    <div v-for="claim in damage.pending_claims" :key="claim.pending_claim_id" class="damage-product">
      <strong>待关联商品的信息</strong>
      <p>商品描述：{{ claim.reference ?? '尚未明确' }}；破损总数量：{{ claim.quantity ?? '待补充' }}</p>
      <p>破损描述：{{ claim.description ?? '待补充' }}</p>
      <label>
        确认这段信息属于哪个商品：
        <select :disabled="disabled" value="" @change="emit('resolve', {
          itemId: ($event.target as HTMLSelectElement).value, pendingId: claim.pending_claim_id,
        })">
          <option value="" disabled>选择订单中的具体商品</option>
          <option v-for="product in damage.products" :key="product.order_item_id" :value="product.order_item_id">
            {{ product.title }}（购买 {{ product.purchased_quantity }}）
          </option>
        </select>
      </label>
      <button type="button" :disabled="disabled" @click="emit('discard', claim.pending_claim_id)">
        放弃这段待关联信息
      </button>
    </div>
    <ol class="damage-products">
      <li v-for="product in damage.products" :key="product.order_item_id" class="damage-product">
        <strong>{{ product.title }}</strong>
        <p>购买数量：{{ product.purchased_quantity }}（订单单位）</p>
        <template v-if="product.included">
          <p>破损总数量：{{ product.damaged_quantity ?? '待补充' }}</p>
          <p>用户描述：{{ product.description ?? '待补充' }}</p>
          <p>已收到 {{ product.attachments.length }} 张照片，均待审核。</p>
          <p v-if="damage.focused_item_id === product.order_item_id" class="focus-note">
            当前选中：下一条数量或描述补充将对应这个商品。
          </p>
          <div class="actions">
            <button type="button" :disabled="disabled" @click="emit('select', product.order_item_id)">
              补充或更正此商品
            </button>
            <button type="button" :disabled="disabled" @click="emit('remove', product.order_item_id)">
              从本次申报移除
            </button>
          </div>
          <label v-if="product.draft_item_id" class="photo-upload">
            上传此商品的破损照片（JPEG/PNG，单张最多 5 MB）
            <input type="file" accept="image/jpeg,image/png" :disabled="disabled"
              @change="choosePhoto($event, product.draft_item_id!)" />
          </label>
        </template>
        <button v-else type="button" :disabled="disabled" @click="emit('select', product.order_item_id)">
          申报此商品破损
        </button>
      </li>
    </ol>
  </section>
</template>
