from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings with foundation extension points."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EMBEDDED_COPILOT_",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = "Embedded Copilot"
    version: Literal["7.1.0"] = "7.1.0"
    environment: str = "development"
    debug: bool = False
    llm_provider: str | None = None
    vector_store: str | None = None
    runtime_mode: Literal["offline", "llm"] = "offline"
    knowledge_dir: Path = Path("knowledge")
    chroma_path: Path = Path(".data/chroma")
    collection_name: str = "embedded_copilot_v01"
    embedding_provider: Literal["local_hash", "openai_compatible", "ollama"] = "local_hash"
    embedding_dimension: int = Field(default=384, ge=64, le=4096)
    chat_model: str | None = None
    embedding_model: str | None = None
    openai_base_url: HttpUrl | None = None
    openai_api_key: SecretStr | None = None
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=100, ge=0, le=2000)
    retrieval_top_k: int = Field(default=4, ge=1, le=20)
    retrieval_score_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    request_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    analysis_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    analysis_registry_capacity: int = Field(default=100, ge=1, le=10_000)
    model_provider: Literal["unavailable", "ollama"] = "unavailable"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    ollama_require_tls: bool = False
    vision_provider: Literal["unavailable", "ollama"] = "unavailable"
    ollama_vision_base_url: str = "http://127.0.0.1:11434"
    ollama_vision_model: str | None = None
    ollama_vision_require_tls: bool = False
    file_workspace_root: Path | None = None
    project_root: Path | None = None
    project_location_allow_list: tuple[Path, ...] = ()
    consistency_toolchain_bin: Path | None = None
    kicad_cli_path: Path | None = None
    kicad_install_roots: Annotated[tuple[Path, ...], NoDecode] = ()

    @field_validator("project_location_allow_list", mode="before")
    @classmethod
    def parse_project_location_allow_list(cls, value: object) -> tuple[Path, ...]:
        if value in (None, "", []):
            return ()
        if isinstance(value, str):
            return tuple(Path(item.strip()) for item in value.split(";") if item.strip())
        if isinstance(value, (list, tuple)):
            return tuple(Path(item) for item in value)
        raise TypeError("project_location_allow_list is invalid")

    @field_validator("kicad_install_roots", mode="before")
    @classmethod
    def parse_kicad_install_roots(cls, value: object) -> tuple[Path, ...]:
        if value in (None, "", []):
            return ()
        if isinstance(value, str):
            return tuple(Path(item.strip()) for item in value.split(";") if item.strip())
        if isinstance(value, (list, tuple)):
            return tuple(Path(item) for item in value)
        raise TypeError("kicad_install_roots is invalid")
    desktop_state_database: Path = Path(".data/embedded-copilot-desktop.sqlite3")
    desktop_project_metadata_directory: Path = Path(".data/embedded-copilot/projects")
    project_location_label: str = "Embedded Projects"
    desktop_local_identity_enabled: bool = True
    production_state_root: Path | None = None
    desktop_static_assets: Path | None = None
    desktop_session_ttl_seconds: int = Field(default=1800, ge=60, le=86_400)
    desktop_bootstrap_ttl_seconds: int = Field(default=30, ge=5, le=300)
    file_max_size_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        le=100 * 1024 * 1024,
    )

    @model_validator(mode="after")
    def validate_chunk_window(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self
