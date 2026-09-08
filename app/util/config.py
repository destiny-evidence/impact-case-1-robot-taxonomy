"""API config parsing and model."""

import logging
import tomllib
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from destiny_sdk.visibility import Visibility
from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def configure_logging(base_level: int | str = "INFO") -> None:
    """Configure logging for the application."""
    httpx_level = logging.DEBUG if base_level in {logging.DEBUG, "DEBUG"} else logging.WARNING
    logging.getLogger("httpx").setLevel(httpx_level)

    logging.basicConfig(
        level=base_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def get_logger(
    name: str,
    level: str | None = None,
    init_logging: bool = False,
    base_level: int | str = "INFO",
) -> logging.Logger:
    """Get an initialised logger."""
    if init_logging:
        configure_logging(base_level=base_level)
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger


class Environment(StrEnum):
    """
    Environment that therobot is running in.

    **Allowed values**:
    - `local`: The robot is running locally
    - `development`: The robot is running in development
    - `staging`: The robot is running in staging
    - `production`: The robot is running in production
    - `test`: The robot is running as a test fixture for the repository
    """

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


def read_toml_value(path_to_toml: str | Path, *path: str) -> str:
    """Read the information from the pyproject.toml."""
    with open(path_to_toml, "rb") as toml_file:  # noqa: PTH123
        current_node: Any | None = tomllib.load(toml_file)
        steps = ""
        for step in path:
            if type(current_node) is not dict:
                raise ValueError(f"Cannot follow `step` after `{steps}` in {path_to_toml}")

            steps += f".{step}"

            if not (current_node := current_node.get(step, None)):
                raise ValueError(f"`{steps}` not present in {path_to_toml}")

        if not isinstance(current_node, str):
            raise ValueError(  # noqa: TRY004
                f"{steps} did not lead to singular string value in {path_to_toml}",
            )

        return current_node


class OTelConfig(BaseModel):
    """Honeycomb OTLP/HTTP export config, supplied as the JSON env var `OTEL_CONFIG`."""

    trace_endpoint: str = Field(default="https://api.honeycomb.io/v1/traces", description="OTLP/HTTP traces endpoint")
    api_key: str | None = Field(default=None, description="Honeycomb ingest key, sent as the x-honeycomb-team header")
    timeout: int = Field(default=30, description="Export timeout in seconds", gt=0)


class Settings(BaseSettings):
    """Settings model for polling robot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime settings
    loglevel: str | int = Field(default="INFO", description="Logging level")

    # Observability settings
    otel_enabled: bool = Field(default=False, description="Export traces to Honeycomb over OTLP/HTTP.")
    otel_config: OTelConfig | None = Field(default=None, description="Honeycomb export config, as JSON in OTEL_CONFIG.")
    otel_capture_llm_content: bool = Field(
        default=False,
        description="Attach prompt and completion text to LLM spans. Off by default: the prompts carry reference abstracts.",
    )

    # Robot identification settings
    robot_id: UUID = Field(
        description="Client id needed for communicating with destiny repository.",
    )
    robot_version: str = Field(
        default=read_toml_value("pyproject.toml", "project", "version"),
        pattern="[0-9]+.[0-9]+.[0-9]+",
        description="Semantic version of the robot",
    )
    robot_name: str = Field(
        default=read_toml_value("pyproject.toml", "project", "name"),
        pattern="([a-z]+-)+",
        description="Name of the robot",
    )

    # Repository settings
    base_url: HttpUrl = Field(
        default=HttpUrl("https://api.staging.evidence-repository.org"),
        description="DESTinY repository API endpoint",
    )
    env: Environment = Field(
        default=Environment.STAGING,
        description="The environment this robot is deployed in.",
    )

    # Robot looping settings
    interval_seconds: int = Field(
        default=30,
        description="How long to sleep between each loop",
    )
    concurrent_batches: int = Field(
        default=1,
        description="Batches to process at once. Lets one worker prompt while another polls or submits.",
        ge=1,
    )
    batch_size: int = Field(
        default=500,
        description="The number of references to include per batch",
    )

    llm_num_retries: int = Field(default=3, description="Retries on transient errors.", ge=0)

    llm_requests_per_minute: int = Field(default=1200, description="Number of prompts per minute for the API endpoint", ge=1)
    llm_tokens_per_minute: int = Field(default=1200 * 1000)

    llm_max_concurrent_extractions: int = Field(default=100, description="Maximum number of prompts to run in parallel", ge=1)

    max_document_tokens: int = Field(default=1500, description="Maximum allowable tokens for a document we are extracting from", ge=1)

    min_document_tokens: int = Field(
        default=50,
        description="Minimum tokens for a document to be extractable; below this (e.g. missing abstract) it is skipped as invalid",
        ge=1,
    )

    # Robot identification and authentication settings
    robot_secret: SecretStr = Field(
        description="Secret needed for communicating with destiny repo.",
    )

    extraction_config: Path = Field(
        default=Path(".configs/taxonomy/extraction_config.yaml"),
        description="Path to the frozen deet DataExtractionConfig. Its vocabulary_path and "
        "vocabulary_mapping_path resolve relative to this file's directory.",
    )

    extraction_attribute_csv: Path = Field(default=Path(".configs/taxonomy/prompts_used.csv"), description="Path to the attribute csv.")

    upstream_scheme: str = Field(
        default="domain-inclusion",
        description="scheme of the inclusion annotation that triggers taxonomy annotation.",
    )
    upstream_label: str = Field(
        default="destiny-high-precision",
        description="label of the final inclusion decision that triggers taxonomy annotation.",
    )

    abandon_threshold: float = Field(
        default=0.1,
        description="Shut the robot down (leaving the batch unfinalised for redelivery) if more than this "
        "fraction of references fail after retries — a signal of a systemic outage rather than bad documents.",
        ge=0.0,
        le=1.0,
    )

    # Enhancement settings
    enhancement_visibility: Visibility = Field(default=Visibility.PUBLIC, description="Visibility level for Enhancements")

    vocabulary_uid: str = Field(description="Project UID under which the vocabulary is published in the Vocabulary Builder.")

    vocabulary_version: str = Field(description="Published vocabulary version.")

    @model_validator(mode="after")
    def _warn_missing_otel_api_key(self) -> "Settings":
        if self.otel_enabled and not (self.otel_config and self.otel_config.api_key):
            logging.getLogger("taxonomy-robot").warning("OTEL_ENABLED set but no Honeycomb api_key in OTEL_CONFIG")
        return self

    @property
    def vocabulary_uri(self) -> HttpUrl:
        return HttpUrl(f"https://vocab.evidence-repository.org/published/{self.vocabulary_uid}/{self.vocabulary_version}/vocabulary.ttl")

    @property
    def context_uri(self) -> str:
        return "https://vocab.evidence-repository.org/published/" f"{self.vocabulary_uid}/{self.vocabulary_version}/context.jsonld"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get a cached settings object."""
    return Settings()
