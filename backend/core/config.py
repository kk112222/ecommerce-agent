# 全局配置 —— 从 .env 自动读取
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    dashscope_api_key: str = ""
    llm_provider: str = "qwen"
    llm_model: str = "qwen3.7-plus"
    database_url: str = "sqlite+aiosqlite:///./data.db"
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 60 * 24

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略未定义的字段，避免报错


settings = Settings()