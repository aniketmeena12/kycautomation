"""
Importing this package registers every ORM model on Base.metadata, which is
what Base.metadata.create_all() (see app/core/database.init_db) needs to
create all tables. Import order matters only in that every model referenced
by a relationship() string (e.g. "Evidence") must be imported somewhere
before create_all runs -- importing this package guarantees that.
"""

from app.models.account import Account
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.case import Case
from app.models.client import Client
from app.models.evidence import Evidence
from app.models.investigation import (
    Investigation,
    InvestigationFinding,
    InvestigationRecommendation,
)
from app.models.media import AdverseMediaArticle
from app.models.ownership import OwnershipEntity, OwnershipRelationship
from app.models.resolution import EntityMatch
from app.models.review import HumanReview
from app.models.risk import RiskEvent, RiskScoreSnapshot, risk_event_evidence, risk_snapshot_trigger_event
from app.models.sanctions import SanctionsAlias, SanctionsEntity
from app.models.sar import SARDraft
from app.models.source_status import DatasetSourceStatus
from app.models.transaction import Transaction

__all__ = [
    "Account",
    "Alert",
    "AuditLog",
    "Case",
    "Client",
    "Evidence",
    "Investigation",
    "InvestigationFinding",
    "InvestigationRecommendation",
    "AdverseMediaArticle",
    "OwnershipEntity",
    "OwnershipRelationship",
    "EntityMatch",
    "HumanReview",
    "RiskEvent",
    "RiskScoreSnapshot",
    "risk_event_evidence",
    "risk_snapshot_trigger_event",
    "SanctionsAlias",
    "SanctionsEntity",
    "SARDraft",
    "DatasetSourceStatus",
    "Transaction",
]
