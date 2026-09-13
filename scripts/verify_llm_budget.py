"""真机验证：预算装饰器对真实 LLM 是否记得准、拦得住（需要 .env 里有可用 Key）

单测用的是假 LLM（usage 是我们自己塞的），这个脚本回答的是另一个问题：
真实 DashScope 返回的 usage 会不会被正确累加、到上限后是不是真的不再发请求。

用法：PYTHONPATH=. .venv/Scripts/python scripts/verify_llm_budget.py
"""
import asyncio
import time

from backend.core.llm.base import Message
from backend.core.llm.budget import BudgetedLLM, BudgetExceeded, UsageBudget
from backend.core.llm.factory import create_llm


async def main():
    # ① 记账准确性：一次真调用，看 usage 是不是真 token 数
    budget = UsageBudget(max_tokens=100000)
    llm = BudgetedLLM(create_llm(), budget)
    t0 = time.time()
    resp = await llm.chat([Message(role="user", content="用一句话说明什么是电商转化率")])
    print(f"① 真调用返回 {len(resp.content)} 字，耗时 {time.time() - t0:.1f}s")
    print(f"   usage = {budget.snapshot()}")
    assert budget.calls == 1, "调用次数没记上"
    assert budget.estimated_calls == 0, "真实接口返回了 usage，不该走估算"
    assert budget.total_tokens > 0, "token 数为 0，说明 usage 没解析到"

    # ② 拦截有效性：把上限压到"比一次调用还小"，第二次必须被拦下且不产生网络请求
    budget2 = UsageBudget(max_tokens=1)
    llm2 = BudgetedLLM(create_llm(), budget2)
    await llm2.chat([Message(role="user", content="你好")])
    t0 = time.time()
    try:
        await llm2.chat([Message(role="user", content="你好")])
        raise AssertionError("超限后仍然发起了调用")
    except BudgetExceeded as e:
        print(f"② 第二次被拦下（{time.time() - t0:.2f}s，未发起网络请求）：{e.detail}")
        assert time.time() - t0 < 0.5, "拦截太慢，疑似仍然发了请求"

    print("\n结论：真机记账准确、超限即拦 —— 预算不是纸面配置")


if __name__ == "__main__":
    asyncio.run(main())
