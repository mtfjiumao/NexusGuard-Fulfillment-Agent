<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import DamageDraftPanel from '../components/DamageDraftPanel.vue';
import MessageComposer from '../components/MessageComposer.vue';
import OrderCard from '../components/OrderCard.vue';
import OrderPicker from '../components/OrderPicker.vue';
import ProductCards from '../components/ProductCards.vue';
import ShippingAddressForm from '../components/ShippingAddressForm.vue';
import SuggestedQuestions from '../components/SuggestedQuestions.vue';
import { useReception } from '../composables/useReception';

const {
  view, busy, notice, hasError, draft, orders, nextOffset, ordersFailed, messages,
  applicationNotices,
  pending, selecting, activeOrder, disabled, deliveryStatus, nextBefore, syncFailed,
  messageStatus, olderMessages, synchronize, pendingPhoto,
  editDamageItem, uploadDamagePhoto, retryPhoto,
  submitDamageReview, withdrawDamageReview, removeDamagePhoto,
  initialize, act, send, retryPending, moreOrders, retryOrders,
} = useReception();
const timeline = computed(() => [
  ...messages.value.map(message => ({
    key: message.message_id, time: message.created_at, message,
  })),
  ...applicationNotices.value.map(notice => ({
    key: `application-${notice.review_id}`, time: notice.created_at, notice,
  })),
].sort((a, b) => a.time.localeCompare(b.time) || a.key.localeCompare(b.key)));
const addressOrder = ref<string | null>(null);
let lastOpenedReply: string | undefined;
const boundOrderId = computed(() => view.value?.bound_order?.order_id);
const latestAddressReply = computed(() => messages.value
  .find(message => message.message_id === view.value?.latest_message?.message_id)
  ?.replies.find(reply => Boolean(reply.address_form_order_id)));

watch(boundOrderId, () => { addressOrder.value = null; });
watch([latestAddressReply, boundOrderId], ([reply, orderId]) => {
  if (reply && orderId && reply.address_form_order_id === orderId
      && reply.reply_id !== lastOpenedReply) {
    lastOpenedReply = reply.reply_id;
    addressOrder.value = orderId;
  }
});
onMounted(initialize);
</script>

<template>
  <main class="reception" aria-labelledby="page-title" :aria-busy="busy">
    <header class="header">
      <span class="eyebrow">NEXUSGUARD</span>
      <h1 id="page-title">店铺客服</h1>
      <p>选定订单，或直接说说你想咨询的问题。</p>
    </header>
    <p class="notice" :class="{ error: hasError }" role="status" aria-live="polite">{{ notice }}</p>
    <section class="context" aria-label="当前咨询">
      <p>{{ view?.prompt ?? '登录后可查询和选择你的订单。' }}</p>
      <OrderCard
        v-if="activeOrder" :order="activeOrder"
        :title="view?.bound_order ? '正在咨询这笔订单' : '是否咨询这笔订单？'"
      />
      <SuggestedQuestions
        :questions="view?.suggestions ?? []" :disabled="disabled" @select="send"
      />
      <div v-if="view" class="actions" aria-label="咨询选项">
        <button
          v-for="item in view.buttons" :key="item.action" type="button" :disabled="disabled"
          @click="act(item.action)"
        >{{ item.label }}</button>
      </div>
    </section>
    <ShippingAddressForm v-if="view && addressOrder && addressOrder === boundOrderId"
      :key="addressOrder" :order-id="addressOrder" :session-id="view.session_id" :disabled="disabled"
      @close="addressOrder = null" />
    <OrderPicker
      v-if="selecting" :orders="orders" :disabled="disabled"
      :has-more="nextOffset !== null" :failed="ordersFailed"
      @select="act('select_order', $event)" @load-more="moreOrders" @retry="retryOrders"
    />
    <DamageDraftPanel v-if="view?.damage_draft" :damage="view.damage_draft" :disabled="disabled"
      @select="editDamageItem($event, 'select')" @remove="editDamageItem($event, 'remove')"
      @upload="uploadDamagePhoto"
      @submit="submitDamageReview" @withdraw="withdrawDamageReview" @remove-photo="removeDamagePhoto"
      @resolve="editDamageItem($event.itemId, 'select', $event.pendingId)"
      @discard="editDamageItem(undefined, 'discard_pending', $event)" />
    <div v-if="pendingPhoto && !busy" class="context" role="status">
      <p>照片接收结果尚未确认，请先重试，再进行其他操作。</p>
      <button type="button" @click="retryPhoto">重试上传照片</button>
    </div>
    <button v-if="syncFailed" type="button" @click="synchronize">重新同步</button>
    <section aria-label="咨询记录">
      <button v-if="nextBefore" type="button" :disabled="busy" @click="olderMessages">加载更早消息</button>
      <ol class="messages" aria-live="polite">
        <li v-for="entry in timeline" :key="entry.key" class="turn">
          <template v-if="'message' in entry">
            <div class="user-message"><span class="message-author">你</span>{{ entry.message.text }}</div>
            <template v-for="reply in entry.message.replies" :key="reply.reply_id">
              <div class="assistant-message"><span class="message-author">客服</span>{{ reply.text }}</div>
              <ProductCards :products="reply.product_cards" />
              <button v-if="reply.address_form_order_id && reply.address_form_order_id === boundOrderId"
                type="button" :disabled="disabled" @click="addressOrder = reply.address_form_order_id">
                打开改址表单
              </button>
            </template>
            <p class="message-status">{{ messageStatus(entry.message.status) }}</p>
          </template>
          <div v-else class="assistant-message">
            <span class="message-author">客服 · 人工处理结果</span>{{ entry.notice.text }}
          </div>
        </li>
      </ol>
    </section>
    <div v-if="pending && !busy" class="context" role="status">
      <p>这条问题的发送结果尚未确认，请先重试，再切换订单或发送其他问题。</p>
      <p class="pending-text">{{ pending.request.text }}</p>
      <button type="button" @click="retryPending">重试发送</button>
    </div>
    <MessageComposer v-model="draft" :disabled="disabled" :status="deliveryStatus" @send="send" />
  </main>
</template>
