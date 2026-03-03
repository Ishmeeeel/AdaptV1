"""
config.py – All environment variables in one place.
Load from .env locally; set directly on Render in production.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Supabase ──────────────────────────────────────────────────────────
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str

    # ── Azure TTS ─────────────────────────────────────────────────────────
    AZURE_TTS_KEY: str = ""
    AZURE_TTS_REGION: str = "eastus"

    # ── Hugging Face ──────────────────────────────────────────────────────
    HF_TOKEN: str = ""
    HF_MODEL: str = "mistralai/Mistral-7B-Instruct-v0.1"

    # ── CORS ──────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g.
    # "https://adaptable.vercel.app,http://localhost:3000"
    ALLOWED_ORIGINS_STR: str = "http://localhost:3000"

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS_STR.split(",")]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
