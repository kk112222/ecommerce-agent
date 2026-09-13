import difflib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from backend.core.llm.base import Message
from backend.db.models.chat_message import ChatMessage
from backend.db.models.chat_session import ChatSession
from backend.db.models.user_profile import UserProfile
from backend.db.models.uploaded_doc import UploadedDoc
from backend.db.models.user_memory import LongTermMemory
from backend.db.session import AsyncSessionLocal
from backend.infrastructure.vector_store.embeddings import embed_text, embed_batch
from backend.infrastructure.vector_store.qdrant_client import (
    ensure_collection, upsert_docs, search_similar, delete_points, list_point_ids,
)
from sqlalchemy import select, func, delete, update

MEMORY_COLLECTION = "user_memories"  # 用户记忆的 qdrant collection（和知识库 kb_docs 分开）

logger = logging.getLogger(__name__)


async def save_message(sid: str, user_id: int, role: str, content: str) -> None:
    """存一条消息到 chat_messages 表（会话历史持久化）"""
    async with AsyncSessionLocal() as db:
        db.add(ChatMessage(session_id=sid, user_id=user_id, role=role, content=content))
        await db.commit()


async def load_messages(sid: str, user_id: int) -> list[Message]:
    """读某个会话的历史消息（按时间升序），供未来多轮上下文/个性化记忆使用"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == sid,
                ChatMessage.user_id == user_id,
            ).order_by(ChatMessage.id)
        )).scalars().all()
        return [Message(role=r.role, content=r.content) for r in rows]
# 历史注入的上下文预算（字）：超过就从最旧的整条消息丢起，保住最近几轮完整对话
MAX_HISTORY_CHARS = 8000

async def get_messages(sid: str, user_id: int, limit: int = 10) -> str:
    history = await load_messages(sid, user_id)
    if not history:
        return ""
    recent = history[-limit:]
    lines = []
    for msg in recent:
        who = "用户" if msg.role == "user" else "助手"
        lines.append(f"{who}: {msg.content}")
    text = "\n".join(lines)
    # 超预算按整条丢（不砍半条），宁可少带几轮也别截断消息内容
    while len(text) > MAX_HISTORY_CHARS and len(lines) > 1:
        lines.pop(0)
        text = "\n".join(lines)
    return text
async def get_user_profile(user_id:int) -> str:
    #获取用户偏好
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
            )
        )).scalar_one_or_none()
        return row.preferences if row else ""

async def update_user_profile(user_id:int,preferences:str) -> None:
    """更新用户偏好"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
            )
        )).scalar_one_or_none()
        if row:
            row.preferences = preferences
        else:
            db.add(UserProfile(user_id=user_id,preferences=preferences))
        await db.commit()


# ==================== 长期记忆：语义画像 + 情景记忆（增量式） ====================
# 外部评审改造核心（对旧"删光重建"）：
# - 写路径不再 delete_by_user 整组清空 → merge 增量对齐：同义刷新 / 变化作废重写 / 全新 append
# - 每条落 SQLite 一行（long_term_memories，事实源），qdrant 只当向量召回索引 → 能逐条删/管理
# - 补齐情景记忆层（episodic），和语义画像同表 kind 区分；读取按 相似×重要×新鲜度 加权

KIND_SEMANTIC = "semantic"    # 稳定画像：负责类目/关注 KPI/偏好
KIND_EPISODIC = "episodic"    # 情景记忆：发生过的事/带时间的事实/新动向（可过期）

# 情景记忆默认存活天数（P2-10）："上周做了什么"过一阵就不再影响判断，
# 不设 TTL 的话它会无限堆积、长期污染召回。`_active_rows` 已经在读过期过滤，这里只管写。
EPISODIC_TTL_DAYS = 30


def _norm(s: str) -> str:
    """归一化：去空白 + 小写，让相似度比较不被空格/大小写干扰"""
    return re.sub(r"\s+", "", s or "").lower()


def _utcnow() -> datetime:
    """naive UTC 当前时间 —— 与 SQLite func.now()(UTC) 同口径，避免用将被弃用的 datetime.utcnow"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ratio(a: str, b: str) -> float:
    """中文字面相似度（difflib 标准库，不引包）。1=完全一致，0=毫不相关"""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# 单轮"从缺席推断作废"的比例上限（P1-4 护栏）：超过就整批不作废
RETIRE_GUARD_RATIO = 0.5
# 低于这个条数时比例没有统计意义（1 条里作废 1 条永远是 100%），不套护栏
RETIRE_GUARD_MIN_ROWS = 3

_JUDGE_PROMPT = """下面每组是【已记住的旧偏好】和【本轮新出现的句子】，请逐组判断关系：
- update：新句是旧句的**更新版**，同一件事变了、新句取代旧句（如"负责男装"→"负责女装"）
- parallel：两句是**并列的不同事实**，都成立、互不取代（如"关注退货率"与"关注退款率"）

只输出 JSON 字符串数组，长度与组数一致，元素只能是 "update" 或 "parallel"，不要任何解释。
例如：["update","parallel"]

{pairs}"""


async def _judge_same_preference(llm, pairs: list[tuple[str, str]]) -> list[str] | None:
    """让 LLM 判 0.45~0.9 歧义档：同一偏好的更新 vs 两条并列偏好

    为什么非它不可：difflib 实测分不出这两类 ——
    "负责男装类目 vs 负责女装类目"=0.833（真更新）、"关注退货率 vs 关注退款率"=0.800（并列），
    字面相似度几乎一样，语义判定只能交给模型。判不了就返回 None，调用方降级回字面判据。
    """
    if llm is None or not pairs:
        return None
    block = "\n".join(
        f"第{i + 1}组：\n旧：{old}\n新：{new}" for i, (new, old) in enumerate(pairs)
    )
    try:
        resp = await llm.chat([Message(role="user", content=_JUDGE_PROMPT.format(pairs=block))],
                              temperature=0.0)
        raw = (resp.content or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        verdicts = json.loads(raw)
        if (isinstance(verdicts, list) and len(verdicts) == len(pairs)
                and all(v in ("update", "parallel") for v in verdicts)):
            return verdicts
        logger.warning("记忆合并判定返回格式异常，降级回字面判据：%s", raw[:120])
    except Exception:
        logger.exception("记忆合并判定失败，降级回字面判据")
    return None


async def _embed_chunked(texts: list[str], size: int = 8) -> list[list[float]]:
    """批量向量化（DashScope 单批 ≤10，本地再压到 8 保险）"""
    out: list[list[float]] = []
    for i in range(0, len(texts), size):
        out.extend(await embed_batch(texts[i:i + size]))
    return out


async def _active_rows(user_id: int, kind: str | None = None) -> list[LongTermMemory]:
    """该用户生效中的长期记忆行（可只看某一类）；expires 已过的当失效，不返回"""
    now = _utcnow()
    async with AsyncSessionLocal() as db:
        q = select(LongTermMemory).where(
            LongTermMemory.user_id == user_id,
            LongTermMemory.active.is_(True),
            (LongTermMemory.expires_at.is_(None)) | (LongTermMemory.expires_at > now),
        )
        if kind:
            q = q.where(LongTermMemory.kind == kind)
        return list((await db.execute(q.order_by(LongTermMemory.id))).scalars().all())


async def semantic_snapshot(user_id: int) -> str:
    """当前语义画像快照（合并提炼的底子 / 兜底）—— 生效中的 semantic 行编号拼串"""
    rows = await _active_rows(user_id, KIND_SEMANTIC)
    if not rows:
        return await get_user_profile(user_id)   # 空时退一句话画像表
    return "\n".join(f"{i + 1}. {r.text}" for i, r in enumerate(rows))


async def _sync_profile_fallback(user_id: int) -> None:
    """同步一句话画像表（兜底用）：由生效中的 semantic 行重算，保证两处一致"""
    rows = await _active_rows(user_id, KIND_SEMANTIC)
    await update_user_profile(user_id, "；".join(r.text for r in rows))


async def merge_semantic_memories(user_id: int, new_sentences: list[str],
                                  source_session: str | None = None,
                                  llm=None) -> dict:
    """增量合并语义画像（核心写路径，取代"删光重建"）

    把 LLM 输出的【当前全量画像】和库里生效中的旧画像逐条比对：
    - 相似度 ≥ 0.9   同一条偏好 → 只刷新热度（importance+1 / last_access）
    - 0.45 ~ 0.9    歧义档：交 LLM 判"同一偏好的更新"（作废旧行重写）还是"两条并列偏好"
                    （旧行原样保留，两句都生效）—— 判不了/没给 llm 时降级回"当作更新"
    - < 0.45        全新偏好 → append
    - 旧行没被任何新句认领 = LLM 判定不再成立 → 作废（active=False，删其向量，保留行可审计）
      但这条是**从缺席推断**的（LLM 少写一条、输出被截断都会触发），
      所以加了比例护栏：未认领比例过高时整批不作废，宁可多留也不静默丢真记忆。
    """
    clean = [s.strip() for s in (new_sentences or []) if s and s.strip()]
    if not clean:
        return {"added": 0, "changed": 0, "kept": 0, "retired": 0,
                "parallel": 0, "guard": False}
    ensure_collection(MEMORY_COLLECTION)

    old = await _active_rows(user_id, KIND_SEMANTIC)
    used = [False] * len(old)          # 每条旧行最多被认领一次
    keep_ids, retire = [], []          # 刷新 / 作废（含被"变化版"替换的）
    add_texts: list[str] = []
    n_new = n_changed = n_parallel = 0
    fuzzy: list[tuple[str, int]] = []  # 歧义档：(新句, 旧行下标)，等 LLM 统一判定

    for s in clean:
        best_i, best_r = -1, 0.0
        for i, row in enumerate(old):
            if used[i]:
                continue
            r = _ratio(s, row.text)
            if r > best_r:
                best_i, best_r = i, r
        if best_i >= 0 and best_r >= 0.90:
            used[best_i] = True
            keep_ids.append(old[best_i].id)
        elif best_i >= 0 and best_r >= 0.45:
            used[best_i] = True                  # 先占位，避免同一旧行被两条新句抢
            fuzzy.append((s, best_i))
        else:
            add_texts.append(s)
            n_new += 1

    # 歧义档一次 LLM 调用批量判定（省成本：整轮合并只多花一次调用）
    if fuzzy:
        verdicts = await _judge_same_preference(llm, [(s, old[i].text) for s, i in fuzzy])
        for idx, (s, i) in enumerate(fuzzy):
            if verdicts and verdicts[idx] == "parallel":
                # 两条并列事实：旧行原样保留（不刷新热度），新句单独落一条
                add_texts.append(s)
                n_parallel += 1
            else:
                retire.append(old[i])            # 降级路径 = 原行为：当作同一偏好变了
                add_texts.append(s)
                n_changed += 1

    unclaimed = [old[i] for i in range(len(old)) if not used[i]]
    guard = len(old) >= RETIRE_GUARD_MIN_ROWS and len(unclaimed) > len(old) * RETIRE_GUARD_RATIO
    if guard:
        # 从缺席推断作废本来就不可靠，本轮大面积未认领更像是"LLM 输出不全"而不是"偏好都变了"
        logger.warning("记忆合并护栏触发：%d/%d 条旧记忆未被本轮认领，本轮不作废（防静默丢记忆）",
                       len(unclaimed), len(old))
    else:
        retire.extend(unclaimed)                 # 没被认领 = 判定不再成立 → 作废

    # ① 新文本向量化（API 失败留空 → 该行 qdrant_point_id=None，仍保留文本、可走画像兜底）
    vecs = await _embed_chunked(add_texts)
    payload_ts = datetime.now().isoformat(timespec="seconds")
    now = _utcnow()
    new_points = []                              # (text, point_id or None)
    for text, v in zip(add_texts, vecs):
        if not v:
            new_points.append((text, None))
        else:
            new_points.append((text, str(uuid.uuid4())))

    # ② qdrant：删作废向量 + 插新向量（payload 带 kind/created_at 便于排查）
    del_ids = [r.qdrant_point_id for r in retire if r.qdrant_point_id]
    if del_ids:
        delete_points(MEMORY_COLLECTION, del_ids)
    docs = [{"id": pid, "vector": vecs[i],
             "payload": {"text": text, "user_id": user_id, "created_at": payload_ts,
                         "kind": KIND_SEMANTIC}}
            for i, (text, pid) in enumerate(new_points) if pid]
    if docs:
        upsert_docs(docs, collection_name=MEMORY_COLLECTION)

    # ③ SQLite：刷新命中条 / 作废 retire / 插新行（一次事务）
    # 注意 retire 行是上个 session 读出的脱管对象，直接改属性不落库 → 用 id 批量 UPDATE
    retire_ids = [r.id for r in retire]
    async with AsyncSessionLocal() as db:
        for kid in keep_ids:
            row = await db.get(LongTermMemory, kid)
            if row:
                row.importance = min(10, (row.importance or 1) + 1)
                row.last_access_at = now
        if retire_ids:
            await db.execute(
                update(LongTermMemory)
                .where(LongTermMemory.id.in_(retire_ids))
                .values(active=False))
        for text, pid in new_points:
            db.add(LongTermMemory(user_id=user_id, kind=KIND_SEMANTIC, text=text,
                                  qdrant_point_id=pid, source_session=source_session,
                                  created_at=now))
        await db.commit()

    await _sync_profile_fallback(user_id)        # 兜底画像表跟随
    return {"added": n_new, "changed": n_changed, "kept": len(keep_ids),
            "retired": len(retire), "parallel": n_parallel, "guard": guard}


async def save_user_memories(user_id: int, sentences: list[str]) -> None:
    """兼容旧调用（历史验证脚本）：等同于"全量对齐语义画像"，已是增量 merge 而非删光重建"""
    await merge_semantic_memories(user_id, sentences)


async def save_episodic_memories(user_id: int, events: list[str],
                                 source_session: str | None = None,
                                 ttl_days: int = EPISODIC_TTL_DAYS) -> int:
    """情景记忆 append：和已有情景条相似的（≥0.85）去重跳过，否则落新行 + 向量

    带 TTL（P2-10）：情景记忆是"有时效的事实"，过期后 `_active_rows` 自动不再返回它。
    行还留在库里（可审计/可回溯），只是不再参与召回。
    """
    clean = [e.strip() for e in (events or []) if e and e.strip()]
    if not clean:
        return 0
    ensure_collection(MEMORY_COLLECTION)

    old = await _active_rows(user_id, KIND_EPISODIC)
    to_add = []
    for e in clean:
        dup = any(_ratio(e, row.text) >= 0.85 for row in old)
        if not dup:
            to_add.append(e)
    if not to_add:
        return 0

    vecs = await _embed_chunked(to_add)
    payload_ts = datetime.now().isoformat(timespec="seconds")
    now = _utcnow()
    docs, rows = [], []
    for text, v in zip(to_add, vecs):
        pid = str(uuid.uuid4()) if v else None
        rows.append((text, pid))
        if v:
            docs.append({"id": pid, "vector": v,
                         "payload": {"text": text, "user_id": user_id,
                                     "created_at": payload_ts, "kind": KIND_EPISODIC}})
    if docs:
        upsert_docs(docs, collection_name=MEMORY_COLLECTION)
    expires = now + timedelta(days=ttl_days) if ttl_days else None
    async with AsyncSessionLocal() as db:
        for text, pid in rows:
            db.add(LongTermMemory(user_id=user_id, kind=KIND_EPISODIC, text=text,
                                  qdrant_point_id=pid, source_session=source_session,
                                  created_at=now, expires_at=expires))
        await db.commit()
    return len(rows)


async def recall_user_memories(user_id: int, query: str, top_k: int = 3) -> str:
    """按问题召回长期记忆（语义+情景一起），加权 = 相似度 × 重要性 × 新鲜度

    语义与情景都向量化进了同一 collection，向量召回天然能"语义命中"两类的文本；
    命中不足时补最近几条**语义**记忆（承接"刚才/上次说的"这类指代）；全空才退一句话画像兜底。
    """
    rows = await _active_rows(user_id)
    if not rows:
        return await get_user_profile(user_id)
    now = _utcnow()
    picked: list[LongTermMemory] = []

    vector = await embed_text(query)
    if vector:
        hits = search_similar(vector, top_k=max(top_k * 4, 12),
                              collection_name=MEMORY_COLLECTION, user_id=user_id)
        pid2row = {r.qdrant_point_id: r for r in rows if r.qdrant_point_id}
        scored = []
        for h in hits:
            row = pid2row.get(h["id"])
            if not row:
                continue
            age_days = max(0.0, (now - (row.created_at or now)).total_seconds() / 86400.0)
            recency = 1.0 / (1.0 + 0.5 * age_days)                        # 越近越相关
            weight = (h["score"] or 0.0) * (1.0 + 0.3 * (row.importance or 1)) * recency
            scored.append((weight, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [row for _, row in scored[:top_k]]

    # 命中不足 → 补最近几条生效的**语义**记忆（覆盖"刚才那个/上次"类提问）
    # 只补 semantic（P2-9）：情景记忆是"当时发生了什么"，和当前问题的相关性无法靠"最近"推断，
    # 塞进来只会污染 prompt —— 而且它本来就更该靠向量命中，不该靠兜底。
    if len(picked) < top_k:
        recent = sorted((r for r in rows if r.kind == KIND_SEMANTIC),
                        key=lambda r: r.created_at or datetime.min, reverse=True)
        for r in recent:
            if len(picked) >= top_k:
                break
            if all(r.id != p.id for p in picked):
                picked.append(r)

    if not picked:
        return await get_user_profile(user_id)

    # 只刷新 last_access（管理界面展示"最近用过"），**不再动 importance**（P2-8）：
    # 召回只说明"这条被搜到了"，不说明它重要。以前每召回一次就 +1 封顶 10，
    # 聊十来轮后所有记忆都饱和成 10，"重要性"直接退化成噪声。
    # importance 现在只在合并 keep 时涨（LLM 每轮重新确认它仍成立 = 真的稳定偏好）。
    async with AsyncSessionLocal() as db:
        for row in picked:
            r = await db.get(LongTermMemory, row.id)
            if r:
                r.last_access_at = now
        await db.commit()

    lines = []
    for row in picked:
        prefix = "（事件）" if row.kind == KIND_EPISODIC else ""
        lines.append(f"{prefix}{row.text}")
    return "\n".join(lines)


async def list_long_term_memories(user_id: int) -> list[dict]:
    """列该用户生效中的长期记忆（管理接口用），按时间倒序，两类都展示"""
    rows = await _active_rows(user_id)
    rows.sort(key=lambda r: r.created_at or datetime.min, reverse=True)
    return [{
        "id": r.id,
        "kind": r.kind,
        "text": r.text,
        "importance": r.importance,
        "source_session": r.source_session,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "last_access_at": r.last_access_at.isoformat() if r.last_access_at else "",
    } for r in rows]


async def delete_long_term_memory(user_id: int, mem_id: int) -> bool:
    """删单条长期记忆（连带删它的向量点 + 重算兜底画像），返回是否存在"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(LongTermMemory).where(
            LongTermMemory.id == mem_id, LongTermMemory.user_id == user_id,
        ))).scalars().first()
        if not row:
            return False
        pid, kind = row.qdrant_point_id, row.kind
        await db.delete(row)
        await db.commit()
    if pid:
        delete_points(MEMORY_COLLECTION, [pid])
    if kind == KIND_SEMANTIC:
        await _sync_profile_fallback(user_id)
    return True


async def reconcile_memory(user_id: int | None = None, dry_run: bool = False) -> dict:
    """SQLite ↔ qdrant 双向对账修复（P1-5）

    背景：两个存储没有共享事务，写路径是"先动向量再提交 SQLite"，中途崩溃会留下两类不一致：
    - 向量已删 / 没插上，而 DB 行还在 → 该行永远召不回（静默降级，不报错，最坑）
    - 向量插上了，而 DB 提交失败 → 孤儿向量，白占空间且永远没人引用
    两边顺序怎么调都堵不住（换顺序只是把故障从一类挪到另一类），
    真正的出路是承认 SQLite 是唯一事实源，然后**定期拿事实源去校准索引**：

    - 生效行缺少向量（pid 为空 / 点上不存在）→ 重新向量化补上（缺了就补，功能恢复）
    - qdrant 里没有行引用的点 → 删掉（清理泄漏；dry_run 时只报告不删）

    返回报告 dict，供启动日志 / 脚本打印。
    """
    ensure_collection(MEMORY_COLLECTION)
    now = _utcnow()
    async with AsyncSessionLocal() as db:
        q = select(LongTermMemory).where(
            LongTermMemory.active.is_(True),
            (LongTermMemory.expires_at.is_(None)) | (LongTermMemory.expires_at > now),
        )
        if user_id is not None:
            q = q.where(LongTermMemory.user_id == user_id)
        rows = list((await db.execute(q)).scalars().all())

    actual = list_point_ids(MEMORY_COLLECTION)
    expected = {r.qdrant_point_id for r in rows if r.qdrant_point_id}

    # ① 缺向量的生效行：重 embedding 补齐（补不了就留着，下次对账再试，绝不静默丢文本）
    need_fix = [r for r in rows if not r.qdrant_point_id or r.qdrant_point_id not in actual]
    repaired, failed = 0, 0
    if need_fix:
        vecs = await _embed_chunked([r.text for r in need_fix])
        payload_ts = datetime.now().isoformat(timespec="seconds")
        docs, pid_updates = [], []
        for row, v in zip(need_fix, vecs):
            if not v:
                failed += 1
                continue
            pid = row.qdrant_point_id or str(uuid.uuid4())
            docs.append({"id": pid, "vector": v,
                         "payload": {"text": row.text, "user_id": row.user_id,
                                     "created_at": payload_ts, "kind": row.kind}})
            pid_updates.append((row.id, pid))
        if docs and not dry_run:
            upsert_docs(docs, collection_name=MEMORY_COLLECTION)
            async with AsyncSessionLocal() as db:
                for rid, pid in pid_updates:
                    await db.execute(update(LongTermMemory)
                                     .where(LongTermMemory.id == rid)
                                     .values(qdrant_point_id=pid))
                await db.commit()
        repaired = len(docs)

    # ② 孤儿向量：没有任何行引用的点 → 删（保留日志，出问题能追）
    orphans = actual - expected
    if orphans and not dry_run:
        delete_points(MEMORY_COLLECTION, sorted(orphans))
        logger.warning("记忆对账：清理 %d 个孤儿向量 %s", len(orphans), sorted(orphans)[:5])
    if failed:
        logger.warning("记忆对账：%d 条记忆向量化失败，文本仍保留在 SQLite，下次对账重试", failed)

    return {"rows": len(rows), "repaired": repaired, "orphans": len(orphans),
            "failed": failed, "dry_run": dry_run}


async def save_uploaded_doc(session_id: str, user_id: int, filename: str, content: str) -> None:
    """存一条上传文件的解析结果（按会话关联，同一个 session 可传多个文件）"""
    async with AsyncSessionLocal() as db:
        db.add(UploadedDoc(session_id=session_id, user_id=user_id,
                           filename=filename, content=content))
        await db.commit()


# 单个上传文件最多注入 Agent 的字符数（20 页 PDF 解析几万字，全量塞 prompt 会撑爆上下文）
MAX_DOC_CHARS = 6000

async def get_uploaded_docs(session_id: str, user_id: int) -> str:
    """读该会话上传过的文件解析文本，拼成字符串（聊天时注入 Agent，供对比分析）

    超长文件在注入层截断到 MAX_DOC_CHARS（DB 仍存完整解析文本），
    避免上传大文件把上下文撑爆
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(UploadedDoc).where(
                UploadedDoc.session_id == session_id,
                UploadedDoc.user_id == user_id,
            ).order_by(UploadedDoc.id)
        )).scalars().all()
    if not rows:
        return ""
    parts = []
    for r in rows:
        content = r.content
        if len(content) > MAX_DOC_CHARS:
            content = content[:MAX_DOC_CHARS] + \
                f"\n…[原文共 {len(r.content)} 字，超长截断，仅保留前 {MAX_DOC_CHARS} 字]"
        parts.append(f"【上传文件：{r.filename}】\n{content}")
    return "\n\n".join(parts)


# ============ 会话元信息（多会话支持）============
# 每次对话落库时同步一张 chat_sessions 表：侧边栏列表 / 重命名 / 删除都靠它。
# 和消息表分离的好处：会话列表只查这张小表，不用 DISTINCT 扫消息表；还留了软删除的口子。


async def upsert_session(session_id: str, user_id: int, title_hint: str = "") -> None:
    """每次对话落库时同步会话元信息（chat.py 里存 user 消息后调用）

    - 会话不存在 → 新建，标题取本轮第一条用户消息（截断 30 字）
    - 会话已存在 → 刷新 updated_at（置顶用）；只有还没标题（首轮）才补标题，
      手动重命名过的标题不覆盖
    """
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )).scalars().first()
        if row:
            row.is_deleted = False          # 软删后同一 sid 复用 → 自动恢复
            if not row.title:
                row.title = (title_hint or "")[:30]
        else:
            db.add(ChatSession(session_id=session_id, user_id=user_id,
                               title=(title_hint or "")[:30]))
        await db.commit()


async def list_sessions(user_id: int) -> list[dict]:
    """当前用户未删除的会话列表，按最近活跃倒序，带消息数（侧边栏用）"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatSession, func.count(ChatMessage.id).label("msg_count"))
            .outerjoin(ChatMessage,
                       (ChatMessage.session_id == ChatSession.session_id) &
                       (ChatMessage.user_id == ChatSession.user_id))
            .where(ChatSession.user_id == user_id, ChatSession.is_deleted == False)
            .group_by(ChatSession.id)
            .order_by(ChatSession.updated_at.desc())
        )).all()
    return [{
        "id": s.session_id,
        "title": s.title or "新对话",
        "msg_count": cnt,
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    } for s, cnt in rows]


async def get_session_messages(session_id: str, user_id: int) -> list[dict]:
    """某个会话的完整历史消息（切换会话时前端加载，只回 user/assistant 两种角色）"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.user_id == user_id,
            ).order_by(ChatMessage.id)
        )).scalars().all()
    return [{
        "role": r.role, "content": r.content,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]


async def rename_session(session_id: str, user_id: int, title: str) -> None:
    """手动重命名会话标题"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )).scalars().first()
        if row:
            row.title = title.strip()[:30]
            await db.commit()


async def delete_session(session_id: str, user_id: int) -> None:
    """彻底删除会话：连同消息、上传文档一起物理删除（不可恢复）

    注意：用户画像长期记忆（qdrant user_memories / user_profile 表）
    是跨会话的"这个人"的记忆，不属于某个会话，删单个会话不清它。
    """
    async with AsyncSessionLocal() as db:
        for model in (ChatMessage, UploadedDoc, ChatSession):
            await db.execute(
                delete(model).where(
                    model.session_id == session_id,
                    model.user_id == user_id,
                )
            )
        await db.commit()
