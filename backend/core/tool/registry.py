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
        """按名字找到工具，执行它"""
        tool = self.tools[name]
        return await tool.execute(**kwargs)

