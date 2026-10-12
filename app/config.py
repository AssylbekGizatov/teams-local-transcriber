from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_api_token: str = ""
    asr_api_token: str = ""
    data_dir: Path = Path("./data")
    asr_backend: str = "gigaam"
    asr_model_path: str = ""
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_chunk_seconds: float = Field(default=20.0, ge=2, le=25)
    asr_preload: bool = False
    asr_cpu_threads: int = Field(default=4, ge=1, le=128)
    max_upload_mb: int = Field(default=500, ge=1, le=4096)
    max_audio_seconds: int = Field(default=14400, ge=1, le=86400)
    max_inference_queue: int = Field(default=4, ge=1, le=64)
    max_pending_jobs: int = Field(default=32, ge=1, le=1000)
    poll_seconds: float = Field(default=5.0, ge=0.1, le=60)
    retain_audio: bool = False
    retention_days: int = Field(default=30, ge=1, le=3650)
    teams_enabled: bool = False
    teams_authorization_reference: str = ""
    teams_allowed_tenant_ids: str = ""
    teams_allowed_meeting_ids: str = ""
    vexa_api_url: str = "http://vexa-gateway:8000"
    vexa_api_key: str = ""
    bot_name: str = "Meeting transcription (local ASR)"
    worker_enabled: bool = True
    trusted_hosts: str = "localhost,127.0.0.1,testserver,platform,asr-platform,asr-app"

    @field_validator("app_api_token", "asr_api_token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if value and (len(value) < 32 or value.lower().startswith("change-me")):
            raise ValueError(
                "API tokens must contain at least 32 characters; use scripts/generate_env.py"
            )
        return value

    @field_validator("asr_backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if value not in {"gigaam", "faster-whisper"}:
            raise ValueError("ASR_BACKEND must be gigaam or faster-whisper")
        return value

    @model_validator(mode="after")
    def validate_security(self):
        if self.app_api_token and self.asr_api_token == self.app_api_token:
            raise ValueError("APP_API_TOKEN and ASR_API_TOKEN must be distinct")
        if self.teams_enabled and not (
            self.teams_authorization_reference.strip()
            and self.teams_allowed_meeting_ids.strip()
            and self.vexa_api_key.strip()
        ):
            raise ValueError(
                "Teams requires authorization reference, meeting allowlist, and Vexa key"
            )
        return self

    @property
    def db_path(self) -> Path:
        return self.data_dir / "platform.sqlite3"

    @property
    def model_configured(self) -> bool:
        return bool(self.asr_model_path and Path(self.asr_model_path).exists())


@lru_cache
def get_settings() -> Settings:
    return Settings()
