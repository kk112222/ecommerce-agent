
import logging
from datetime import datetime
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent

logger = logging.getLogger("ecommerce-agent")   # 与 chat.py / planner.py 同 logger


class Executor:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry
    async def run(self, task: dict, uploaded_data: str = "", user_profile: str = "",
                  scopes: list[str] | None = None) -> str:
        """跑一个子任务。

        - uploaded_data / user_profile：以前没有 → "结合上传的竞品数据分析"这类子任务
          手里根本没有竞品数据（文档链路能拿到，分析链路拿不到，是断链）
        - tool_hint：**只是"从哪个工具入手"的提示，不是权限边界**（2026-09-16 调整）
        - scopes：这次子任务能用的工具范围（TOOL_SCOPES 的名字，可多个取并集）。
          不传 = 走默认的 analysis 角色范围；命中技能时由**技能**声明它要哪几个范围。

        能力边界按**角色**划、不按单条子任务划，原因：
        - 子任务的语义是"回答一个问题"，它天然可能要用多个工具（查销售 → 发现跌 → 再查库存、
          必要时看会员结构）；按 hint 锁成单工具时，planner 拆得不够细就会**静默给出残缺结论**
          （不报错、报告看着还挺完整，比"越界调用"更难发现）；
        - 业界做法是角色级白名单（"只读规划者""只读代码浏览者"这类 agent 类型各自一份最小工具集），
          角色内部自由选择工具，只有跨角色才拦；
        - 所以这里给子任务的是 analysis 角色的 5 个数据工具：职责隔离仍在（写文件、发文案的工具
          根本不在 schema 里），但同角色内可以交叉验证。
        """
        # ① 构造"子任务专属"system prompt
        today = datetime.now().strftime("%Y-%m-%d")  # 算今天
        hint = (task.get("tool_hint") or "").strip()
        allowed = hint if hint in self.registry.tools else ""     # 非法/为空 → 不给提示（不影响权限）
        if hint and not allowed:
            # planner 那边已经会清空非法 hint 并告警，走到这里说明是别的调用方直接给的
            logger.warning("tool_hint 不是合法工具名，本次忽略该提示：%r（子任务 %s）",
                           hint, task.get("id"))
        system_prompt = f"""你是数据分析子任务执行者,今天是{today}。你被分配了一个明确的子任务，只完成它，不要跑题。

        【你的子任务】
        {task['task']}

        【工作方式】
        1. 先判断要查哪些数据，调用对应工具收集
        2. 如果一次数据不够下结论，继续调用工具补充，直到数据齐全
        3. 数据收集完，给出简明结论

        【输出要求】
        - 用 200 字以内总结：查到了什么 + 发现了什么问题
        - 直接给结论和发现，不要复述调用过程"""
        if allowed:
            # 从"指定工具"改成"建议优先使用"：它是起点提示，不是权限——同角色的其它工具照样可调
            system_prompt += (f"\n\n【建议优先使用】{allowed}"
                              "（也可以调用其它数据分析工具交叉验证，例如查完销售再确认库存）。")
        if user_profile:
            system_prompt += f"\n\n【用户画像】\n{user_profile}"
        if uploaded_data:
            system_prompt += f"\n\n【上传数据】\n{uploaded_data}"
        # ② 复用 ReActAgent 跑一个独立 ReAct 循环
        # 拿的是**范围**（默认 analysis 角色，命中技能时按技能声明的范围并集），不是单个工具。
        # 边界仍然由代码里的白名单定死：模型能选的只有"用哪个技能"，选不了"要多大权限"。
        registry = self.registry.subset_scopes(scopes or ["analysis"])
        agent = ReActAgent(self.llm, registry)
        result = await agent.run([Message(role="system",content=system_prompt)])
        # ③ 空结果兜底：LLM 偶发返回空串，不能让空结果流到综合报告
        if not result or not result.strip():
            return "该子任务未获取到数据：工具查询无结果或执行器未返回内容。"
        return result