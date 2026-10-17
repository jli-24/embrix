"""Presentation-only Native Desktop Shell boundary.

The package intentionally contains no ApplicationRuntime, Agent, Workspace,
Execution, Memory, Validation or other engineering capability.  The native
GUI adapter talks to the product only through the URL supplied by the
launcher and the existing FastAPI/session boundary.
"""

from .host import (
    NativeDesktopHostPort,
    NativeShellConfig,
    NativeShellError,
    NativeShellResult,
    PyWebView2DesktopHost,
)
from .picker import (
    NativeProjectRootBridge,
    NativeProjectRootPicker,
    NativeProjectRootPickerPort,
    NativeProjectRootSelection,
    NativeProjectRootSelectionStatus,
)
from .policy import NavigationAction, NavigationDecision, decide_navigation
from .single_instance import SingleInstanceGuard

__all__ = (
    "NativeDesktopHostPort",
    "NativeShellConfig",
    "NativeShellError",
    "NativeShellResult",
    "NavigationAction",
    "NavigationDecision",
    "PyWebView2DesktopHost",
    "SingleInstanceGuard",
    "NativeProjectRootBridge",
    "NativeProjectRootPicker",
    "NativeProjectRootPickerPort",
    "NativeProjectRootSelection",
    "NativeProjectRootSelectionStatus",
    "decide_navigation",
)
