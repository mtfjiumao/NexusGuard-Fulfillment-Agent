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


export interface AssistantReply {
  reply_id: UUID;
  phase: 'clarification' | 'answer' | 'error';
  text: string;
  created_at: string;
}

export interface ConversationMessage extends MessageReceipt {
  text: string;
  created_at: string;
  replies: AssistantReply[];
}

export interface MessagePage {
  messages: ConversationMessage[];
  next_before: UUID | null;
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
}
