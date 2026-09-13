# 全局配置 —— 从 .env 自动读取
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据库默认落在项目根目录，写成绝对路径 —— 不管从哪个目录启动都指向同一个库
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    dashscope_api_key: str = ""
    llm_provider: str = "qwen"
    llm_model: str = "qwen3.7-plus"
    # 只有本机证书链有问题时才在 .env 里置 False（默认必须校验）
    llm_verify_ssl: bool = True
    database_url: str = f"sqlite+aiosqlite:///{(_PROJECT_ROOT / 'data_v3.db').as_posix()}"
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 60 * 24
    debug: bool = False          # 控制 SQL echo 等调试开关
    # 单轮对话的 LLM 预算（P2-11）：ReAct 循环次数由 LLM 自决，没有上限就等于成本不封顶
    # 0 = 不限制（本地脚本/评测用）；正常一轮 4 个子任务实测在 3 万 tokens / 1 分钟内
    agent_budget_tokens: int = 120000
    agent_budget_seconds: int = 240
    # 前端只从这几个来源访问（dev 下 vite 代理其实同源、用不到 CORS；这里是给直连/上线留的白名单）
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # 忽略未定义的字段，避免报错
    )


settings = Settings()