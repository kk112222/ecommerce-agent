import json
import re
from backend.core.llm.base import Message

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
        2. 每个子任务尽量匹配一个最合适的工具名填在 tool_hint；不确定就留空字符串
        3. 只输出一个 JSON 数组，不要任何解释、不要 markdown 代码块、不要多余文字

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
        这里逐条补齐 + 去重 + 校验，保证：id 唯一、task 非空、tool_hint 只可能是真工具名或空。
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
            cleaned.append({
                "id": tid, "task": task_text,
                "tool_hint": hint if hint in self.registry.tools else "",   # 编造的工具名要清掉
            })
        return cleaned or [{"id": "t1", "task": goal, "tool_hint": ""}]