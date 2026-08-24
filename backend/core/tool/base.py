from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel



class ToolSpec(BaseModel):
    """工具的 JSON Schema 说明书 —— LLM 据此判断何时调用"""
    name: str
    description: str
    parameters : dict

class ToolResult(BaseModel):
    success: bool
    data : Any
    error: Optional[str] = None

class BaseTool(ABC):
    spec : ToolSpec
    @abstractmethod
    async def execute(self,**kwargs) -> ToolResult:
        ...

