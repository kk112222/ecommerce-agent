from backend.core.llm.base import Message


# 可视化输出规则（独立普通字符串常量，避免塞进 f-string 要转义花括号）
# Synthesizer 可选：报告末尾附一个 ```viz 代码块（内嵌图表 JSON），前端识别渲染成 plots 图表/表格
VIZ_RULES = """6. 【可选·数据可视化】若某条结论适合用图或表呈现（趋势 / 分类对比 / 明细罗列），在报告【最末尾】追加且只追加一个可视化代码块，格式严格如下：
```viz
{"type": "line", "title": "近7日销售额趋势", "data": [{"x": "08-28", "y": 12000}, {"x": "08-29", "y": 15600}]}
```
- type 只允许：line(趋势线/面积)、bar(横向分类对比)、column(纵向分类并排)、table(明细罗列)
- 图统一用 {"x": 类目/日期, "y": 数值}，table 的 data 用 [{"列名": 值}, ...]，数据行数 ≤ 15，金额单位与正文一致
- 数值必须【逐字照抄】上方【各子任务调查结果】里出现过的数字，禁止自行计算/估算/编造；抄不准或没把握就不加 viz 块，宁可纯文字
- 正文结论必须仍用文字讲清，图/表只是辅助呈现，不能替代正文"""


class Synthesizer:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry
    def _build_prompt(self, goal: str, results: dict[str, str], history: str = "",
                      user_profile: str = "", uploaded_data: str = "") -> str:
        parts = []
        for tid, text in results.items():
            parts.append(f"【子任务 {tid}】\n{text}")
        results_text = "\n\n".join(parts)

        # 有内容才拼对应背景段（空串不污染 prompt）
        history_block = ""
        if history:
            history_block = f"\n\n【历史对话背景】\n{history}"
        profile_block = ""
        if user_profile:
            profile_block = f"\n\n【用户画像】\n{user_profile}"
        uploaded_block = ""
        if uploaded_data:
            uploaded_block = f"\n\n【上传数据】\n{uploaded_data}"

        return f"""你是电商运营分析师。下面是针对一个运营目标的多份子任务调查结果，请综合分析，输出完整报告。
        {history_block}{profile_block}{uploaded_block}
        【原始目标】
        {goal}
        【各子任务调查结果】
        {results_text}
        【输出要求】
        1. 先正面回答原始目标（比如"本周营业额低"的主要原因是什么）
        2. 用数据支撑，并交叉对比多个子任务结果（销售、库存、会员等互相印证，别各说各的）
        3. 如果提供了上传数据（如竞品价格/行业数据），要和自己的经营数据做对比分析
        4. 最后给 2~3 条可执行的改进建议
        5. 用 markdown 分点结构，清晰易读
        {VIZ_RULES}"""
    async def synthesize(self, goal: str, results: dict[str, str], history: str = "",
                         user_profile: str = "", uploaded_data: str = "") -> str:
        response = await self.llm.chat([Message(role="system", content=self._build_prompt(
            goal, results, history, user_profile, uploaded_data))])
        return response.content
    async def synthesize_stream(self, goal: str, results: dict[str, str], history: str = "",
                                user_profile: str = "", uploaded_data: str = ""):
        """流式版：逐 token 产出报告，用于 SSE 打字机效果"""
        async for chunk in self.llm.chat_stream([Message(role="system", content=self._build_prompt(
                goal, results, history, user_profile, uploaded_data))]):
            yield chunk
