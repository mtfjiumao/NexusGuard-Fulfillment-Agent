import { requestJson } from './reception';
import type {
  ApplicationOutcome, ApplicationStatus, DamageApplicationDecision, DamageReviewSummary,
  ReviewDecision, ReviewStatus, StaffDamageReview, UUID,
} from './types';

const prefix = '/api/v1/aftersales/damage-reviews';
const detail = (id: UUID) => `${prefix}/${encodeURIComponent(id)}`;
const item = (id: UUID, itemId: UUID) => `${detail(id)}/items/${encodeURIComponent(itemId)}`;

export const damageReviewApi = {
  queue: (status: ApplicationStatus, offset = 0) =>
    requestJson<{ reviews: DamageReviewSummary[]; next_offset: number | null }>(
      `${prefix}?status=${status}&offset=${offset}`,
    ),
  detail: (id: UUID) => requestJson<StaffDamageReview>(detail(id)),
  decide: (id: UUID, itemId: UUID, decision: ReviewDecision, reason: string) =>
    requestJson<StaffDamageReview>(`${item(id, itemId)}/decision`, { decision, reason }),
  resolve: (id: UUID, decision: DamageApplicationDecision) =>
    requestJson<StaffDamageReview>(`${detail(id)}/resolution`, decision),
  photo: (id: UUID, itemId: UUID, photoId: UUID) =>
    `${item(id, itemId)}/photos/${encodeURIComponent(photoId)}`,
};

export const reviewLabels: Record<ReviewStatus, string> = {
  pending: '待核验', passed: '已通过', needs_supplement: '需补充',
  rejected: '未通过', withdrawn: '已撤回',
};

export const outcomeLabels: Record<ApplicationOutcome, string> = {
  approved: '同意申请', needs_supplement: '需要补充', rejected: '未同意申请',
};

export const applicationStatusLabels: Record<ApplicationStatus, string> = {
  pending: '待核验材料', awaiting_resolution: '待人工处理',
  resolved: '已处理', withdrawn: '已撤回', legacy: '历史材料核验',
};

export function applicationStatus(review: DamageReviewSummary): ApplicationStatus {
  if (review.status === 'withdrawn' || review.status === 'pending') return review.status;
  if (review.result) return 'resolved';
  return review.requested_resolution ? 'awaiting_resolution' : 'legacy';
}

export function formatAmount(cents: number, currency: string) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(cents / 100);
}
