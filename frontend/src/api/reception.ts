import type {
  ActionRequest, CreateReception, MessageRequest, MessagePage, OrderPage, ReceptionView, UUID,
} from './types';

const prefix = '/api/v1/supervisor/reception';

export class ApiError extends Error {
  constructor(readonly status: number, readonly code?: string) {
    super('reception_request_failed');
  }
}

export async function requestJson<T>(
  path: string, body?: object | File, method = body === undefined ? 'GET' : 'POST',
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(path, {
      method,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: body === undefined ? {} : {
        'Content-Type': body instanceof File ? body.type : 'application/json',
      },
      body: body === undefined ? undefined : body instanceof File ? body : JSON.stringify(body),
    });
    // Do not display arbitrary upstream diagnostics to the customer.
    if (!response.ok) {
      const detail = response.status === 409 ? await response.json().catch(() => null) : null;
      throw new ApiError(response.status, typeof detail?.detail === 'string' ? detail.detail : undefined);
    }
    return await response.json() as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

const request = <T>(path: string, body?: object | File, method?: string) =>
  requestJson<T>(`${prefix}${path}`, body, method);
const sessionPath = (id: UUID) => `/sessions/${encodeURIComponent(id)}`;
const damagePath = (id: UUID, draftId: UUID) =>
  `${sessionPath(id)}/damage/${encodeURIComponent(draftId)}`;
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
  submitReview: (id: UUID, draftId: UUID, submissionId: UUID, version: number, resolution: string) =>
    request<ReceptionView>(`${damagePath(id, draftId)}/reviews`, {
      submission_id: submissionId, expected_version: version,
      requested_resolution: resolution,
    }),
  withdrawReview: (id: UUID, draftId: UUID, reviewId: UUID, version: number) =>
    request<ReceptionView>(
      `${damagePath(id, draftId)}/reviews/${encodeURIComponent(reviewId)}/withdraw`,
      { expected_version: version },
    ),
  removePhoto: (id: UUID, draftId: UUID, photoId: UUID, version: number) =>
    request<ReceptionView>(
      `${damagePath(id, draftId)}/photos/${encodeURIComponent(photoId)}?expected_version=${version}`,
      undefined, 'DELETE',
    ),
};
