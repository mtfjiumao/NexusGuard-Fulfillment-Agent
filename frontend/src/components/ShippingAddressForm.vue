<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { addressApi } from '../api/address';
import { ApiError } from '../api/reception';
import type { AddressFormData, ShippingAddress } from '../api/address';
import type { UUID } from '../api/types';

const props = defineProps<{ sessionId: UUID; orderId: UUID; disabled: boolean }>();
const emit = defineEmits<{ close: [] }>();
const form = ref<Pick<AddressFormData, 'order_number' | 'version'> | null>(null);
const address = ref<ShippingAddress>({ recipient: '', phone: '', region: '', street: '' });
const busy = ref(false);
const saved = ref(false);
const refreshRequired = ref(false);
const blocked = ref(false);
const notice = ref('');

const conflicts: Record<string, string> = {
  warehouse_processing: '订单已进入出库，无法自助修改收货地址。',
  order_already_shipped: '订单已发货，无法自助修改收货地址。',
  order_closed: '订单已关闭或取消，无法自助修改收货地址。',
  order_binding_changed: '咨询的订单已变更，请重新选择订单。',
  order_version_conflict: '订单信息已变化，请重新读取并核对地址。',
};

function showError(error: unknown, saving: boolean) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 409 && error instanceof ApiError && error.code && Object.hasOwn(conflicts, error.code)) {
    notice.value = conflicts[error.code] ?? '';
    refreshRequired.value = error.code === 'order_version_conflict';
    blocked.value = !refreshRequired.value;
  } else if (status === 401 || status === 403 || status === 404) {
    notice.value = '当前订单不可用，请重新登录或选择订单。';
    blocked.value = true;
    address.value = { recipient: '', phone: '', region: '', street: '' };
  } else if (status === 422) {
    notice.value = '请检查姓名、电话号码和地址格式。';
  } else {
    notice.value = saving ? '暂时无法确认保存结果，可再次提交同一地址。' : '暂时无法读取地址，请重试。';
  }
}

async function load() {
  if (busy.value) return;
  busy.value = true;
  notice.value = '';
  try {
    const data = await addressApi.load(props.sessionId, props.orderId);
    form.value = { order_number: data.order_number, version: data.version };
    address.value = { recipient: '', phone: '', region: '', street: '', ...data.address };
    refreshRequired.value = false;
  } catch (error) {
    showError(error, false);
  } finally {
    busy.value = false;
  }
}

async function save() {
  if (!form.value || busy.value || props.disabled || blocked.value || refreshRequired.value || saved.value) return;
  busy.value = true;
  notice.value = '';
  try {
    await addressApi.save(props.sessionId, props.orderId, form.value.version, address.value);
    saved.value = true;
    address.value = { recipient: '', phone: '', region: '', street: '' };
    notice.value = `订单 ${form.value.order_number} 的收货地址已保存。`;
  } catch (error) {
    showError(error, true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="context" aria-label="收货地址表单" :aria-busy="busy">
    <h2>修改收货地址 <span v-if="form">· {{ form.order_number }}</span></h2>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-else-if="!form && busy" role="status">正在读取订单地址…</p>
    <form v-if="form && !saved && !blocked" class="review-form address-form" @submit.prevent="save">
      <fieldset :disabled="disabled || busy || refreshRequired">
        <label>收货人 <input v-model.trim="address.recipient" maxlength="60" autocomplete="shipping name" required /></label>
        <label>联系电话 <input v-model.trim="address.phone" type="tel" maxlength="16" pattern="\+?[0-9]{7,15}" autocomplete="shipping tel" required /></label>
        <label>省市区 <input v-model.trim="address.region" maxlength="100" autocomplete="shipping address-level1" required /></label>
        <label>详细地址 <input v-model.trim="address.street" maxlength="250" autocomplete="shipping address-line1" required /></label>
        <button class="send" type="submit">确认修改地址</button>
      </fieldset>
    </form>
    <div class="actions">
      <button v-if="!saved && !blocked && (!form || refreshRequired)" type="button" :disabled="busy" @click="load">
        {{ refreshRequired ? '重新读取地址' : '重试读取' }}
      </button>
      <button type="button" :disabled="busy" @click="emit('close')">关闭</button>
    </div>
  </section>
</template>
