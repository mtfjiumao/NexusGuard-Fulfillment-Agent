<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { applicationStatus, applicationStatusLabels, formatAmount, outcomeLabels, reviewLabels } from '../api/damageReview';
import type { DamageDraft, UUID } from '../api/types';

const props = defineProps<{ damage: DamageDraft; disabled: boolean }>();
const locked = computed(() => props.damage.review?.locked_for_processing === true);
const approved = computed(() => props.damage.review?.result?.outcome === 'approved');
const editDisabled = computed(() => props.disabled || locked.value);
const request = ref('');
watch(() => props.damage.draft_id, () => {
  request.value = props.damage.review?.requested_resolution ?? '';
}, { immediate: true });
const emit = defineEmits<{
  discard: [pendingId: UUID];
  resolve: [payload: { itemId: UUID; pendingId: UUID }];
  select: [id: UUID]; remove: [id: UUID]; upload: [payload: { claimId: UUID; file: File }];
  submit: [request: string]; withdraw: []; removePhoto: [id: UUID];
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
    <h2>破损申报</h2>
    <p role="status">{{ damage.prompt }}</p>
    <section v-if="damage.review" class="review-result" aria-label="售后申请状态">
      <h3>第 {{ damage.review.revision }} 版 · {{ applicationStatusLabels[applicationStatus(damage.review)] }}</h3>
      <p v-if="damage.review.requested_resolution">申请诉求：{{ damage.review.requested_resolution }}</p>
      <template v-if="damage.review.result">
        <p><strong>{{ outcomeLabels[damage.review.result.outcome] }}</strong> · {{ damage.review.result.reason }}</p>
        <p>{{ damage.review.result.resolution }}</p>
        <p v-if="damage.review.result.refund_cents !== null && damage.review.result.refund_currency">
          审核确认 {{ formatAmount(damage.review.result.refund_cents, damage.review.result.refund_currency) }}
        </p>
        <p v-if="damage.review.result.refund_status_text">{{ damage.review.result.refund_status_text }}</p>
      </template>
      <ul>
        <li v-for="item in damage.review.items" :key="item.item_id">
          <strong>{{ item.title }}</strong> · {{ item.decision ? reviewLabels[item.decision]
            : damage.review.status === 'withdrawn' ? '审核已终止' : '待核验' }}
          <span v-if="item.reason"> · {{ item.reason }}</span>
        </li>
      </ul>
      <button v-if="locked" type="button" :disabled="disabled" @click="emit('withdraw')">
        撤回并修改
      </button>
    </section>
    <p v-if="damage.products.length === 0">此订单暂无可查询的商品明细，请联系店铺核实。</p>
    <div v-for="claim in damage.pending_claims" :key="claim.pending_claim_id" class="damage-product">
      <strong>待关联商品的信息</strong>
      <p>商品描述：{{ claim.reference ?? '尚未明确' }}；破损总数量：{{ claim.quantity ?? '待补充' }}</p>
      <p>破损描述：{{ claim.description ?? '待补充' }}</p>
      <label>
        确认这段信息属于哪个商品：
        <select :disabled="editDisabled" value="" @change="emit('resolve', {
          itemId: ($event.target as HTMLSelectElement).value, pendingId: claim.pending_claim_id,
        })">
          <option value="" disabled>选择订单中的具体商品</option>
          <option v-for="product in damage.products" :key="product.order_item_id" :value="product.order_item_id">
            {{ product.title }}（购买 {{ product.purchased_quantity }}）
          </option>
        </select>
      </label>
      <button type="button" :disabled="editDisabled" @click="emit('discard', claim.pending_claim_id)">
        放弃这段待关联信息
      </button>
    </div>
    <ol v-if="!approved" class="damage-products">
      <li v-for="product in damage.products" :key="product.order_item_id" class="damage-product">
        <strong>{{ product.title }}</strong>
        <p>购买数量：{{ product.purchased_quantity }}（订单单位）</p>
        <template v-if="product.included">
          <p>破损总数量：{{ product.damaged_quantity ?? '待补充' }}</p>
          <p>用户描述：{{ product.description ?? '待补充' }}</p>
          <p>当前草稿有 {{ product.attachments.length }} 张照片</p>
          <ol v-if="product.attachments.length && !locked" class="draft-photos">
            <li v-for="(photo, index) in product.attachments" :key="photo.attachment_id">
              照片 {{ index + 1 }}（{{ photo.media_type === 'image/png' ? 'PNG' : 'JPEG' }}）
              <button type="button" :disabled="editDisabled" @click="emit('removePhoto', photo.attachment_id)">
                移除
              </button>
            </li>
          </ol>
          <p v-if="!locked && damage.focused_item_id === product.order_item_id" class="focus-note">
            当前选中：下一条数量或描述补充将对应这个商品。
          </p>
          <div v-if="!locked" class="actions">
            <button type="button" :disabled="editDisabled" @click="emit('select', product.order_item_id)">
              补充或更正此商品
            </button>
            <button type="button" :disabled="editDisabled" @click="emit('remove', product.order_item_id)">
              从本次申报移除
            </button>
          </div>
          <label v-if="!locked && product.draft_item_id" class="photo-upload">
            上传此商品的破损照片（JPEG/PNG，单张最多 5 MB）
            <input type="file" accept="image/jpeg,image/png" :disabled="editDisabled"
              @change="choosePhoto($event, product.draft_item_id!)" />
          </label>
        </template>
        <button v-else-if="!locked" type="button" :disabled="editDisabled" @click="emit('select', product.order_item_id)">
          申报此商品破损
        </button>
      </li>
    </ol>
    <form v-if="damage.can_submit" class="review-form" @submit.prevent="emit('submit', request.trim())">
      <label>
        你希望怎么处理？
        <textarea v-model="request" maxlength="500" required :disabled="disabled" rows="2"
          placeholder="例如：申请退一盒压坏的苹果的钱" />
      </label>
      <button class="primary send" type="submit" :disabled="disabled || !request.trim()">
        {{ damage.review ? '确认提交补充申请' : '确认提交售后申请' }}
      </button>
    </form>
  </section>
</template>
