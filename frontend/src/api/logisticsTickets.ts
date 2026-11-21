import { requestJson } from './reception';
import type { UUID } from './types';

export interface LogisticsTicket {
  id: UUID;
  order_number: string;
  created_at: string;
  resolved_at: string | null;
  result: string | null;
  kind: TicketKind;
  refund_status_text: string | null;
  compensation_cents: number | null;
  currency: string | null;
}

export type TicketKind = 'investigation' | 'delay_compensation';

interface Shipment {
  shipment_id: UUID;
  carrier: string;
  tracking_no: string | null;
  status: string;
  promised_delivery_at: string;
  delivered_at?: string | null;
  last_update_at: string | null;
  recent_events: { type: string; at: string }[];
}

export interface StaffTicket extends LogisticsTicket {
  reviewer_id: string | null;
  refund_case_id: UUID | null;
  facts: { observed_at: string; shipments: Shipment[] };
}

const prefix = '/api/v1/logistics';

export const logisticsTickets = {
  submit: (sessionId: UUID, orderId: UUID, ticketId: UUID, kind: TicketKind) =>
    requestJson<LogisticsTicket>(
      `${prefix}/sessions/${encodeURIComponent(sessionId)}/orders/${encodeURIComponent(orderId)}/tickets`,
      { ticket_id: ticketId, kind },
    ),
  queue: (resolved: boolean, offset: number) =>
    requestJson<StaffTicket[]>(`${prefix}/tickets?resolved=${resolved}&offset=${offset}`),
  resolve: (id: UUID, result: string, refundCents: number | null) =>
    requestJson<StaffTicket>(`${prefix}/tickets/${encodeURIComponent(id)}/resolution`, {
      result, refund_cents: refundCents,
    }),
};
