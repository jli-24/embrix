from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot import __version__
from embedded_copilot.services.config import Settings


def test_settings_have_v70_offline_defaults() -> None:
    settings = Settings(_env_file=None)

    assert __version__ == "7.1.0"
    assert settings.version == "7.1.0"
    assert settings.runtime_mode == "offline"
    assert settings.chunk_size == 800
    assert settings.chunk_overlap == 100
    assert settings.retrieval_top_k == 4
    assert settings.embedding_dimension == 384


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError):
        Settings(chunk_size=100, chunk_overlap=100, _env_file=None)


def test_api_key_is_redacted_from_repr() -> None:
    settings = Settings(openai_api_key="super-secret", _env_file=None)

    assert "super-secret" not in repr(settings)


def test_example_environment_loads_in_offline_mode() -> None:
    example_env = Path(__file__).resolve().parents[2] / ".env.example"

    settings = Settings(_env_file=example_env)

    assert settings.runtime_mode == "offline"
    assert settings.openai_base_url is None
    assert settings.openai_api_key is None
    assert settings.chat_model is None
    assert settings.embedding_model is None


def test_settings_loads_bounded_kicad_toolchain_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDED_COPILOT_KICAD_CLI_PATH=D:/KiCad/bin/kicad-cli.exe\n"
        "EMBEDDED_COPILOT_KICAD_INSTALL_ROOTS=D:/KiCad;C:/KiCad\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.kicad_cli_path == Path("D:/KiCad/bin/kicad-cli.exe")
    assert settings.kicad_install_roots == (Path("D:/KiCad"), Path("C:/KiCad"))


def test_ollama_embedding_settings_are_independent_from_chat_credentials() -> None:
    settings = Settings(
        _env_file=None,
        embedding_provider="ollama",
        embedding_model="embeddinggemma",
        ollama_base_url="http://127.0.0.1:11434",
        openai_api_key=None,
    )

    assert settings.embedding_provider == "ollama"
    assert settings.embedding_model == "embeddinggemma"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.openai_api_key is None
