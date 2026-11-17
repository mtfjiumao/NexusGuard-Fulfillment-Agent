import { computed, onScopeDispose, ref } from 'vue';
import { ApiError, receptionApi } from '../api/reception';
import type {
  ActionRequest, ConversationMessage, CreateReception, MessageRequest, MessageStatus, OrderCard, ReceptionAction,
  ReceptionView, UUID,
} from '../api/types';

interface PendingMessage { sessionId: UUID; request: MessageRequest }
interface PendingPhoto {
  sessionId: UUID; draftId: UUID; claimId: UUID; uploadId: UUID; file: File; version: number;
}

/** Local UI state only. The backend owns reception transitions and authorization. */
export function useReception() {
  const view = ref<ReceptionView | null>(null);
  const busy = ref(false);
  const notice = ref('正在读取咨询信息…');
  const hasError = ref(false);
  const draft = ref('');
  const orders = ref<OrderCard[]>([]);
  const nextOffset = ref<number | null>(null);
  const ordersFailed = ref(false);
  const messages = ref<ConversationMessage[]>([]);
  const pending = ref<PendingMessage | null>(null);
  const nextBefore = ref<UUID | null>(null);
  const pendingPhoto = ref<PendingPhoto | null>(null);
  const syncing = ref(false);
  const syncFailed = ref(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let failedOffset = 0;
  let initialized = false;

  const selecting = computed(() => ['selecting', 'clarifying'].includes(view.value?.state ?? ''));
  const activeOrder = computed(() => view.value?.bound_order ?? view.value?.candidate_order ?? null);
  const agentBusy = computed(() => ['queued', 'processing', 'ready_for_agent']
    .includes(view.value?.latest_message?.status ?? ''));
  const disabled = computed(() => busy.value || syncing.value || syncFailed.value
    || agentBusy.value || view.value === null || pending.value !== null || pendingPhoto.value !== null);
  function messageStatus(status: MessageStatus) {
    return {
      queued: '已收到，等待处理', processing: '正在理解问题并查询信息…',
      needs_order: '请先选择订单，选定后继续处理', ready_for_agent: '已选定订单，等待继续处理',
      completed: '已回复', failed: '处理未完成，可重新发送问题', superseded: '此问题已不再继续处理',
    }[status];
  }
  const deliveryStatus = computed(() => {
    const status = view.value?.latest_message?.status;
    return status ? messageStatus(status) : '';
  });
  onScopeDispose(() => {
    disposed = true;
    if (timer) clearTimeout(timer);
  });

  function announce(text: string, error = false) {
    notice.value = text;
    hasError.value = error;
  }

  function clearPrivateState() {
    view.value = null;
    orders.value = [];
    messages.value = [];
    nextOffset.value = null;
    pending.value = null;
    pendingPhoto.value = null;
    nextBefore.value = null;
    syncFailed.value = false;
  }

  async function loadOrders(offset: number) {
    if (!view.value) return;
    try {
      const page = await receptionApi.orders(view.value.session_id, offset);
      const existing = offset === 0 ? [] : orders.value;
      const ids = new Set(existing.map((order) => order.order_id));
      orders.value = [...existing, ...page.orders.filter((order) => !ids.has(order.order_id))];
      nextOffset.value = page.next_offset;
      ordersFailed.value = false;
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status)) throw error;
      ordersFailed.value = true;
      failedOffset = offset;
    }
  }

  async function applyView(data: ReceptionView) {
    view.value = data;
    orders.value = [];
    nextOffset.value = null;
    ordersFailed.value = false;
    if (selecting.value) await loadOrders(0);
  }

  function mergeMessages(incoming: ConversationMessage[]) {
    const rows = new Map(messages.value.map((row) => [row.message_id, row]));
    for (const row of incoming) rows.set(row.message_id, row);
    messages.value = [...rows.values()].sort((a, b) =>
      a.created_at.localeCompare(b.created_at) || a.message_id.localeCompare(b.message_id));
  }

  async function loadHistory() {
    if (!view.value) return;
    const page = await receptionApi.history(view.value.session_id);
    if (disposed) return;
    const hadMessages = messages.value.length > 0;
    mergeMessages(page.messages);
    if (!hadMessages) nextBefore.value = page.next_before;
  }

  async function synchronize() {
    if (disposed || busy.value || syncing.value || !view.value) return;
    syncing.value = true;
    try {
      const [data, page] = await Promise.all([
        receptionApi.get(view.value.session_id), receptionApi.history(view.value.session_id),
      ]);
      if (disposed) return;
      const recovered = syncFailed.value;
      const changed = data.version !== view.value?.version;
      if (changed) await applyView(data);
      else view.value = data;
      if (messages.value.length === 0) nextBefore.value = page.next_before;
      mergeMessages(page.messages);
      syncFailed.value = false;
      if (recovered) announce('咨询记录已同步，可以继续操作。');
    } catch (error) {
      if (disposed) return;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        clearPrivateState();
        announce('当前会话已不可用，请重新登录或打开客服。', true);
      } else {
        syncFailed.value = true;
        announce('暂时无法同步处理进度，正在重试；请先保留输入内容。', true);
      }
    } finally { syncing.value = false; }
  }

  function scheduleSync() {
    if (disposed || !view.value) return;
    timer = setTimeout(async () => {
      await synchronize();
      scheduleSync();
    }, 2500);
  }

  async function olderMessages() {
    if (busy.value || syncing.value || !view.value || !nextBefore.value) return;
    await perform(async () => {
      const page = await receptionApi.history(view.value!.session_id, nextBefore.value!);
      mergeMessages(page.messages);
      nextBefore.value = page.next_before;
    });
  }

  async function handleError(error: unknown) {
    if (error instanceof ApiError) {
      if ([401, 403].includes(error.status)) {
        clearPrivateState();
        announce(error.status === 401 ? '请先登录店铺账号，再重新打开客服。'
          : '当前账号暂时无法使用客服，请联系店铺。', true);
        return;
      }
      if (error.status === 409 && view.value) {
        pending.value = null;
        pendingPhoto.value = null;
        try {
          await applyView(await receptionApi.get(view.value.session_id));
          announce('咨询信息已更新，请确认当前订单后重新操作。输入的文字已保留。', true);
        } catch (refreshError) {
          if (refreshError instanceof ApiError && [401, 403].includes(refreshError.status)) {
            await handleError(refreshError);
          } else {
            // Disable writes until a fresh version is obtained.
            view.value = null;
            orders.value = [];
            announce('暂时无法同步咨询信息，请重新打开客服。', true);
          }
        }
        return;
      }
      if (error.status >= 400 && error.status < 500) {
        pending.value = null;
        pendingPhoto.value = null;
      }
      if ([413, 415].includes(error.status)) {
        announce('请使用不超过 5 MB 的 JPEG/PNG 照片；每份草稿最多 10 张、合计 20 MB。', true);
        return;
      }
      if (error.status === 404) {
        announce('未找到这笔订单或会话，请重新打开客服或选择其他订单。', true);
        return;
      }
      if (error.status === 422) {
        announce('入口或提交内容不完整，请从店铺或订单页重新进入。', true);
        return;
      }
    }
    announce(pendingPhoto.value ? '照片接收结果尚未确认，请重试上传；系统会防止重复保存。'
      : pending.value ? '发送结果尚未确认，请点击重试；系统将复用同一条消息。'
      : '暂时无法完成请求，请稍后重试。输入的文字已保留。', true);
  }

  async function perform(operation: () => Promise<void>) {
    if (busy.value || syncing.value || disposed) return;
    busy.value = true;
    try { await operation(); }
    catch (error) { await handleError(error); }
    finally { busy.value = false; }
  }

  function entryContext(): CreateReception {
    const params = new URLSearchParams(window.location.search);
    const entry = params.get('entry_point') ?? 'store';
    if (!['order', 'store', 'other'].includes(entry)) throw new ApiError(422);
    const context: CreateReception = { entry_point: entry as CreateReception['entry_point'] };
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    for (const name of ['order_id', 'sku_id'] as const) {
      const value = params.get(name);
      if (value !== null) {
        if (!uuid.test(value)) throw new ApiError(422);
        context[name] = value;
      }
    }
    if ((entry === 'order') !== (context.order_id !== undefined)) throw new ApiError(422);
    return context;
  }

  async function initialize() {
    if (initialized) return;
    initialized = true;
    await perform(async () => {
      const params = new URLSearchParams(window.location.search);
      const sessionId = params.get('session_id');
      const data = sessionId ? await receptionApi.get(sessionId)
        : await receptionApi.create(entryContext());
      await applyView(data);
      const url = new URL(window.location.href);
      url.searchParams.set('session_id', data.session_id);
      window.history.replaceState(null, '', url);
      try { await loadHistory(); }
      catch (error) {
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) throw error;
        syncFailed.value = true;
        announce('暂时无法读取咨询记录，正在重试。', true);
        return;
      }
      announce('你可以点击问题按钮，也可以随时直接输入。');
    });
    scheduleSync();
  }

  async function act(action: ReceptionAction, orderId?: UUID) {
    if (disabled.value || !view.value) return;
    const session = view.value;
    if (action === 'select_order' && !orderId) return;
    const request: ActionRequest = action === 'select_order'
      ? { action, order_id: orderId!, expected_version: session.version }
      : { action, expected_version: session.version };
    await perform(async () => {
      const data = await receptionApi.act(session.session_id, request);
      await applyView(data);
      announce(data.state === 'bound' ? '已更新咨询订单，请选择下方的问题或直接输入。' : '请选择咨询内容。');
    });
  }

  async function submitPending() {
    const submission = pending.value;
    if (!submission) return;
    const data = await receptionApi.message(submission.sessionId, submission.request);
    // The API acknowledged the message, so later list-loading failures cannot resend it.
    pending.value = null;
    if (draft.value.trim() === submission.request.text) draft.value = '';
    await applyView(data);
    try { await loadHistory(); }
    catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status)) throw error;
      syncFailed.value = true;
      announce('问题已收到，暂时无法同步记录；正在重试，请勿重复发送。', true);
      return;
    }
    announce(data.latest_message?.status === 'needs_order'
      ? '请在上方选择你要咨询的订单。' : '问题已收到。');
  }

  async function send(text: string) {
    if (disabled.value || !view.value || !text.trim()) return;
    if (text.trim().length > 4000) {
      announce('问题请控制在 4000 字以内。', true);
      return;
    }
    if (!window.crypto?.randomUUID) {
      announce('请使用 HTTPS 或本机 localhost 地址打开客服页面。', true);
      return;
    }
    pending.value = { sessionId: view.value.session_id, request: {
      message_id: crypto.randomUUID(), text: text.trim(), expected_version: view.value.version,
    } };
    await perform(submitPending);
  }

  async function retryPending() {
    if (busy.value || !pending.value) return;
    await perform(submitPending);
  }

  async function editDamageItem(orderItemId: UUID | undefined, action: 'select' | 'remove' | 'discard_pending', pendingClaimId?: UUID) {
    if (disabled.value || !view.value?.damage_draft) return;
    const session = view.value;
    await perform(async () => {
      const data = await receptionApi.damageItem(session.session_id, session.damage_draft!.draft_id,
        orderItemId, action, session.version, pendingClaimId);
      await applyView(data);
      announce(pendingClaimId ? '待关联信息已处理，已更新材料清单。'
        : action === 'select' ? '已选择商品，请在下方补充破损总数量和具体情况。'
          : '已从本次申报移除该商品，其余商品信息已保留。');
    });
  }

  async function submitPhoto() {
    const submission = pendingPhoto.value;
    if (!submission) return;
    const data = await receptionApi.damagePhoto(submission.sessionId, submission.draftId,
      submission.claimId, submission.uploadId, submission.file, submission.version);
    pendingPhoto.value = null;
    await applyView(data);
    announce('照片已收到，尚未审核；已更新下方的材料清单。');
  }

  async function uploadDamagePhoto(payload: { claimId: UUID; file: File }) {
    if (disabled.value || !view.value?.damage_draft) return;
    if (!['image/jpeg', 'image/png'].includes(payload.file.type)
        || payload.file.size === 0 || payload.file.size > 5 * 1024 * 1024) {
      announce('请选择不超过 5 MB 的 JPEG 或 PNG 照片。', true);
      return;
    }
    if (!window.crypto?.randomUUID) {
      announce('请使用 HTTPS 或本机 localhost 地址上传照片。', true);
      return;
    }
    pendingPhoto.value = {
      sessionId: view.value.session_id, draftId: view.value.damage_draft.draft_id,
      claimId: payload.claimId, uploadId: crypto.randomUUID(), file: payload.file,
      version: view.value.version,
    };
    await perform(submitPhoto);
  }

  async function retryPhoto() {
    if (busy.value || !pendingPhoto.value) return;
    await perform(submitPhoto);
  }

  async function moreOrders() {
    if (disabled.value || nextOffset.value === null) return;
    const offset = nextOffset.value;
    await perform(() => loadOrders(offset));
  }

  async function retryOrders() {
    if (disabled.value) return;
    await perform(() => loadOrders(failedOffset));
  }

  return {
    view, busy, notice, hasError, draft, orders, nextOffset, ordersFailed, messages,
    pending, selecting, activeOrder, disabled, deliveryStatus, nextBefore, syncFailed,
    messageStatus, olderMessages, synchronize, pendingPhoto,
    editDamageItem, uploadDamagePhoto, retryPhoto,
    initialize, act, send, retryPending, moreOrders, retryOrders,
  };
}
