"""意图分类器：结构化输出容错 + 兜底（离线，假 LLM）

覆盖真实 LLM 会犯的错：加 ```json 围栏、返回非 JSON、返回不在枚举里的值。
"""
import pytest

from backend.agents.intent_classifier import INTENTS, IntentClassifier
from tests.conftest import FakeLLM


def test_intents_include_document():
    """四条链路都要在枚举里（新增 document 后别漏）"""
    assert set(INTENTS) == {"analysis", "content", "service", "document"}


@pytest.mark.parametrize("raw, expected", [
    ('{"intent": "document"}', "document"),                       # 标准
    ('```json\n{"intent": "content"}\n```', "content"),           # 带 markdown 围栏
    ('{"intent": "service"}', "service"),
    ("这不是 JSON", "analysis"),                                   # 解析失败 → 兜底 analysis
    ('{"intent": "unknown_xxx"}', "analysis"),                    # 不在枚举 → 兜底 analysis
    ('{"intent": "analysis"}', "analysis"),
])
async def test_classify_tolerance(raw, expected):
    clf = IntentClassifier(FakeLLM([raw]))
    assert await clf.classify("随便一个问题") == expected


async def test_user_message_carries_goal():
    """踩过的坑：目标必须在 user 消息里，不能塞进 system（否则分类跑偏）"""
    llm = FakeLLM(['{"intent": "analysis"}'])
    await IntentClassifier(llm).classify("这周营业额为什么低")
    last = llm.calls[0][-1]
    assert last.role == "user" and last.content == "这周营业额为什么低"
