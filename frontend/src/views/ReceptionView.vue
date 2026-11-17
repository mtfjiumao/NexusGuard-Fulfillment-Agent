<script setup lang="ts">
import { onMounted } from 'vue';
import DamageDraftPanel from '../components/DamageDraftPanel.vue';
import MessageComposer from '../components/MessageComposer.vue';
import OrderCard from '../components/OrderCard.vue';
import OrderPicker from '../components/OrderPicker.vue';
import SuggestedQuestions from '../components/SuggestedQuestions.vue';
import { useReception } from '../composables/useReception';

const {
  view, busy, notice, hasError, draft, orders, nextOffset, ordersFailed, messages,
  pending, selecting, activeOrder, disabled, deliveryStatus, nextBefore, syncFailed,
  messageStatus, olderMessages, synchronize, pendingPhoto,
  editDamageItem, uploadDamagePhoto, retryPhoto,
  initialize, act, send, retryPending, moreOrders, retryOrders,
} = useReception();
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
    <OrderPicker
      v-if="selecting" :orders="orders" :disabled="disabled"
      :has-more="nextOffset !== null" :failed="ordersFailed"
      @select="act('select_order', $event)" @load-more="moreOrders" @retry="retryOrders"
    />
    <DamageDraftPanel v-if="view?.damage_draft" :damage="view.damage_draft" :disabled="disabled"
      @select="editDamageItem($event, 'select')" @remove="editDamageItem($event, 'remove')"
      @upload="uploadDamagePhoto"
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
        <li v-for="message in messages" :key="message.message_id" class="turn">
          <div class="user-message"><span class="message-author">你</span>{{ message.text }}</div>
          <div v-for="reply in message.replies" :key="reply.reply_id" class="assistant-message">
            <span class="message-author">客服</span>{{ reply.text }}
          </div>
          <p class="message-status">{{ messageStatus(message.status) }}</p>
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
