import asyncio
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
from backend.agents.supervisor import build_supervisor

async def main():
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
    graph = build_supervisor(llm, registry)

    state = {"goal": "为什么这周营业额很低，怎么提高销量"}
    final = await graph.invoke(state)   # 全流程跑一遍

    print("【Planner 拆出的计划】")
    for s in final["plan"]:
        print(f"  {s['id']}: {s['task']}")
    print("\n【各子任务结论】")
    for k, v in final["results"].items():
        print(f"  {k}: {v}")
    print("\n【最终报告】")
    print(final["report"])

asyncio.run(main())