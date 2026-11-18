<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  applicationStatus, applicationStatusLabels, damageReviewApi, formatAmount,
  outcomeLabels, reviewLabels,
} from '../api/damageReview';
import { ApiError } from '../api/reception';
import type {
  ApplicationOutcome, ApplicationStatus, DamageReviewSummary, ReviewDecision, StaffDamageReview, UUID,
} from '../api/types';

const status = ref<ApplicationStatus>('pending');
const rows = ref<DamageReviewSummary[]>([]);
const selected = ref<StaffDamageReview | null>(null);
const nextOffset = ref<number | null>(null);
const busy = ref(false);
const notice = ref('');
const failed = ref(false);
const dateTime = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
const outcome = ref<ApplicationOutcome | ''>('');
const canResolve = computed(() =>
  selected.value !== null && applicationStatus(selected.value) === 'awaiting_resolution');

async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  notice.value = '';
  failed.value = false;
  try {
    await action();
  } catch (error) {
    failed.value = true;
    const code = error instanceof ApiError ? error.status : 0;
    if (code === 401 || code === 403) {
      selected.value = null;
      rows.value = [];
      nextOffset.value = null;
      notice.value = '请使用有破损材料核验权限的账号登录。';
    } else {
      notice.value = code === 409
        ? '审核状态已变化，请刷新当前记录。'
        : '暂时无法完成请求，请重试。';
    }
  } finally {
    busy.value = false;
  }
}

async function readQueue(offset = 0) {
  const page = await damageReviewApi.queue(status.value, offset);
  const existing = offset ? rows.value : [];
  const ids = new Set(existing.map(row => row.review_id));
  rows.value = [...existing, ...page.reviews.filter(row => !ids.has(row.review_id))];
  nextOffset.value = page.next_offset;
}

function refreshQueue(offset = 0) {
  return perform(async () => {
    if (!offset) {
      rows.value = [];
      nextOffset.value = null;
    }
    await readQueue(offset);
  });
}

function open(id: UUID) {
  return perform(async () => {
    const review = await damageReviewApi.detail(id);
    if (selected.value?.review_id !== id) outcome.value = '';
    selected.value = review;
  });
}

async function decide(event: SubmitEvent, itemId: UUID) {
  const review = selected.value;
  if (!review) return;
  const fields = new FormData(event.currentTarget as HTMLFormElement);
  const decision = fields.get('decision') as ReviewDecision;
  const reason = String(fields.get('reason') ?? '').trim();
  if (!reason) {
    failed.value = true;
    notice.value = '请输入审核原因。';
    return;
  }
  await perform(async () => {
    selected.value = await damageReviewApi.decide(review.review_id, itemId, decision, reason);
    if (applicationStatus(selected.value) !== status.value) {
      rows.value = rows.value.filter(row => row.review_id !== review.review_id);
      nextOffset.value = null;
      await readQueue();
    }
    notice.value = canResolve.value ? '材料已核验，请填写整份申请的处理结论。' : '商品核验已保存。';
  });
}

async function resolve(event: SubmitEvent) {
  const review = selected.value;
  if (!review || !outcome.value || !canResolve.value) return;
  const fields = new FormData(event.currentTarget as HTMLFormElement);
  const reason = String(fields.get('reason') ?? '').trim();
  const resolution = String(fields.get('resolution') ?? '').trim();
  const amount = String(fields.get('refund') ?? '').trim();
  const [whole = '', fraction = ''] = amount.split('.');
  const cents = amount ? Number(whole) * 100 + Number(fraction.padEnd(2, '0')) : null;
  if (!reason || !resolution) {
    failed.value = true;
    notice.value = '请填写处理原因和处理说明。';
    return;
  }
  if (cents !== null && (
    !/^\d+(\.\d{1,2})?$/.test(amount) || !Number.isSafeInteger(cents)
      || cents <= 0 || cents > review.order_paid_cents
  )) {
    failed.value = true;
    notice.value = '请输入大于零且不超过订单实付金额的数字，最多两位小数。';
    return;
  }
  const decision = { outcome: outcome.value, reason, resolution, refund_cents: cents };
  await perform(async () => {
    selected.value = await damageReviewApi.resolve(review.review_id, decision);
    rows.value = rows.value.filter(row => row.review_id !== review.review_id);
    await readQueue();
    notice.value = '处理结论已保存，将通过客服会话通知顾客。';
  });
}

onMounted(() => refreshQueue());
</script>

<template>
  <main class="review-workspace" aria-labelledby="review-title" :aria-busy="busy">
    <header class="review-header">
      <h1 id="review-title">破损售后申请</h1>
      <p class="notice" :class="{ error: failed }" role="status">{{ notice }}</p>
    </header>
    <div class="review-layout">
      <aside aria-label="核验队列" class="review-queue">
        <div class="review-toolbar">
          <label>
            状态
            <select v-model="status" :disabled="busy" @change="refreshQueue()">
              <option v-for="(label, key) in applicationStatusLabels" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
          <button type="button" :disabled="busy" @click="refreshQueue()">刷新队列</button>
        </div>
        <ol v-if="rows.length" class="review-list">
          <li v-for="row in rows" :key="row.review_id">
            <button type="button" :disabled="busy"
              :aria-current="selected?.review_id === row.review_id ? 'true' : undefined"
              @click="open(row.review_id)">
              <strong>{{ row.order_number }}</strong>
              <span>第 {{ row.revision }} 版 · {{ dateTime.format(new Date(row.created_at)) }}</span>
            </button>
          </li>
        </ol>
        <p v-else-if="!busy && !failed" class="order-meta">此状态下暂无记录。</p>
        <button v-if="nextOffset !== null" type="button" :disabled="busy"
          @click="refreshQueue(nextOffset!)">加载更多</button>
      </aside>
      <section v-if="selected" aria-label="提交详情" class="review-detail">
        <header class="review-toolbar">
          <div>
            <h2>{{ selected.order_number }}</h2>
            <p class="order-meta">第 {{ selected.revision }} 版 · {{ applicationStatusLabels[applicationStatus(selected)] }}</p>
          </div>
          <button type="button" :disabled="busy" @click="open(selected.review_id)">刷新记录</button>
        </header>
        <p v-if="selected.requested_resolution">顾客诉求：{{ selected.requested_resolution }}</p>
        <p class="order-meta">订单实付 {{ formatAmount(selected.order_paid_cents, selected.order_currency) }}</p>
        <article v-for="item in selected.items" :key="item.item_id" class="review-item">
          <h3>{{ item.title }}</h3>
          <p class="order-meta">购买 {{ item.purchased_quantity }} · 申报破损 {{ item.damaged_quantity }}</p>
          <p class="review-description">{{ item.description }}</p>
          <ul class="review-photos" aria-label="提交的破损照片">
            <li v-for="(photo, index) in item.photos" :key="photo.attachment_id">
              <a :href="damageReviewApi.photo(selected.review_id, item.item_id, photo.attachment_id)"
                target="_blank" rel="noopener noreferrer" :aria-label="`在新标签查看照片 ${index + 1}`">
                <img :src="damageReviewApi.photo(selected.review_id, item.item_id, photo.attachment_id)"
                  :alt="`破损照片 ${index + 1}`" loading="lazy" />
              </a>
            </li>
          </ul>
          <template v-if="item.decision">
            <p><strong>{{ reviewLabels[item.decision] }}</strong> · {{ item.reason }}</p>
            <p v-if="item.reviewed_at" class="order-meta">
              {{ item.reviewer_id }} · {{ dateTime.format(new Date(item.reviewed_at)) }}
            </p>
          </template>
          <form v-else-if="selected.status === 'pending'" class="review-form"
            @submit.prevent="decide($event, item.item_id)">
            <label>
              核验结论
              <select name="decision" :disabled="busy" required>
                <option value="">请选择</option>
                <option value="passed">通过</option>
                <option value="needs_supplement">需补充</option>
                <option value="rejected">不通过</option>
              </select>
            </label>
            <label>
              原因（顾客可见）
              <textarea name="reason" :disabled="busy" maxlength="500" required rows="2" />
            </label>
            <button type="submit" class="send" :disabled="busy">保存结论</button>
          </form>
          <p v-else class="order-meta">审核已终止。</p>
        </article>
        <section v-if="selected.result" class="review-item" aria-label="处理结论">
          <h3>{{ outcomeLabels[selected.result.outcome] }}</h3>
          <p>{{ selected.result.reason }}</p>
          <p>{{ selected.result.resolution }}</p>
          <p v-if="selected.result.refund_cents !== null && selected.result.refund_currency">
            审核确认 {{ formatAmount(selected.result.refund_cents, selected.result.refund_currency) }}
          </p>
          <p v-if="selected.result.refund_status_text">{{ selected.result.refund_status_text }}</p>
          <p class="order-meta">{{ selected.resolver_id }} · {{ dateTime.format(new Date(selected.result.resolved_at)) }}</p>
        </section>
        <form v-else-if="canResolve" :key="selected.review_id" class="review-form review-item" @submit.prevent="resolve">
          <h3>处理这份申请</h3>
          <label>
            处理结论
            <select v-model="outcome" :disabled="busy" required>
              <option value="">请选择</option>
              <option v-for="(label, key) in outcomeLabels" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
          <label>
            原因（顾客可见）
            <textarea name="reason" :disabled="busy" maxlength="500" required rows="2" />
          </label>
          <label>
            处理说明（说明安排或下一步）
            <textarea name="resolution" :disabled="busy" maxlength="500" required rows="2" />
          </label>
          <label v-if="outcome === 'approved'">
            审核确认退款金额（{{ selected.order_currency }}，退款时填写）
            <input name="refund" inputmode="decimal" maxlength="18" :disabled="busy" />
          </label>
          <button type="submit" class="send" :disabled="busy">保存结论并通知顾客</button>
        </form>
      </section>
      <p v-else class="order-meta">请选择一条提交记录。</p>
    </div>
  </main>
</template>
