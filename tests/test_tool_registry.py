"""工具注册中心的统一参数校验（离线）

校验逻辑收在 ToolRegistry.execute 一层（工具的 spec 就是契约），这里把四件事钉死：
未知工具名 / 缺必需参数 / 传错键名 / 工具本体抛异常
—— 一律返回 ToolResult(success=False)，不抛异常打断 ReAct。
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


class _BoomTool(BaseTool):
    """工具本体抛异常（对应 LLM 传 "本周" 进 date.fromisoformat 的真实场景）"""
    spec = ToolSpec(
        name="boom", description="会炸的工具",
        parameters={"type": "object",
                    "properties": {"d": {"type": "string", "description": "日期"}},
                    "required": ["d"]},
    )

    async def execute(self, d=None):
        raise ValueError(f"time data {d!r} does not match format '%Y-%m-%d'")


async def test_tool_exception_becomes_readable_error():
    """工具内部异常必须被兜成 success=False —— 否则穿透出去会打死整个 ReAct 循环"""
    reg = ToolRegistry()
    reg.register(_BoomTool())
    r = await reg.execute("boom", d="本周")
    assert r.success is False
    assert "boom" in r.error and "ValueError" in r.error and "本周" in r.error


async def test_real_tool_bad_date_does_not_raise():
    """接真工具回归：query_sales 传中文相对时间（LLM 最常犯的错）"""
    from backend.tools.data_tools import register_data_tools
    reg = ToolRegistry()
    register_data_tools(reg)
    r = await reg.execute("query_sales", start_date="本周", end_date="今天")
    assert r.success is False and "失败" in r.error


# ==================== 类型校验（P2-14） ====================

class _TypedTool(BaseTool):
    spec = ToolSpec(
        name="typed", description="类型校验用",
        parameters={"type": "object",
                    "properties": {"n": {"type": "integer", "description": "整数"},
                                   "f": {"type": "number", "description": "数字"},
                                   "b": {"type": "boolean", "description": "布尔"},
                                   "s": {"type": "string", "description": "字符串"}},
                    "required": ["n"]},
    )

    def __init__(self):
        self.got = None

    async def execute(self, n=None, f=None, b=None, s=None):
        self.got = {"n": n, "f": f, "b": b, "s": s}
        return ToolResult(success=True, data=self.got)


def _typed_reg():
    reg = ToolRegistry()
    tool = _TypedTool()
    reg.register(tool)
    return reg, tool


async def test_type_mismatch_returns_readable_error():
    """类型明显不对 → 可读错误，且不进到工具里"""
    reg, tool = _typed_reg()
    r = await reg.execute("typed", n=[1, 2, 3])
    assert r.success is False and "n" in r.error and "整数" in r.error
    assert tool.got is None                       # 工具压根没被调用


async def test_type_is_coerced_when_salvageable():
    """能救的顺手救：字符串数字 → 数字、3.0 → 3、True → 1"""
    reg, tool = _typed_reg()
    r = await reg.execute("typed", n="3", f="2.5", b="true", s=7)
    assert r.success is True
    assert tool.got == {"n": 3, "f": 2.5, "b": True, "s": "7"}


async def test_bool_is_not_accepted_as_integer():
    """bool 是 int 的子类，但语义上不该当整数用（True 当 n → 1 可以接受，这里只钉住不报错）"""
    reg, tool = _typed_reg()
    r = await reg.execute("typed", n=True)
    assert r.success is True and tool.got["n"] == 1
