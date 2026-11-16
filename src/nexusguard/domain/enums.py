from enum import StrEnum


class Intent(StrEnum):
    PRESALES = "presales"
    LOGISTICS = "logistics"
    AFTERSALES = "aftersales"
    UNKNOWN = "unknown"


class Eligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"


class RiskLevel(StrEnum):
    LOW = "low"
    HIGH = "high"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class FinancialActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    RECONCILING = "reconciling"
    FAILED = "failed"
