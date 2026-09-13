"""意图分类器 —— 判断用户问题属于哪条链路，决定 supervisor 走哪条路"""
import json
import re
from backend.core.llm.base import Message

# 四类意图：analysis 数据分析 | content 内容生成 | service 客服问答 | document 文档处理
INTENTS = ["analysis", "content", "service", "document"]

class IntentClassifier:
    def __init__(self, llm):
        self.llm = llm

    async def classify(self, goal: str) -> str:
        """返回意图：analysis / content / service / document"""
        system_prompt = f"""你是电商系统的意图分类器。判断用户问题属于下面四类中的哪一类：

1. analysis 数据分析：查销售/库存/会员/商品数据，做经营分析（如"这周营业额为什么低""查下苹果15库存""会员消费怎么样"）
2. content 内容生成：写商品文案、标题、营销话术（如"给苹果15写三条淘宝标题""生成朋友圈推广文案"）
3. service 客服问答：问售后政策、退货规则等，需要查知识库（如"退货要多久""运费谁出"）
4. document 文档处理：处理/优化/生成文档文件（如"把这文档精简一下""整理成要点""生成一份周报存下来""转成 docx"）

只输出一个 JSON，不要任何其他文字：{{"intent": "analysis"}}"""
        response = await self.llm.chat(
            [
                Message(role="system", content=system_prompt),
                Message(role="user", content=goal),   # user 消息必须放目标（踩过的坑！）
            ],
            temperature=0,
        )
        # 结构化输出容错：剥 markdown 代码块 → json 解析 → 兜底
        text = re.sub(r'^```(?:json)?|```', '', response.content.strip(), flags=re.M)
        try:
            intent = json.loads(text).get("intent", "analysis")
        except json.JSONDecodeError:
            intent = "analysis"
        if intent not in INTENTS:
            intent = "analysis"   # 分类失败默认走数据分析链路
        return intent
