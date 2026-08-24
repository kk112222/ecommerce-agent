import asyncio


from backend.core.agent.base import AgentGraph

def hello_node(state):
    state["result"] = "hello"
    return state
async def main():
    graph = AgentGraph()
    graph.add_node("say_hello",hello_node)
    graph.entry_point = "say_hello"

    state = await graph.invoke({"messages":[]})
    print(f"结果: {state['result']}")  # 应该打印 "hello world"

if __name__ == "__main__":
    asyncio.run(main())
