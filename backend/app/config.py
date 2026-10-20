"""
Configuration module for DataSentinel AI.

Loads settings from environment variables with sensible defaults.
API keys are kept server-side and never exposed to the frontend.
"""

import os
from dotenv import load_dotenv

# Load .env file if it exists (development convenience)
load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    # LLM Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-3.5-turbo")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # File upload limits
    MAX_FILE_SIZE_MB: int = 50  # Maximum CSV file size in megabytes
    MAX_ROWS_PREVIEW: int = 100  # Rows shown in preview
    ALLOWED_EXTENSIONS: set = {".csv"}

    # Quality score weights (documented and adjustable)
    # These weights determine how each category affects the overall score.
    # All weights must sum to 1.0.
    QUALITY_WEIGHTS: dict = {
        "completeness": 0.35,  # Missing values have the highest impact
        "uniqueness": 0.25,    # Duplicates significantly affect reliability
        "validity": 0.25,      # Invalid formats reduce data usability
        "consistency": 0.15,   # Outliers and anomalies are investigation candidates
    }

    @property
    def has_llm_key(self) -> bool:
        """Check if an LLM API key is configured."""
        return bool(self.OPENAI_API_KEY and self.OPENAI_API_KEY.strip())


settings = Settings()
