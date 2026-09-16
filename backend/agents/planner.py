import json
import logging
import re
from backend.core.llm.base import Message

logger = logging.getLogger("ecommerce-agent")   # 与 chat.py / supervisor.py 同 logger，日志格式统一


class Planner:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry

    async def plan(self, goal: str, history: str = "", user_profile: str = "",
                   uploaded_data: str = "") -> list[dict]:
        tool_list = "\n".join(
            f"- {tool.spec.name}: {tool.spec.description}"
            for tool in self.registry.tools.values()
        )
        system_prompt = f"""你是任务规划器，负责把用户的电商运营目标拆解成可并行执行的子任务。

        【可用工具】
        {tool_list}

        【硬规则】
        1. 把目标拆成 2~5 个互相独立的子任务，每个子任务必须能独立完成，不能依赖其他子任务的输出
        2. tool_hint 填【首选工具】（可选，只填一个工具名）：给这条子任务一个"从哪个工具入手"的提示；
           判断不出就留空字符串。注意它只是提示，不是权限边界 —— 子任务拿到的是整个数据分析工具集
        3. 子任务按"要回答的问题"拆，不按"工具"拆：一条子任务内部可以调用多个工具交叉验证
           （例如"为什么销量跌"要先查销售、再查库存、必要时看会员结构）；
           但彼此独立、能并行的不同方向仍然要拆成多条子任务，别把两件事塞进一条
        4. 只输出一个 JSON 数组，不要任何解释、不要 markdown 代码块、不要多余文字

        【输出格式】
        [
          {{"id": "t1", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}},
          {{"id": "t2", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}}
        ]"""
        if history:
            system_prompt += f"\n\n【历史对话背景】\n{history}"
        if user_profile:
            system_prompt += f"\n\n【用户画像】\n{user_profile}"
        if uploaded_data:
            system_prompt += f"\n\n【上传数据】\n{uploaded_data}"
        messages = [
            Message(role="system",content=system_prompt),
            Message(role="user",content=goal),
        ]
        response = await self.llm.chat(messages,temperature=0.3)
        text = re.sub(r'^```(?:json)?|```$', '', response.content.strip(), flags=re.M)
        try:
            plan = json.loads(text)
        except json.decoder.JSONDecodeError:
            plan = None

        return self._sanitize(plan, goal)

    MAX_TASKS = 5

    def _sanitize(self, plan, goal: str) -> list[dict]:
        """把 LLM 输出的计划校准成"下游一定能用"的形状（P2-13）

        LLM 会输出各种不合法形状：不是 list、缺 task、id 重复/缺失、tool_hint 编造工具名。
        以前只判了 isinstance(list)，下游 `state["results"] = {t["id"]: r for t, r in zip(plan, ...)}`
        一旦 id 重复就会把两条子任务的结果折叠成一条（静默丢结果），缺 task 直接 KeyError。
        这里逐条补齐 + 去重 + 校验，保证：id 唯一、task 非空、tool_hint 只可能是**单个**真工具名或空。

        为什么 hint 仍然卡"单个合法工具名"：它现在的语义是【首选工具提示】而不是权限边界，
        所以填错/填多个**不会**削掉能力（子任务拿的是角色范围）；但脏值会让模型看到一句无意义的
        提示，也让"planner 有没有守约定"失去观测点 —— 清空 + 告警是为了提示质量与可观测性。
        """
        bad = (not isinstance(plan, list)) or not plan
        if not bad:
            # 全是非 dict / 没有一条带有效 task → 视为废输出
            bad = not any(isinstance(it, dict) and str(it.get("task") or "").strip() for it in plan)
        if bad:
            # 兜底降级成"把用户目标本身当一个子任务"（原来的 id 是 "tl"，明显是 t1 的笔误，
            # 既不好读也会和真 t1 撞车）
            return [{"id": "t1", "task": goal, "tool_hint": ""}]

        cleaned, seen_ids = [], set()
        for i, item in enumerate(plan[:self.MAX_TASKS]):
            if not isinstance(item, dict):
                continue
            task_text = str(item.get("task") or "").strip()
            if not task_text:
                continue
            tid = str(item.get("id") or "").strip() or f"t{i + 1}"
            if tid in seen_ids:                      # id 重复 → 重编，避免结果被折叠
                # 候选名必须每次循环都往前推：seen_ids 只在循环外 add，写成
                # `while tid in seen_ids: tid = f"t{len(seen_ids) + 1}"` 的话 len() 是常量，
                # 候选名固定不变 → 一旦被占就原地自旋，同步 CPU 跑在请求路径上会堵死整个事件循环
                n = len(seen_ids) + 1
                while f"t{n}" in seen_ids:
                    n += 1
                tid = f"t{n}"
            seen_ids.add(tid)
            hint = str(item.get("tool_hint") or "").strip()
            if hint and hint not in self.registry.tools:
                # 命中两种情况：编造的工具名、以及"一条子任务填了多个工具名"（首选工具只该有一个）。
                # 两者都不会削能力（子任务拿的是角色范围），但会让提示变成噪音、也失去合规观测点
                logger.warning("planner 的 tool_hint 不是单个合法工具名，已清空（只影响提示质量）："
                               "%r（子任务 %s）", hint, tid)
                hint = ""
            cleaned.append({
                "id": tid, "task": task_text,
                "tool_hint": hint,
            })
        return cleaned or [{"id": "t1", "task": goal, "tool_hint": ""}]