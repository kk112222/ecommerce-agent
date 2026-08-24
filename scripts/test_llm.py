import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.llm.factory import create_llm
from backend.core.llm.base import Message


def safe(text: str) -> str:
    """把无法打印的字符替换掉，避免 GBK 编码报错"""
    return text.encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding)


async def main() -> None:
    llm = create_llm()
    messages = [Message(role="user", content="你好, 你是谁？")]

    # 普通对话
    print("=== 普通对话 ===")
    response = await llm.chat(messages)
    print(f"回复: {safe(response.content)}")

    # 流式对话
    print("\n=== 流式对话 ===")
    print("回复: ", end="")
    async for chunk in llm.chat_stream(messages):
        print(safe(chunk), end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
