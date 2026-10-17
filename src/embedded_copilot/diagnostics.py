"""Content-free diagnostic helpers shared by application boundaries.

The implementation remains in :mod:`runtime_diagnostics` for compatibility
with existing telemetry consumers.  This narrow alias keeps domain services
from importing a runtime-named module directly.
"""

from .runtime_diagnostics import (
    attach_exception_diagnostics,
    exception_diagnostics,
    record_application_runtime_not_reached,
    record_application_runtime_outer_not_reached,
    record_application_runtime_outer_stage,
    record_application_runtime_stage,
    record_binding_engineering_qa_not_reached,
    record_binding_engineering_qa_stage,
    record_engineering_qa_not_reached,
    record_engineering_qa_stage,
    record_prompt_pipeline_not_reached,
    record_prompt_pipeline_stage,
)

__all__ = (
    "attach_exception_diagnostics",
    "exception_diagnostics",
    "record_application_runtime_not_reached",
    "record_application_runtime_outer_not_reached",
    "record_application_runtime_outer_stage",
    "record_application_runtime_stage",
    "record_binding_engineering_qa_not_reached",
    "record_binding_engineering_qa_stage",
    "record_engineering_qa_not_reached",
    "record_engineering_qa_stage",
    "record_prompt_pipeline_not_reached",
    "record_prompt_pipeline_stage",
)
