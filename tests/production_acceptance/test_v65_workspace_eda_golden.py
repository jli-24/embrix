import os

import pytest

from scripts.v65_workspace_eda_golden import run_golden


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_V65_GOLDEN") != "1",
    reason="v6.5 external synthetic Golden requires RUN_V65_GOLDEN=1",
)


def test_v65_workspace_eda_golden() -> None:
    report = run_golden()
    assert report["status"] == "PARTIAL VERIFIED", report
    assert all(value == 0 for value in report["invocations"].values())
