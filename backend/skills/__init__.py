"""技能（Skill）—— 团队预置的流程知识包，按需加载

和工具的区别：工具回答"**能做什么**"（一次原子调用），技能回答"**这件事该怎么做**"
（拆成哪几步、按什么口径、输出要什么形状）。技能不新增任何能力，只固化流程知识 ——
它不是一个"把工具再包一层的盒子"。

为什么要它：planner 每次都在**现编**拆解方案 —— 同一个目标（"出个周报"）这次拆 3 条、
下次拆 5 条，维度漏没漏全看这一次的运气。而"周报该覆盖哪几个维度"是团队早就知道答案的
东西，不该每轮让模型重新发明。

为什么不做成"把知识常驻 prompt"：那等于每加一个技能就永久多几百 token，会和工具 schema
一样越堆越大。这里用的是**渐进披露** ——
- 常驻 planner prompt 的只有元数据：name + 一句话适用场景（一个技能几十个 token）
- 只有 planner 认为命中了某个技能，才把它的**正文**（口径/步骤）拉进 context 重新规划
- 没命中就一个字都不加载

**目录即注册表**：`backend/skills/*.md`，加技能不用改代码。写成文件而不是写进 Python 常量，
是因为技能是"**知识**"不是"**逻辑**" —— 运营想改一条口径，不该需要改代码、跑测试、发版。
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("ecommerce-agent")   # 与 planner/executor 同 logger，日志格式统一

SKILL_DIR = Path(__file__).parent

# front-matter：文件开头 --- 与下一个 --- 之间的 key: value 段；后面全是正文
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


@dataclass
class Skill:
    """一个技能。meta（常驻 prompt）与 body（命中才加载）是分开的，别混用"""
    name: str                       # 唯一标识，planner 输出的就是它
    title: str                      # 中文名，给人看的
    when: str                       # 适用场景（常驻 prompt 的那一句话）
    scopes: list[str] = field(default_factory=list)   # 需要的工具范围（TOOL_SCOPES 的 key，可多个）
    body: str = ""                  # 正文：口径 / 步骤 / 输出要求（命中后才进 context）
    path: Path | None = None


def _parse(text: str) -> tuple[dict, str]:
    """拆成 (front-matter dict, 正文)；没有 front-matter 就当整篇是正文

    只认 `key: value` 这种最朴素的写法，不引 yaml —— 技能的元数据就四个字段，
    为此多装一个依赖不划算。值里带冒号也没问题（按第一个冒号切）。
    """
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, m.group(2)


def load_skills(skill_dir: Path | None = None) -> list[Skill]:
    """扫目录加载技能；缺 name 的文件跳过并告警（半个技能比没有更危险）

    **每次调用都重新读盘**，不缓存：技能是纯文本、量级是"几个"，读盘开销可以忽略；
    加缓存反而会踩"改了口径不生效、非得重启后端"的坑（rag_search 的 BM25 语料
    就吃过这个亏，那边才专门做了指纹重建）。
    """
    d = Path(skill_dir) if skill_dir else SKILL_DIR
    skills: list[Skill] = []
    for path in sorted(d.glob("*.md")):
        meta, body = _parse(path.read_text(encoding="utf-8"))
        name = (meta.get("name") or "").strip()
        if not name:
            logger.warning("技能文件缺少 name（front-matter），已跳过：%s", path.name)
            continue
        skills.append(Skill(
            name=name,
            title=(meta.get("title") or name).strip(),
            when=(meta.get("when") or "").strip(),
            scopes=[s.strip() for s in (meta.get("scopes") or "").split(",") if s.strip()],
            body=body.strip(),
            path=path,
        ))
    return skills


def metadata_text(skills: list[Skill]) -> str:
    """技能元数据（**只有**名字和适用场景，不含正文）—— 常驻 planner prompt 的就是这段"""
    if not skills:
        return "（暂无）"
    return "\n".join(f"- {s.name}（{s.title}）：{s.when}" for s in skills)


def get_skill(skills: list[Skill], name: str) -> Skill | None:
    """按名字取技能；取不到返回 None"""
    for s in skills:
        if s.name == name:
            return s
    return None
