"""Application configuration using Pydantic settings"""
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    """Application settings"""
    
    # Project
    PROJECT_NAME: str = "Soccer Schedules API"
    ENVIRONMENT: str = "development"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://USER:PASSWORD@localhost:5432/soccerschedules"
    
    @field_validator('DATABASE_URL')
    @classmethod
    def convert_db_url(cls, v: str) -> str:
        """Convert postgres:// to postgresql+asyncpg:// and remove unsupported params"""
        # Replace postgres:// with postgresql+asyncpg://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        
        # Remove sslmode parameter (not supported by asyncpg)
        # asyncpg uses different SSL params, we'll handle it via connection params
        if "?sslmode=" in v:
            v = v.split("?")[0]
        if "&sslmode=" in v:
            parts = v.split("&sslmode=")
            v = parts[0]
            
        return v
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # CORS - Allow all origins for now, can restrict later
    CORS_ORIGINS: List[str] = ["*"]
    
    # Scraping
    SCRAPE_DELAY_MIN: int = 2  # Minimum seconds between requests
    SCRAPE_DELAY_MAX: int = 5  # Maximum seconds between requests
    SCRAPE_TIMEOUT: int = 30  # Request timeout in seconds
    
    # Scheduling
    SCHEDULER_ENABLED: bool = False
    SCRAPING_ENABLED: bool = False
    DEFAULT_SCRAPE_INTERVAL_HOURS: int = 24
    TOURNAMENT_SCRAPE_INTERVAL_HOURS: int = 1
    CAPTCHA_COOLDOWN_MINUTES: int = 60
    BROWSER_IMPORT_TOKEN: Optional[str] = None
    BROWSER_IMPORT_MAX_HTML_BYTES: int = 10 * 1024 * 1024
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
