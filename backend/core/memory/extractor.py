"""记忆抽取器 —— 把一轮对话拆成两类长期记忆

改造（外部评审 M1）：旧实现只喂"用户：{goal}"（孤立问题），看不到助手答了什么、用户认不认可，
而稳定画像恰恰藏在行为信号里（追问=感兴趣、否定=不感兴趣）。现在输入是【整轮】：用户问 + 助手答。
一次 LLM 调用产出两类：
- semantic   语义画像：跨会话稳定的偏好（负责类目/关注 KPI/内容偏好），合并旧画像输出全量当前画像
- episodic   情景记忆："发生过什么"（带时间/数值的事实、新动向、待跟进、明确不关注的方向），
             可过期；写入端 append + 去重即可，不用合并旧全量
"""
import json
import re

from backend.core.llm.base import Message


class ProfileExtractor:
    """把用户的历史对话提炼"""
    def __init__(self, llm):
        self.llm = llm

    async def extract(self, conversation: str, old_profile: str = "") -> dict:
        """返回 {"semantic": [短句...], "episodic": [短句...]}，解析失败两类都回空表"""
        prompt = (
            "你是用户画像分析师。输入是一轮真实对话（【用户问题】+【助手回答】）。\n"
            "根据对话里暴露的【用户行为信号】提炼两类记忆，只输出 JSON，不要任何其他文字。\n\n"
            "一、semantic（语义画像）—— 跨会话稳定的画像。\n"
            "已有的画像（要和本次合并，不要丢弃仍然成立的部分）：\n"
            + (old_profile or "（暂无）") + "\n"
            "把【已有画像】与【本轮新信息】合并成当前最完整的语义画像。规则：\n"
            "- 一条一句话、独立成立，去掉'该运营人员''用户'等主语前缀，可直接当语义检索条目\n"
            "- 保留已有画像里仍然成立的信息；新旧冲突以新为准；每条 15 字以内\n"
            "- 只提炼稳定特征：负责的类目/店铺、经营关注点、内容/报告偏好、使用习惯\n"
            "- 具体不空泛（'关注转化率'而不是'关心业绩'）\n\n"
            "二、episodic（情景记忆）—— 发生过什么，随对话发生会变化的。\n"
            "- 本轮用户提到的指标数值/时间性事实/动作/新动向/待跟进（如'男装转化率这周掉到 2%'）\n"
            "- 用户明确否定/不想做的方向，记为一条'不关注 X / 放弃 X'（不感兴趣也是信号）\n"
            "- 一条一短句、独立成立；不要放稳定的语义画像（负责类目等放 semantic）\n\n"
            "两类都只提炼关于用户的事实，不要复述助手自己的输出。看不出新特征就返回空数组。\n"
            '只输出一行 JSON：{"semantic": ["短句1", "短句2", ...], "episodic": ["短句1", ...]}'
        )
        response = await self.llm.chat([
            Message(role="system", content=prompt),
            Message(role="user", content=conversation or "（空对话）"),
        ], temperature=0)
        # 容错三层：剥代码块 → json 解析 → 兜底空列表
        text = re.sub(r'^```(?:json)?|```', '', response.content.strip(), flags=re.M)
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            return {"semantic": [], "episodic": []}
        return {
            "semantic": d.get("semantic") if isinstance(d.get("semantic"), list) else [],
            "episodic": d.get("episodic") if isinstance(d.get("episodic"), list) else [],
        }
