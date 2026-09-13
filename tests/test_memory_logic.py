"""长期记忆的纯逻辑（离线）—— 相似度阈值语义 + 时间口径

merge/recall 需要数据库，这里只钉住它们依赖的两个纯函数：
_ratio 的分档语义（0.9 / 0.45 是设计决策）与 _utcnow 的口径。
"""
from backend.core.memory.store import (
    KIND_EPISODIC, KIND_SEMANTIC, _norm, _ratio, _utcnow,
)


def test_kind_constants():
    assert KIND_SEMANTIC == "semantic" and KIND_EPISODIC == "episodic"


def test_norm_strips_whitespace_and_case():
    assert _norm("  负责 男装  ") == "负责男装"
    assert _norm("ABC") == "abc"


def test_ratio_identical_is_one():
    assert _ratio("负责男装类目", "负责男装类目") == 1.0


def test_ratio_change_falls_in_middle_band():
    """同一偏好的"变化版"应落在 0.45~0.9 → 触发 merge 的"作废重写"分支"""
    r = _ratio("负责男装类目", "负责女装类目")
    assert 0.45 <= r < 0.90, r


def test_ratio_unrelated_is_low():
    """毫不相关应 < 0.45 → 走"全新 append"分支"""
    assert _ratio("负责男装类目", "用户喜欢英文查询") < 0.45


def test_ratio_empty_is_zero():
    assert _ratio("", "负责男装类目") == 0.0


def test_utcnow_is_naive_utc():
    """与 SQLite func.now()（UTC, naive）同口径，避免 tz-aware 比较炸"""
    assert _utcnow().tzinfo is None
