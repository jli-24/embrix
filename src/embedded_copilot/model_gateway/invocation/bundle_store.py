"""Pure compatibility surface; concrete persistence is loaded only on explicit adapter access."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {'_encode': 'embedded_copilot.model_gateway_persistence.bundle_store', '_changed': 'embedded_copilot.model_gateway_persistence.bundle_store', '_attempt_row': 'embedded_copilot.model_gateway_persistence.bundle_store', '_read_claim': 'embedded_copilot.model_gateway_persistence.bundle_store', '_update_attempt': 'embedded_copilot.model_gateway_persistence.bundle_store', '_update_claim': 'embedded_copilot.model_gateway_persistence.bundle_store', 'CounterBundleStoreMixin': 'embedded_copilot.model_gateway_persistence.bundle_store'}

def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

__all__ = ['CounterBundleStoreMixin']

if TYPE_CHECKING:
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        CounterBundleStoreMixin,
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _attempt_row as _attempt_row,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _changed as _changed,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _encode as _encode,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _read_claim as _read_claim,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _update_attempt as _update_attempt,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
    from embedded_copilot.model_gateway_persistence.bundle_store import (
        _update_claim as _update_claim,  # noqa: PLC0414 - preserve private compatibility re-export.
    )
