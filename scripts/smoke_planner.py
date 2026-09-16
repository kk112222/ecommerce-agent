import asyncio
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
from backend.agents.planner import Planner   # 改名后就是 backend.agents.planner

async def main():
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
    planner = Planner(llm, registry)          # skills 不传 = 去 backend/skills/*.md 现读
    result = await planner.plan("为什么这周营业额很低，怎么提高销量")
    print("命中技能：", result["skill"] or "（无）")
    for step in result["tasks"]:
        print(step)

asyncio.run(main())