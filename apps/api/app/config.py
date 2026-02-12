"""Runtime settings for API app."""

from __future__ import annotations

import os


class Settings:
    """Application settings loaded from environment."""

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://agro:agro@db:5432/agro",
    )


settings = Settings()
