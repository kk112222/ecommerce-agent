import logging

from backend.core.tool.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


def _coerce(prop: dict, value):
    """按 spec 声明的 type 做轻量校验 + 能救的顺带转换，返回 (ok, 值, 原因)

    为什么要有这一层（P2-14）：以前只查"参数在不在"，类型错的参数会一路进到工具内部，
    再以 ValueError/TypeError 的形式炸出来。有了 P0-2 的兜底虽然不崩了，但让 LLM
    拿到的错误越靠前越准确，它一次改对的概率越高。
    能救的顺手救（"3" → 3、3.0 → 3、True → 1），救不了才报错。
    """
    t = prop.get("type")
    if t in (None, "string"):
        if isinstance(value, str):
            return True, value, ""
        if isinstance(value, (int, float, bool)):
            return True, str(value), ""
        return False, value, "应为字符串"
    if t == "integer":
        if isinstance(value, bool):
            return True, int(value), ""
        if isinstance(value, int):
            return True, value, ""
        if isinstance(value, float) and value.is_integer():
            return True, int(value), ""
        if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
            return True, int(value), ""
        return False, value, "应为整数"
    if t == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True, float(value), ""
        try:
            return True, float(str(value).strip()), ""
        except (TypeError, ValueError):
            return False, value, "应为数字"
    if t == "boolean":
        if isinstance(value, bool):
            return True, value, ""
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return True, value.strip().lower() == "true", ""
        return False, value, "应为布尔值"
    if t == "array":
        return (True, value, "") if isinstance(value, list) else (False, value, "应为数组")
    if t == "object":
        return (True, value, "") if isinstance(value, dict) else (False, value, "应为对象")
    return True, value, ""          # 没声明/未知类型：不拦


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

    def subset(self, names) -> "ToolRegistry":
        """只含指定工具的新注册中心（按 tool_hint 给子任务隔离职责）

        比"prompt 里叮嘱它只用某个工具"硬：子任务拿到的注册中心里根本没有别的工具，
        既看不到别的工具说明书，也调不动。names 里不存在的工具名直接忽略。
        """
        sub = ToolRegistry()
        for n in names:
            tool = self.tools.get(n)
            if tool is not None:
                sub.register(tool)
        return sub
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
        4. 工具本体抛异常 → 兜成可读 error（LLM 最常见的错是乱传日期"本周"，
           异常穿透上来会打死整个 ReAct 循环，而它本该是"看到错误 → 改参数重试"）
        以上失败一律返回 ToolResult(success=False)，不抛异常打断 ReAct 循环。
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

        # 类型校验（P2-14）：能转的转，转不了的当场报错，别让脏参数进到工具里再炸
        coerced = {}
        for k, v in clean.items():
            ok, val, reason = _coerce(props.get(k) or {}, v)
            if not ok:
                return ToolResult(success=False, data=None,
                                  error=f"调用 {name} 的参数 {k} 类型不对：{reason}，"
                                        f"实际收到 {type(v).__name__}（{v!r}）。请改正后重新调用。")
            coerced[k] = val

        try:
            return await tool.execute(**coerced)
        except Exception as e:
            # 兜住工具本体的任何异常（ValueError/TypeError/网络错…）：
            # 变成结构化错误喂回 LLM，让它自己改参数重试，而不是整条链路崩掉
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(
                success=False, data=None,
                error=f"调用 {name} 失败：{type(e).__name__}: {e}。请检查参数后重试。",
            )

