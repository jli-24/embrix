"""v7.0 production-release boundaries.

This package deliberately owns release concerns only.  It neither grants an
engineering execution capability nor changes the v6.x chat/SSE contracts.
"""

from .backup import BackupService, RestoreError
from .credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    PROVIDER_BINDING_INTEGRITY_PURPOSE,
    CredentialProviderPort,
    InMemoryCredentialProvider,
    WindowsCredentialManager,
)
from .migration import MigrationRegistry, MigrationTarget
from .model_pool import ModelPoolRefreshResult, ProviderModelDiscoveryPort, ProviderModelPool
from .paths import ProductionStateLayout
from .provider import (
    DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    ProviderConfiguration,
    ProviderConfigurationService,
    ProviderConfigurationStore,
    ProviderConnectionStatus,
    ProviderProjection,
    ReloadableModelGateway,
    TransmissionApprovalError,
    TransmissionApprovalReceipt,
    TransmissionApprovalService,
    TransmissionDecision,
    context_fingerprint,
)
from .provider_verification import (
    ConnectionTestRecord,
    GoldenEvaluation,
    GoldenEvaluationStatus,
    ModelRuntimeHealth,
    ProviderVerificationReceipt,
    ProviderVerificationReceiptStore,
    ProviderVerificationTarget,
    ReceiptConflict,
    ResponseParseStatus,
    VerificationStatus,
    evaluate_provider_golden,
    projection_revision,
)
from .release import PRODUCT_VERSION
from .session import LocalSessionManager, SessionRejected

__all__ = (
    "CHAT_PROVIDER_CREDENTIAL_PURPOSE",
    "ConnectionTestRecord",
    "DEEPSEEK_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
    "PRODUCT_VERSION",
    "PROVIDER_BINDING_INTEGRITY_PURPOSE",
    "BackupService",
    "CredentialProviderPort",
    "GoldenEvaluation",
    "GoldenEvaluationStatus",
    "InMemoryCredentialProvider",
    "LocalSessionManager",
    "MigrationRegistry",
    "MigrationTarget",
    "ModelRuntimeHealth",
    "ModelPoolRefreshResult",
    "ProductionStateLayout",
    "ProviderConfiguration",
    "ProviderConfigurationService",
    "ProviderConfigurationStore",
    "ProviderConnectionStatus",
    "ProviderProjection",
    "ProviderModelDiscoveryPort",
    "ProviderModelPool",
    "ProviderVerificationReceipt",
    "ProviderVerificationReceiptStore",
    "ProviderVerificationTarget",
    "ReceiptConflict",
    "ReloadableModelGateway",
    "ResponseParseStatus",
    "RestoreError",
    "SessionRejected",
    "TransmissionApprovalError",
    "TransmissionApprovalReceipt",
    "TransmissionApprovalService",
    "TransmissionDecision",
    "VerificationStatus",
    "WindowsCredentialManager",
    "context_fingerprint",
    "evaluate_provider_golden",
    "projection_revision",
)
