import { requestJson } from './reception';
import type { UUID } from './types';

export interface LogisticsTicket {
  id: UUID;
  order_number: string;
  created_at: string;
  resolved_at: string | null;
  result: string | null;
}

interface Shipment {
  shipment_id: UUID;
  carrier: string;
  tracking_no: string | null;
  status: string;
  promised_delivery_at: string;
  last_update_at: string | null;
  recent_events: { type: string; at: string }[];
}

export interface StaffTicket extends LogisticsTicket {
  reviewer_id: string | null;
  facts: { observed_at: string; shipments: Shipment[] };
}

const prefix = '/api/v1/logistics';

export const logisticsTickets = {
  submit: (sessionId: UUID, orderId: UUID, ticketId: UUID) =>
    requestJson<LogisticsTicket>(
      `${prefix}/sessions/${encodeURIComponent(sessionId)}/orders/${encodeURIComponent(orderId)}/tickets`,
      { ticket_id: ticketId },
    ),
  queue: (resolved: boolean, offset: number) =>
    requestJson<StaffTicket[]>(`${prefix}/tickets?resolved=${resolved}&offset=${offset}`),
  resolve: (id: UUID, result: string) =>
    requestJson<StaffTicket>(`${prefix}/tickets/${encodeURIComponent(id)}/resolution`, { result }),
};
