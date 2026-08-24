import asyncio

from backend.core.llm.factory import create_llm
from backend.core.llm.base import Message
from backend.core.tool.registry import ToolRegistry
from backend.core.agent.base import AgentGraph
from backend.tools.data_tools.sales_query import SalesQueryTool
async def chat_node(state:dict) -> dict:
    llm = state["llm"]
    registry = state["registry"]
    messages = state["messages"]
    # ① 把工具列表转成 LLM 格式
    tool_specs = registry.get_all_specs()
    # ② 先问 LLM（带工具列表）
    response = await llm.chat(messages,tools=tool_specs)
    # ③ 如果 LLM 要调工具，就执行
    if response.tool_calls:
        messages.append(Message(
            role="assistant",
            content="",
            tool_calls=response.tool_calls,
        ))
        for tc in response.tool_calls:
            result = await registry.execute(tc.name,**tc.arguments)
            # 把结果喂回对话
            messages.append(Message(role="tool",content=str(result.data),
                                    tool_call_id=tc.id))
        # ④ 再问一次 LLM，让它根据结果生成回复
        response = await llm.chat(messages, tools=tool_specs)
    # ⑤ 存结果
    state["result"] = response.content
    return state
async def main():
    llm = create_llm()
    registry = ToolRegistry()
    registry.register(SalesQueryTool())
    # 搭图
    graph = AgentGraph()
    graph.add_node("chat_node", chat_node)
    graph.entry_point = "chat_node"

    # 初始 state
    state = {
        "llm": llm,
        "registry": registry,
        "messages": [
            Message(role="system", content="你是一个电商助手"),
            Message(role="user", content="上周（2026-07-07 到2026 - 07 - 13）卖了多少？"),
    ],
    }

    # 跑
    state = await graph.invoke(state)
    print(f"结果: {state['result']}")
if __name__ == "__main__":
    asyncio.run(main())
