"""Explicit ORM registry shared by migrations and repositories."""

from nexusguard.infrastructure.models.accounts import Tenant, User, UserRiskProfile
from nexusguard.infrastructure.models.aftersales import (
    AfterSalesCase,
    CaseItem,
    Evidence,
    PolicyDecision,
    RiskAssessment,
)
from nexusguard.infrastructure.models.catalog import SKU, Inventory
from nexusguard.infrastructure.models.damage import DamageAttachment, DamageDraft, DamageDraftItem
from nexusguard.infrastructure.models.events import AuditLog, InboxEvent, OutboxEvent
from nexusguard.infrastructure.models.orders import LogisticsEvent, Order, OrderItem, Shipment
from nexusguard.infrastructure.models.payments import FinancialAction, OrderRefundBalance
from nexusguard.infrastructure.models.reception import (
    ReceptionMessage,
    ReceptionReply,
    ReceptionSession,
)
from nexusguard.infrastructure.models.workflows import ApprovalRequest, WorkflowInstance

__all__ = [
    "SKU",
    "AfterSalesCase",
    "ApprovalRequest",
    "AuditLog",
    "CaseItem",
    "Evidence",
    "DamageDraft",
    "DamageDraftItem",
    "DamageAttachment",
    "FinancialAction",
    "InboxEvent",
    "Inventory",
    "LogisticsEvent",
    "Order",
    "OrderItem",
    "OrderRefundBalance",
    "OutboxEvent",
    "PolicyDecision",
    "ReceptionMessage",
    "ReceptionSession",
    "ReceptionReply",
    "RiskAssessment",
    "Shipment",
    "Tenant",
    "User",
    "UserRiskProfile",
    "WorkflowInstance",
]
