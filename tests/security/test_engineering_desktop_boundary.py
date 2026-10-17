from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "src" / "embedded_copilot" / "engineering_desktop"


def test_desktop_public_models_do_not_expose_sensitive_or_mutating_fields():
    source = (PACKAGE / "models.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "api_key:",
        "credential:",
        "prompt:",
        "command:",
        "path:",
        "raw_log:",
    ):
        assert forbidden not in source


def test_desktop_runtime_has_no_execution_surface():
    source = (PACKAGE / "runtime.py").read_text(encoding="utf-8")
    for forbidden in (".build(", ".flash(", ".apply(", ".promote(", ".invoke("):
        assert forbidden not in source
