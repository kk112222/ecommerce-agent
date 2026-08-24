"""
CLI 命令行工具 —— 终端里直接和 AI Agent 对话

用法:
    uv run python scripts/cli.py chat "运动鞋卖了多少"
    uv run python scripts/cli.py chat "库存低于10的商品有哪些"
    uv run python scripts/cli.py chat "vip会员消费情况"
"""
import asyncio
import argparse
import sys

import requests
import urllib3

from backend.core.llm.factory import create_llm
from backend.core.llm.base import Message
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
from backend.agents.data_analysis.simple_agent import ReActAgent

# 临时：Windows 开发环境跳过 SSL 验证
urllib3.disable_warnings()
_original_request = requests.Session.request

def _patched_request(self, method, url, **kwargs):
    kwargs["verify"] = False
    return _original_request(self, method, url, **kwargs)

requests.Session.request = _patched_request


async def chat(message: str):
    """发送消息，打印回复"""
    from datetime import datetime

    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)  # content 类工具需要 llm
    agent = ReActAgent(llm, registry)

    today = datetime.now().strftime("%Y-%m-%d")
    messages = [
        Message(role="system", content=(
            f"你是电商运营 AI Agent，今天是{today}。金额以元为单位。\n\n"
            "你能自主调用工具，多步执行复杂任务。收到目标后规划步骤，逐步调用工具，综合分析后给出报告。"
        )),
        Message(role="user", content=message),
    ]

    result = await agent.run(messages)
    print(result)


def main():
    parser = argparse.ArgumentParser(description="电商运营 AI Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # chat 子命令
    chat_parser = subparsers.add_parser("chat", help="发送消息给 AI Agent")
    chat_parser.add_argument("message", type=str, nargs="+", help="要发送的消息")

    args = parser.parse_args()

    if args.command == "chat":
        full_message = " ".join(args.message)
        asyncio.run(chat(full_message))


if __name__ == "__main__":
    main()
