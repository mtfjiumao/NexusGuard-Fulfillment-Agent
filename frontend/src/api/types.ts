/** Mirrors supervisor/schemas.py. These types do not replace server-side validation. */
export type UUID = string;
export type ReceptionState =
  | 'confirming' | 'alternatives' | 'selecting' | 'general' | 'bound' | 'clarifying';
export type ReceptionAction = 'confirm' | 'deny' | 'other_orders' | 'general' | 'select_order';
export type MessageStatus = 'queued' | 'processing' | 'needs_order' | 'ready_for_agent' | 'completed' | 'failed' | 'superseded';

export interface CreateReception {
  entry_point: 'order' | 'store' | 'other';
  order_id?: UUID;
  sku_id?: UUID;
}

export type ActionRequest = { expected_version: number } & (
  | { action: 'select_order'; order_id: UUID }
  | { action: Exclude<ReceptionAction, 'select_order'>; order_id?: never }
);

export interface MessageRequest {
  message_id: UUID;
  expected_version: number;
  text: string;
}

export interface OrderCard {
  order_id: UUID;
  order_number: string;
  ordered_at: string;
  payment_status: 'unpaid' | 'paid' | 'closed';
  fulfillment_status: 'unshipped' | 'processing' | 'shipped' | 'delivered' | 'cancelled';
  paid_amount_cents: number;
  currency: string;
  has_open_aftersales: boolean;
}

export interface Suggestion {
  key: string;
  text: string;
}

export interface MessageReceipt {
  message_id: UUID;
  status: MessageStatus;
  order_id: UUID | null;
}

export interface ReceptionView {
  session_id: UUID;
  version: number;
  state: ReceptionState;
  prompt: string;
  candidate_order: OrderCard | null;
  bound_order: OrderCard | null;
  buttons: { action: ReceptionAction; label: string }[];
  suggestions: Suggestion[];
  allow_free_text: true;
  latest_message: MessageReceipt | null;
  damage_draft: DamageDraft | null;
}

export interface OrderPage {
  orders: OrderCard[];
  next_offset: number | null;
}


export interface ProductCard {
  sku_id: UUID;
  title: string;
  specification: string | null;
  price_cents: number;
  currency: string;
  url: string;
  image_url: string | null;
}

export interface AssistantReply {
  reply_id: UUID;
  phase: 'clarification' | 'answer' | 'error';
  text: string;
  created_at: string;
  product_cards: ProductCard[];
  address_form_order_id: UUID | null;
  logistics_ticket_order_id: UUID | null;
  delay_application_order_id: UUID | null;
}

export interface ConversationMessage extends MessageReceipt {
  text: string;
  created_at: string;
  replies: AssistantReply[];
}

export interface MessagePage {
  messages: ConversationMessage[];
  next_before: UUID | null;
  application_notices: DamageApplicationNotice[];
  logistics_notices: LogisticsNotice[];
}

export interface LogisticsNotice {
  id: UUID;
  created_at: string;
  text: string;
}


export interface DamageProduct {
  order_item_id: UUID;
  title: string;
  purchased_quantity: number;
  draft_item_id: UUID | null;
  included: boolean;
  damaged_quantity: number | null;
  description: string | null;
  attachments: { attachment_id: UUID; media_type: string; verification_status: 'pending' }[];
}

export interface DamageDraft {
  draft_id: UUID;
  order_id: UUID;
  status: 'collecting' | 'ready_for_review';
  focused_item_id: UUID | null;
  products: DamageProduct[];
  pending_claims: {
    pending_claim_id: UUID; reference: string | null; quantity: number | null; description: string | null;
  }[];
  missing: string[];
  prompt: string;
  review: DamageReview | null;
  can_submit: boolean;
}

export type ReviewDecision = 'passed' | 'needs_supplement' | 'rejected';
export type ReviewStatus = ReviewDecision | 'pending' | 'withdrawn';
export type ApplicationStatus = 'pending' | 'awaiting_resolution' | 'resolved' | 'withdrawn' | 'legacy';
export type ApplicationOutcome = 'approved' | 'needs_supplement' | 'rejected';

export interface DamageApplicationDecision {
  outcome: ApplicationOutcome;
  resolution: string;
  reason: string;
  refund_cents: number | null;
}

export interface DamageApplicationResult extends DamageApplicationDecision {
  refund_currency: string | null;
  resolved_at: string;
  refund_status: string | null;
  refund_status_text: string | null;
}

export interface DamageApplicationNotice {
  review_id: UUID;
  created_at: string;
  text: string;
}

export interface DamageReviewSummary {
  review_id: UUID;
  revision: number;
  order_number: string;
  status: ReviewStatus;
  created_at: string;
  requested_resolution: string | null;
  locked_for_processing: boolean;
  result: DamageApplicationResult | null;
}

export interface DamageReviewItem {
  item_id: UUID;
  title: string;
  purchased_quantity: number;
  damaged_quantity: number;
  description: string;
  photos: { attachment_id: UUID; media_type: string }[];
  decision: ReviewDecision | null;
  reason: string | null;
  reviewed_at: string | null;
}

export interface DamageReview extends DamageReviewSummary {
  items: DamageReviewItem[];
}

export interface StaffDamageReview extends DamageReviewSummary {
  items: (DamageReviewItem & { reviewer_id: string | null })[];
  resolver_id: string | null;
  order_paid_cents: number;
  order_currency: string;
}
