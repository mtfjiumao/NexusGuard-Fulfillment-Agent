import { requestJson } from './reception';
import type { UUID } from './types';

export interface ShippingAddress {
  recipient: string;
  phone: string;
  region: string;
  street: string;
}

export interface AddressFormData {
  order_number: string;
  version: number;
  address: Partial<ShippingAddress>;
}

const path = (sessionId: UUID, orderId: UUID) =>
  `/api/v1/logistics/sessions/${encodeURIComponent(sessionId)}`
  + `/orders/${encodeURIComponent(orderId)}/shipping-address`;

export const addressApi = {
  load: (sessionId: UUID, orderId: UUID) =>
    requestJson<AddressFormData>(path(sessionId, orderId)),
  save: (sessionId: UUID, orderId: UUID, version: number, address: ShippingAddress) =>
    requestJson<{ status: 'saved' }>(path(sessionId, orderId), {
      expected_version: version, address,
    }, 'PUT'),
};
