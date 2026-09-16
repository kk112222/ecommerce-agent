import json
import logging
import re
from backend.core.llm.base import Message
from backend.skills import Skill, get_skill, load_skills, metadata_text

logger = logging.getLogger("ecommerce-agent")   # 与 chat.py / supervisor.py 同 logger，日志格式统一


def _parse_json(text: str):
    """剥掉代码块围栏 → json.loads；解析不了返回 None（怎么降级由调用方决定）

    LLM 给 JSON 时经常顺手裹一层 ```json 围栏，或者干脆给一段解释 —— 这里只负责
    "能不能解析"，返回 None 就是"不能"，不在这一层替调用方做决定。
    """
    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M)
    try:
        return json.loads(cleaned)
    except json.decoder.JSONDecodeError:
        return None


class Planner:
    def __init__(self,llm,registry, skills: list[Skill] | None = None):
        self.llm = llm
        self.registry = registry
        # skills=None → 去 backend/skills/*.md 现读（运营改了口径不用重启后端）；
        # 显式传 [] 表示"这个场景不启用技能"（测试隔离用）
        self.skills = load_skills() if skills is None else list(skills)

    async def plan(self, goal: str, history: str = "", user_profile: str = "",
                   uploaded_data: str = "") -> dict:
        """把目标拆成可并行执行的子任务，返回 {"skill": 技能名或 "", "tasks": [子任务…]}

        为什么要返回技能名、而不只是任务列表：技能里写着"这件事该按什么口径做"，
        而 **executor 的能力范围要跟着技能走** —— 技能横跨角色时（周报既要查数据、
        又要把结果落盘成文档），光按"请求走的是分析链路"给范围是给不全的。
        技能名是"这次用的是哪套流程"的唯一凭据，丢了就没法还原能力范围。
        """
        system_prompt = self._with_context(self._prompt(), history, user_profile, uploaded_data)
        messages = [
            Message(role="system",content=system_prompt),
            Message(role="user",content=goal),
        ]
        response = await self.llm.chat(messages,temperature=0.3)
        raw = _parse_json(response.content)

        tasks = self._sanitize(raw.get("tasks") if isinstance(raw, dict) else raw, goal)
        skill = self._pick_skill(raw.get("skill") if isinstance(raw, dict) else "")
        if skill is None:
            return {"skill": "", "tasks": tasks}

        # 渐进披露的第二步：**命中之后**才把技能正文（口径 / 步骤）拉进 context 重新规划。
        # 没命中就一个字都不加载 —— 常驻 prompt 的只有上面那段元数据（几行字）。
        refined = await self._replan_with_skill(goal, skill, history, user_profile, uploaded_data)
        if refined:
            logger.info("命中技能 %s，已按技能口径重新规划：%d 条子任务", skill.name, len(refined))
            return {"skill": skill.name, "tasks": refined}
        # 正文白展开了。这里**当没命中**处理（连工具范围也不放宽），而不是"算命中但用第一轮计划"：
        # 否则会出现"因为技能放开了写文件的权限，任务却是现编的"—— 权限跟着技能走，
        # 而技能实际没生效，两边对不上。宁可退回普通规划。
        logger.warning("技能 %s 加载了但没能规划出可用计划，本次按普通规划走（技能不计入命中）",
                       skill.name)
        return {"skill": "", "tasks": tasks}

    def _with_context(self, prompt: str, history: str = "", user_profile: str = "",
                      uploaded_data: str = "") -> str:
        """把历史 / 画像 / 上传数据挂在 prompt 尾巴上（普通规划和技能规划都要带）"""
        if history:
            prompt += f"\n\n【历史对话背景】\n{history}"
        if user_profile:
            prompt += f"\n\n【用户画像】\n{user_profile}"
        if uploaded_data:
            prompt += f"\n\n【上传数据】\n{uploaded_data}"
        return prompt

    def _prompt(self, skill: Skill | None = None) -> str:
        """规划 prompt。skill 非空时用"技能模式"：把该技能的正文也带进来"""
        if skill is not None:
            return f"""你是任务规划器。当前目标已经命中团队预置的技能，请**严格按技能里写的流程和口径**拆解子任务。

        【命中的技能：{skill.title}】
        {skill.body}

        【可用工具】
        {self._tool_list()}

        【硬规则】
        1. 按技能里列的维度拆子任务（通常就是 2~5 条），每条必须能独立完成、不依赖其他子任务的输出
        2. tool_hint 填【首选工具】（可选，只填一个工具名），判断不出就留空字符串；
           注意它只是提示、不是权限边界
        3. 子任务按"要回答的问题"拆，不按"工具"拆；一条子任务内部可以调用多个工具交叉验证
        4. 技能里写明的**对照项**（比如环比要取上一周期）不能省，它是这条流程的价值所在
        5. 只输出 JSON，不要解释、不要 markdown 代码块

        【输出格式】
        {{"skill": "{skill.name}", "tasks": [
          {{"id": "t1", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}}
        ]}}"""

        return f"""你是任务规划器，负责把用户的电商运营目标拆解成可并行执行的子任务。

        【可用工具】
        {self._tool_list()}

        【可用技能】
        {metadata_text(self.skills)}
        技能是团队预置的流程知识（"这件事该按什么口径做"）。如果目标和某个技能的适用场景对得上，
        就在 skill 字段填**它的名字**（只填名字，不要自己展开它的内容）；对不上就填空字符串。

        【硬规则】
        1. 把目标拆成 2~5 个互相独立的子任务，每个子任务必须能独立完成，不能依赖其他子任务的输出
        2. tool_hint 填【首选工具】（可选，只填一个工具名）：给这条子任务一个"从哪个工具入手"的提示；
           判断不出就留空字符串。注意它只是提示，不是权限边界 —— 子任务拿到的是整个数据分析工具集
        3. 子任务按"要回答的问题"拆，不按"工具"拆：一条子任务内部可以调用多个工具交叉验证
           （例如"为什么销量跌"要先查销售、再查库存、必要时看会员结构）；
           但彼此独立、能并行的不同方向仍然要拆成多条子任务，别把两件事塞进一条
        4. skill 字段填了技能名时，子任务要**按那个技能的口径**拆（系统会另外加载它的详细流程）
        5. 只输出一个 JSON，不要任何解释、不要 markdown 代码块、不要多余文字

        【输出格式】
        {{"skill": "命中的技能名，没命中填空字符串", "tasks": [
          {{"id": "t1", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}},
          {{"id": "t2", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}}
        ]}}"""

    def _tool_list(self) -> str:
        return "\n".join(
            f"- {tool.spec.name}: {tool.spec.description}"
            for tool in self.registry.tools.values()
        )

    def _pick_skill(self, name) -> Skill | None:
        """技能名必须真实存在才认 —— 编造的名字直接忽略 + 告警（和 tool_hint 同一套处理）"""
        name = str(name or "").strip()
        if not name:
            return None
        skill = get_skill(self.skills, name)
        if skill is None:
            logger.warning("planner 命中了一个不存在的技能名，已忽略：%r（可用技能：%s）",
                           name, [s.name for s in self.skills])
        return skill

    async def _replan_with_skill(self, goal: str, skill: Skill, history: str,
                                 user_profile: str, uploaded_data: str) -> list[dict] | None:
        """带着技能正文重新规划一次；输出不可用返回 None，由调用方回退到第一轮计划

        注意这里**不吞异常**：预算超限（BudgetExceeded）要照旧往上抛给 supervisor 降级，
        统一在这一层吞掉会让"预算超了"变成"计划悄悄退回第一轮"，链路上没人知道。
        """
        system_prompt = self._with_context(self._prompt(skill=skill),
                                           history, user_profile, uploaded_data)
        response = await self.llm.chat(
            [Message(role="system", content=system_prompt), Message(role="user", content=goal)],
            temperature=0.2,                       # 已经给了明确流程，压低随机性
        )
        raw = _parse_json(response.content)
        if raw is None:
            return None
        tasks = self._sanitize(raw.get("tasks") if isinstance(raw, dict) else raw, goal)
        # 只有一条、内容就是目标原文、也没有提示 → 这是 _sanitize 的兜底形状，不是"按技能规划"的结果
        if len(tasks) == 1 and tasks[0]["task"] == goal and not tasks[0]["tool_hint"]:
            return None
        return tasks

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