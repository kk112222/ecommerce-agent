"""回答层评测的两个确定性判据（离线，纯函数）

P3-18 的评测脚本要调真 LLM（答案生成 + judge），没有额度就跑不动。
但其中两条判据是纯计算，不该跟着 LLM 一起"没额度就没人管"：
  - 数字越界：答案里的数字是否都能在召回片段里逐字找到（幻觉筛查）
  - 包邮门槛抽取：扰动测试靠它判断"答案有没有跟着知识库变"
这里把它们钉住，跑评测前先保证尺子本身是准的。
"""
from scripts.rag_answer_eval import free_shipping_threshold, numeric_overflows

CHUNKS = "订单满 99 元包邮。不满 99 元：普通快递 8 元，顺丰 18 元。偏远地区加收 15 元附加费。"


# ==================== 数字越界（幻觉筛查） ====================

def test_grounded_numbers_pass():
    """答案里的数字都能在片段里找到 → 没有越界"""
    answer = "满 99 元包邮，普通快递 8 元，顺丰 18 元。"
    assert numeric_overflows(answer, CHUNKS) == []


def test_hallucinated_number_is_flagged():
    """凭空多出来的数字必须被抓出来 —— 这就是幻觉最典型的形态"""
    answer = "满 99 元包邮，偏远地区加收 20 元附加费。"
    assert numeric_overflows(answer, CHUNKS) == ["20"]


def test_substring_of_chunk_number_counts_as_present():
    """3 出现在片段的 '3-7 个工作日' 里就算有据 —— 用子串而非词匹配，避免误报"""
    assert numeric_overflows("大概 3 天", "清关 3-7 个工作日") == []


def test_derived_number_is_false_positive():
    """已知边界：答案自己算出来的合计数会被误报（8+18 算出来的 26 片段里没有）。

    子串匹配是**故意偏松**的：把 10000 写成"1 万"不会误报（"1" 就在片段里），
    但自己动手算的数会 —— 所以脚本输出的是"待人工复核"而不是直接扣分。
    """
    assert numeric_overflows("两件合计 26 元", "普通快递 8 元，顺丰 18 元") == ["26"]
    assert numeric_overflows("相当于 1 万元", "门槛 10000 元") == []   # 偏松的一侧


# ==================== 包邮门槛抽取（扰动测试的判据） ====================

def test_extract_plain_threshold():
    assert free_shipping_threshold("订单满 199 元包邮") == "199"


def test_extract_with_markdown_and_rewording():
    """真实回答会加粗、会改写：不能依赖字面短语匹配（第一版断言就栽在这）"""
    answer = "国内普通地区订单满 **199 元** 即可享受包邮服务。"
    assert free_shipping_threshold(answer) == "199"


def test_extract_returns_none_when_absent():
    """答非所问时返回 None，让扰动测试明确"失去参照"而不是误判为通过"""
    assert free_shipping_threshold("您好，请问有什么可以帮您？") is None
