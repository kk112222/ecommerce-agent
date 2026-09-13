from backend.core.tool.base import BaseTool, ToolResult

class ToolRegistry:
    """统一管理所有工具"""
    def __init__(self):
        self.tools :dict[str, BaseTool] = {}
    def register(self,tool:BaseTool):
        """把工具存进去，key 是工具的名字"""
        self.tools[tool.spec.name] = tool


    def get_tool(self,name:str) -> BaseTool:
        """按名字取出工具"""
        return self.tools[name]
    def get_all_specs(self,):
        """把所有工具的说明书转成列表，传给 LLM"""
        result = []
        for tool in self.tools.values():
            result.append({
                "type" : "function",
                "function" : {
                    "name":tool.spec.name,
                    "description":tool.spec.description,
                    "parameters": tool.spec.parameters,
                }
            })
        return result
    async def execute(self,name:str,**kwargs) -> ToolResult:
        """按名字找到工具 → 校验参数 → 执行

        参数校验统一收在这一层（工具的 spec 就是对外契约），所有工具共享同一套：
        1. 工具名不存在 → 返回可读错误
        2. 缺必需参数 → 返回可读错误，并附上该参数该传什么（描述取自 spec）
        3. 多传 / 传错键名 → 过滤掉（LLM 常写错键名，工具签名接不住会抛 TypeError）
        校验失败一律返回 ToolResult(success=False)，不抛异常打断 ReAct 循环。
        """
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(success=False, data=None,
                              error=f"没有名为 {name} 的工具，可用工具：{list(self.tools)}")

        schema = tool.spec.parameters or {}
        props: dict = schema.get("properties", {})
        required: list = schema.get("required", [])

        # 缺参检测：键不存在、值为 None 或空串都算缺（0 / False 是合法值，不算缺）
        missing = [p for p in required if p not in kwargs or kwargs[p] in (None, "")]
        if missing:
            detail = "；".join(
                f"{p}（{props.get(p, {}).get('description', '')}）" for p in missing
            )
            return ToolResult(success=False, data=None,
                              error=f"调用 {name} 缺少必需参数：{detail}。请补齐后重新调用。")

        # 只放行 spec 声明过的参数（传错键名的会被这里挡掉，正好触发上面的缺参错误）
        clean = {k: v for k, v in kwargs.items() if k in props}
        return await tool.execute(**clean)

