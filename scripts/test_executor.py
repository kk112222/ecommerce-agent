import asyncio
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
from backend.agents.executor import Executor

async def main():
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
    ex = Executor(llm, registry)

    # 手动喂一个子任务（模拟 Planner 拆出来的 t1）
    task = {
        "id": "t1",
        "task": "查询上周（8月3日~8月9日）到本周（8月10日~8月12日）的销售额和订单数，对比变化",
        "tool_hint": "query_sales",
    }

    print("=" * 40)
    print("子任务:", task["task"])
    print("=" * 40)
    result = await ex.run(task)
    print("\n【执行器结论】")
    print(result)
asyncio.run(main())