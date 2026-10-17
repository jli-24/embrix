from __future__ import annotations

import copy

from .exceptions import CapabilityRegistryError
from .models import CapabilityBindingReference, CapabilityType
from .ports import CapabilityBindingPort


class CapabilityRegistry:
    __slots__ = ("_bindings",)

    def __init__(self, bindings: tuple[CapabilityBindingPort, ...]) -> None:
        if type(bindings) is not tuple:
            raise TypeError("capability bindings must be a tuple")
        resolved: dict[CapabilityType, CapabilityBindingPort] = {}
        identities: set[tuple[str, str]] = set()
        for binding in bindings:
            if not isinstance(binding, CapabilityBindingPort):
                raise CapabilityRegistryError("capability binding is invalid")
            try:
                reference = CapabilityBindingReference.model_validate(
                    copy.deepcopy(binding.reference.model_dump(mode="python"))
                )
            except Exception as error:
                raise CapabilityRegistryError(
                    "capability binding reference is invalid"
                ) from error
            identity = (reference.binding_id, reference.binding_version)
            if reference.capability in resolved or identity in identities:
                raise CapabilityRegistryError("duplicate capability binding")
            resolved[reference.capability] = binding
            identities.add(identity)
        self._bindings = resolved

    def resolve(self, capability: CapabilityType) -> CapabilityBindingPort | None:
        if type(capability) is not CapabilityType:
            raise CapabilityRegistryError("capability type is invalid")
        return self._bindings.get(capability)


__all__ = ("CapabilityRegistry",)
