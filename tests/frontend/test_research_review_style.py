from pathlib import Path


def test_research_review_uses_defined_light_and_dark_theme_tokens() -> None:
    styles = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "copilot"
        / "styles.css"
    ).read_text(encoding="utf-8")
    review = styles[styles.index(".ide-research-review {") :]
    light = styles[styles.index(".ide-shell {") : styles.index(".ide-shell--theme-dark {")]
    dark = styles[styles.index(".ide-shell--theme-dark {") : styles.index(".ide-shell--workbench {")]

    for token in ("--ide-surface", "--ide-border", "--ide-text", "--ide-secondary", "--ide-subtle"):
        assert f"{token}:" in light
        assert f"{token}:" in dark
    assert "background: var(--ide-surface)" in review
    assert "color: var(--ide-text)" in review
    assert "border: 1px solid var(--ide-border)" in review
    assert "var(--surface-raised" not in review
