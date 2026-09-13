"""工具注册中心的统一参数校验（离线）

校验逻辑收在 ToolRegistry.execute 一层（工具的 spec 就是契约），这里把三件事钉死：
未知工具名 / 缺必需参数 / 传错键名 —— 一律返回 ToolResult(success=False)，不抛异常打断 ReAct。
"""
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec
from backend.core.tool.registry import ToolRegistry


class _RecordingTool(BaseTool):
    """记录 execute 实际收到的参数，用来验证「只放行 spec 声明过的键」"""
    spec = ToolSpec(
        name="rec", description="测试用工具",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "string", "description": "必填项"},
                "count": {"type": "integer", "description": "可选项"},
            },
            "required": ["x"],
        },
    )

    def __init__(self):
        self.received = None

    async def execute(self, **kwargs):
        self.received = kwargs
        return ToolResult(success=True, data={"ok": True})


def _registry():
    reg = ToolRegistry()
    tool = _RecordingTool()
    reg.register(tool)
    return reg, tool


async def test_unknown_tool_returns_error_not_raise():
    reg, _ = _registry()
    r = await reg.execute("不存在的工具", x="1")
    assert r.success is False and "没有名为" in r.error


async def test_missing_required_param():
    reg, _ = _registry()
    r = await reg.execute("rec")                       # 完全没给
    assert r.success is False and "x" in r.error and "缺少必需参数" in r.error


async def test_empty_string_counts_as_missing():
    """空串按缺参处理（LLM 常传空占位）"""
    reg, _ = _registry()
    r = await reg.execute("rec", x="")
    assert r.success is False


async def test_zero_is_a_valid_value():
    """0 是合法值，不能被当成缺参（用 `in (None, \"\")` 而非 `not value` 判断的原因）"""
    reg, tool = _registry()
    r = await reg.execute("rec", x="正常", count=0)
    assert r.success is True and tool.received == {"x": "正常", "count": 0}


async def test_wrong_keynames_filtered_out():
    """LLM 传错键名 → 未声明的键被过滤，工具签名接得住，不会 TypeError"""
    reg, tool = _registry()
    r = await reg.execute("rec", x="正常", 错键="噪声")
    assert r.success is True
    assert tool.received == {"x": "正常"}               # 噪声键被挡掉


async def test_wrong_keyname_triggers_missing():
    """把必填项写错名字 = 等于没给 → 触发缺参错误（可读提示让它自我纠正）"""
    reg, _ = _registry()
    r = await reg.execute("rec", xx="正常")
    assert r.success is False and "x" in r.error
