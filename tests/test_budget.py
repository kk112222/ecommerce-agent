"""LLM 预算与用量汇总（离线：假 LLM，不联网）

P2-11：ReAct 循环调几次工具由 LLM 自决，最坏 40+ 次调用，成本与延迟都不封顶，
而 TokenUsage 定义了从没被汇总过。这里钉三件事：
① 账本记得准（有 usage 用真值，流式没 usage 就估算并标记）
② 到上限后不再发起新调用
③ 超预算时图必须"降级出报告"，不是抛 500 让用户看空白
④ 子任务的**非预算**异常（LLM 500/超时/工具内部报错）同样只降级那一格，不拖垮整轮
"""
import json

import pytest

from backend.agents.supervisor import build_supervisor
from backend.core.llm.base import LLMResponse, Message, TokenUsage
from backend.core.llm.budget import BudgetedLLM, BudgetExceeded, UsageBudget, estimate_tokens
from backend.core.tool.registry import ToolRegistry
from tests.conftest import FakeLLM

# 走数据分析链路的最小脚本：意图 → 计划 → 两个子任务各自的结论
INTENT = '{"intent": "analysis"}'
PLAN = '[{"id": "t1", "task": "查销售"}, {"id": "t2", "task": "查库存"}]'


class _CountingFake(FakeLLM):
    """FakeLLM + usage 记账，验证装饰器读的是真 usage 而不是估算"""

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        r = await super().chat(messages, tools=tools, temperature=temperature)
        r.usage = TokenUsage(prompt_tokens=100, completion_tokens=50)
        return r


class _BlockAfter(FakeLLM):
    """前 N 次调用正常，之后抛 BudgetExceeded —— 模拟额度被前面的步骤打光"""

    def __init__(self, script, block_after: int):
        super().__init__(script)
        self.block_after = block_after
        self.n = 0

    def _guard(self):
        if self.n >= self.block_after:
            raise BudgetExceeded("tokens", "测试：额度已用尽")
        self.n += 1

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        self._guard()
        return await super().chat(messages, tools=tools, temperature=temperature)

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        self._guard()          # 异步生成器：首次迭代时才跑，正好在 synthesize 的 async for 里抛出
        async for chunk in super().chat_stream(messages, tools=tools, temperature=temperature):
            yield chunk


class _FailOnTask(FakeLLM):
    """遇到指定子任务的调用就抛非预算异常 —— 模拟 LLM 500 / 超时 / 工具内部报错

    按 messages 内容判断，而不是"第几次调用"：子任务在 gather 里是并发的，
    靠调用序号归因会随调度顺序漂移。标记词只出现在那一个子任务的 prompt 里。
    """

    def __init__(self, script, fail_when: str, exc: Exception | None = None):
        super().__init__(script)
        self.fail_when = fail_when
        self.exc = exc or RuntimeError("模拟 DashScope 500")
        self.failed = 0

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        if any(self.fail_when in str(getattr(m, "content", "")) for m in messages):
            self.failed += 1
            raise self.exc
        return await super().chat(messages, tools=tools, temperature=temperature)


class _StreamBlocked(FakeLLM):
    """只有流式被卡（子任务都跑完了、综合报告没额度了）—— 最该保住数据的那种情况"""

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        raise BudgetExceeded("tokens", "测试：综合阶段没额度了")
        yield  # noqa: 保持异步生成器语义


# ==================== 账本 ====================

async def test_counts_real_usage():
    budget = UsageBudget(max_tokens=1000)
    llm = BudgetedLLM(_CountingFake(["回复"]), budget)
    await llm.chat([Message(role="user", content="你好")])

    assert budget.prompt_tokens == 100 and budget.completion_tokens == 50
    assert budget.calls == 1 and budget.estimated_calls == 0    # 用了真值，没走估算
    assert budget.snapshot()["total_tokens"] == 150


async def test_stream_without_usage_is_estimated():
    """流式接口不返回 usage：不能不记，但必须标出来是估的"""
    budget = UsageBudget()
    llm = BudgetedLLM(FakeLLM(), budget)
    text = "".join([c async for c in llm.chat_stream([Message(role="user", content="你好")])])

    assert text == "假流式"
    snap = budget.snapshot()
    assert snap["calls"] == 1 and snap["estimated_calls"] == 1
    assert snap["total_tokens"] > 0


async def test_blocks_new_calls_after_limit():
    """到上限后第 3 次调用必须被拦下（第 2 次是"已经开始的那一次"，允许跑完）"""
    budget = UsageBudget(max_tokens=200)
    llm = BudgetedLLM(_CountingFake(), budget)
    m = [Message(role="user", content="你好")]
    await llm.chat(m)
    await llm.chat(m)

    with pytest.raises(BudgetExceeded) as e:
        await llm.chat(m)
    assert e.value.kind == "tokens"
    assert budget.exhausted == "tokens"      # 快照里如实标记，前端能看出是被截断的


async def test_time_budget_blocks():
    """时间预算独立于 token：LLM 卡住不返回时，token 可能一次都没记上"""
    budget = UsageBudget(max_seconds=0.01)
    llm = BudgetedLLM(_CountingFake(), budget)
    import asyncio
    await asyncio.sleep(0.02)

    with pytest.raises(BudgetExceeded) as e:
        await llm.chat([Message(role="user", content="你好")])
    assert e.value.kind == "time"


async def test_zero_means_unlimited():
    """0 = 不限制（本地脚本/评测要跑满），别把评测脚本也锁死"""
    budget = UsageBudget(max_tokens=0, max_seconds=0)
    llm = BudgetedLLM(_CountingFake(), budget)
    for _ in range(30):
        await llm.chat([Message(role="user", content="你好")])
    assert budget.calls == 30 and budget.exhausted == ""


def test_estimate_tokens_roughly_right():
    """估算只是个量级判断，但别错到离谱（中文按 1.5 字/token 上下）"""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 101                  # 英文约 4 字符/token
    assert 40 <= estimate_tokens("中" * 60) <= 80              # 中文约 1.5 字/token


# ==================== 图上的降级 ====================

def _graph(llm):
    return build_supervisor(llm, ToolRegistry())


async def test_executor_subtasks_degrade_instead_of_crash():
    """一个子任务把额度吃光，不能让整轮 500：每格用降级文案占位，报告照样出来"""
    llm = _BlockAfter([INTENT, PLAN], block_after=2)     # 意图/计划跑完，子任务开始就没额度
    final = await _graph(llm).invoke({"goal": "这周为什么掉量"})

    assert set(final["results"]) == {"t1", "t2"}
    assert all("预算" in v for v in final["results"].values())
    assert final["report"].strip()                        # 有报告，不是空串
    assert "预算" in final["report"]


async def test_non_budget_exception_is_isolated_to_its_subtask():
    """回归：一个子任务抛非预算异常，不能让整轮失败、把其余子任务的结果一起丢掉

    gather 默认把第一个异常立刻抛给调用方，异常穿过 executor_node → AgentGraph.invoke
    直接中断 → synthesize 永远不执行 → 用户看到"分析失败"，而另外的子任务其实已经查好了。
    （用 faulthandler / event loop 实测过：gather 并不会取消兄弟协程，它们照样跑完，
      只是返回值被丢弃、token 白烧 —— 所以这里既验证"不崩"，也验证"结果保住了"。）
    """
    plan = json.dumps([{"id": "t1", "task": "查本周销售额"},
                       {"id": "t2", "task": "查库存（注入失败）"}], ensure_ascii=False)
    llm = _FailOnTask([INTENT, plan, "销售环比上升 12%", "库存积压 300 件"],
                      fail_when="注入失败")
    final = await _graph(llm).invoke({"goal": "这周为什么掉量"})

    assert llm.failed == 1, "标记词没命中，这条测试没测到隔离逻辑"
    assert set(final["results"]) == {"t1", "t2"}                 # 报告结构完整，没丢项
    assert final["results"]["t1"] == "销售环比上升 12%"           # 跑成功的那格必须保住
    assert "失败" in final["results"]["t2"] and "RuntimeError" in final["results"]["t2"]
    assert final["report"].strip()                               # 综合报告照样产出
    # 保住的结果要真的喂进综合报告（不是只躺在 state 里）：能流式的是最后一个调用
    synth_prompt = " ".join(str(getattr(m, "content", "")) for m in llm.calls[-1])
    assert "销售环比上升 12%" in synth_prompt and "失败" in synth_prompt


async def test_synthesizer_fallback_keeps_subtask_results():
    """最该保住数据的情况：子任务全跑完了，只剩综合报告没额度 —— 必须把原始结果给用户"""
    llm = _StreamBlocked([INTENT, PLAN, "销售环比上升 12%", "库存积压 300 件"])
    final = await _graph(llm).invoke({"goal": "这周为什么掉量"})

    assert "销售环比上升 12%" in final["report"] and "库存积压 300 件" in final["report"]
    assert "预算" in final["report"]                       # 让用户知道这是被截断的


async def test_degraded_report_is_marked_so_save_can_skip():
    """兜底报告要打 report_degraded 标记 —— 落盘节点据此跳过（否则残件会被存成「经营周报」）"""
    llm = _StreamBlocked([INTENT, PLAN, "销售环比上升 12%", "库存积压 300 件"])
    final = await _graph(llm).invoke({"goal": "这周为什么掉量"})

    assert final.get("report_degraded") is True, "预算兜底的报告必须打标记"



async def test_role_chain_degrades():
    """内容/客服/文档链路只有一步 LLM：额度没了也不能崩"""
    llm = _BlockAfter(['{"intent": "content"}'], block_after=1)
    final = await _graph(llm).invoke({"goal": "写三条标题"})

    assert final["intent"] == "content"
    assert "预算" in final["report"]


async def test_planner_blocked_gives_empty_plan_not_crash():
    """规划阶段就没额度：不猜任务，直接走兜底报告"""
    llm = _BlockAfter([INTENT], block_after=1)
    final = await _graph(llm).invoke({"goal": "这周为什么掉量"})

    assert final["plan"] == [] and final["results"] == {}
    assert "预算" in final["report"]
