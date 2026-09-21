from ..config import settings
from .base import BaseLLM
from .qwen import QwenLLM
def create_llm() -> BaseLLM:
    if settings.llm_provider == "qwen":
        return QwenLLM(
            api_key=settings.dashscope_api_key,
            model=settings.llm_model,
            verify_ssl=settings.llm_verify_ssl,
        )
    raise ValueError(f"不支持的 LLM provider: {settings.llm_provider}")

