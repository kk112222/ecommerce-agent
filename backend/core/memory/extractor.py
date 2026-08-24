import json
import re

from backend.core.llm.base import Message


class ProfileExtractor:
    """把用户的历史对话提炼"""
    def __init__(self,llm):
        self.llm = llm

    async def extract(self,conversation:str) -> list[str]:
        system_prompt = f"""你是用户画像分析师。根据用户（电商运营人员）的历史对话，提炼这个运营人员的画像，拆成多条短记忆句子。
                每条记忆的规则：
                - 一条一句话、独立成立，去掉"该运营人员""用户"等主语前缀，能直接当语义检索条目
                - 只提炼跨会话稳定的信息：负责的类目/店铺、经营关注点、内容/报告偏好、使用习惯
                - 具体不要空泛（"关注转化率" 而不是 "关心业绩"），每条 15 字以内
                - 不要复述某次对话的内容，只提炼稳定的画像
                - 如果对话里看不出任何用户特征，输出空数组
                只输出一个 JSON，不要任何其他文字：{{"preferences": ["短句1", "短句2", ...]}}"""
        response = await self.llm.chat([
            Message(role="system",content=system_prompt),
            Message(role="user",content=conversation),
        ],temperature=0)
        # 容错三层：剥代码块 → json 解析 → 兜底空列表
        text = re.sub(r'^```(?:json)?|```', '', response.content.strip(), flags=re.M)
        try:
            prefs = json.loads(text).get("preferences",[])
            return prefs if isinstance(prefs, list) else []
        except json.JSONDecodeError:
            return []
