import type {
  ActionRequest, CreateReception, MessageRequest, MessagePage, OrderPage, ReceptionView, UUID,
} from './types';

const prefix = '/api/v1/supervisor/reception';

export class ApiError extends Error {
  constructor(readonly status: number) {
    super('reception_request_failed');
  }
}

async function request<T>(path: string, body?: object | File): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(`${prefix}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: body === undefined ? {} : {
        'Content-Type': body instanceof File ? body.type : 'application/json',
      },
      body: body === undefined ? undefined : body instanceof File ? body : JSON.stringify(body),
    });
    // Do not display arbitrary upstream diagnostics to the customer.
    if (!response.ok) throw new ApiError(response.status);
    return await response.json() as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

const sessionPath = (id: UUID) => `/sessions/${encodeURIComponent(id)}`;
export const receptionApi = {
  create: (data: CreateReception) => request<ReceptionView>('/sessions', data),
  get: (id: UUID) => request<ReceptionView>(sessionPath(id)),
  act: (id: UUID, data: ActionRequest) =>
    request<ReceptionView>(`${sessionPath(id)}/actions`, data),
  message: (id: UUID, data: MessageRequest) =>
    request<ReceptionView>(`${sessionPath(id)}/messages`, data),
  history: (id: UUID, before?: UUID) => request<MessagePage>(
    `${sessionPath(id)}/messages?limit=50${before ? `&before=${encodeURIComponent(before)}` : ''}`,
  ),
  damageItem: (id: UUID, draftId: UUID, orderItemId: UUID | undefined, action: 'select' | 'remove' | 'discard_pending', version: number, pendingClaimId?: UUID) =>
    request<ReceptionView>(`${sessionPath(id)}/damage/${encodeURIComponent(draftId)}/items`,
      { action, order_item_id: orderItemId, expected_version: version, pending_claim_id: pendingClaimId }),
  damagePhoto: (id: UUID, draftId: UUID, claimId: UUID, uploadId: UUID, file: File, version: number) =>
    request<ReceptionView>(`${sessionPath(id)}/damage/${encodeURIComponent(draftId)}`
      + `/items/${encodeURIComponent(claimId)}/photos/${encodeURIComponent(uploadId)}`
      + `?expected_version=${version}`, file),
  orders: (id: UUID, offset: number) =>
    request<OrderPage>(`${sessionPath(id)}/orders?limit=20&offset=${offset}`),
};
