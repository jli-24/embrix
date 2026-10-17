"""Pure compatibility surface; concrete persistence is loaded only on explicit adapter access."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

from pydantic import BaseModel

from .consumption_contracts import AccountAuthorityPremiseV1, ConsumptionScopeV1

_Record = TypeVar("_Record", bound=BaseModel)

class ConsumptionRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _reject(code: str = "CONSUMPTION_RECORD_INVALID") -> NoReturn:
    raise ConsumptionRejected(code)


def _now(now: datetime) -> None:
    if type(now) is not datetime or now.utcoffset() is None:
        _reject("CONSUMPTION_TIME_INVALID")


def _checked(kind: type[_Record], value: object) -> _Record:
    if type(value) is not kind:
        _reject()
    try:
        result = kind.model_validate(value.model_dump(mode="python"))
        if result != value:
            _reject()
        return result
    except Exception as error:
        raise ConsumptionRejected("CONSUMPTION_RECORD_INVALID") from error


def _account(
    scope: ConsumptionScopeV1,
    authority: AccountAuthorityPremiseV1 | None,
    now: datetime,
) -> AccountAuthorityPremiseV1:
    if authority is None:
        _reject("ACCOUNT_AUTHORITY_UNAVAILABLE")
    authority = _checked(AccountAuthorityPremiseV1, authority)
    if (
        authority.status != "ACTIVE"
        or not authority.observed_at <= now < authority.valid_until
    ):
        _reject("ACCOUNT_AUTHORITY_UNAVAILABLE")
    if (
        authority.tenant_id,
        authority.principal_id,
        authority.connection_id,
        authority.connection_revision,
        authority.credential_revision,
        authority.endpoint_revision,
        authority.scope_fingerprint,
    ) != (
        scope.tenant_id,
        scope.principal_id,
        scope.connection_id,
        scope.connection_revision,
        scope.credential_revision,
        scope.endpoint_revision,
        scope.fingerprint,
    ):
        _reject("ACCOUNT_AUTHORITY_STALE")
    return authority


_LAZY_EXPORTS = {'_encode': 'embedded_copilot.model_gateway_persistence.consumption_store', '_decode': 'embedded_copilot.model_gateway_persistence.consumption_store', 'initialize_consumption_schema': 'embedded_copilot.model_gateway_persistence.consumption_store', '_owner': 'embedded_copilot.model_gateway_persistence.consumption_store', '_preview': 'embedded_copilot.model_gateway_persistence.consumption_store', '_grant': 'embedded_copilot.model_gateway_persistence.consumption_store', '_event': 'embedded_copilot.model_gateway_persistence.consumption_store', 'validate_phase_grant': 'embedded_copilot.model_gateway_persistence.consumption_store', 'ConsumptionStoreMixin': 'embedded_copilot.model_gateway_persistence.consumption_store'}

def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

__all__ = ['ConsumptionRejected', 'initialize_consumption_schema', 'validate_phase_grant', 'ConsumptionStoreMixin']  # noqa: RUF022 - preserve legacy export order.

if TYPE_CHECKING:
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        ConsumptionStoreMixin,
        initialize_consumption_schema,
        validate_phase_grant,
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _decode as _decode,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _encode as _encode,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _event as _event,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _grant as _grant,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _owner as _owner,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.consumption_store import (
        _preview as _preview,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
