"""构建知识库：读取 Markdown/PDF/Word/网页 → 切块 → embedding → 存入 Qdrant"""
import asyncio
import uuid
from pathlib import Path

from backend.infrastructure.vector_store.embeddings import embed_batch
from backend.infrastructure.vector_store.qdrant_client import ensure_collection, upsert_docs
from backend.infrastructure.vector_store.doc_processor import parse_file

KB_DIR = Path(__file__).parent.parent / "backend" / "data" / "kb"
CHUNK_SIZE = 200  # 结构切块后每块 ≈ 1~2 个条款/Q&A（答案单元粒度）
OVERLAP = 40      # 块间重叠，防"答案骑在块边界上"
SUPPORTED = ("*.md", "*.pdf", "*.docx", "*.html", "*.htm", "*.txt")


def chunk_text(text: str, source: str) -> list[dict]:
    """结构化切块：切分粒度对齐"一个问题的答案单元"，不是字符数。

    原理（面试点）：
    - md 的 ## 分节 / html2text 转换出的 # 标题，就是天然答案边界 → 标题开新块
    - docx/pdf/txt 无标题标记 → 退回按段落(空行)分组；完全无空行(如 PDF get_text
      压成一大段)再按行兜底，避免单块塞多个主题
    - 块间留 OVERLAP 重叠，防止一句话被劈在两块导致召回到一半
    """
    if not text.strip():
        return []
    raw = text.replace("\r\n", "\n")
    # ① 拆段落：优先空行；无空行的纯文本按行
    if "\n\n" in raw:
        paras = [p.strip() for p in raw.split("\n\n") if p.strip()]
    else:
        paras = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # ② 超长段落(无内部结构)先按行切成 ≤CHUNK_SIZE 的小段
    units: list[str] = []
    for p in paras:
        if len(p) <= CHUNK_SIZE:
            units.append(p)
            continue
        buf = ""
        for ln in p.splitlines():
            if buf and len(buf) + len(ln) > CHUNK_SIZE:
                units.append(buf)
                buf = ln
            else:
                buf += ("\n" if buf else "") + ln
        if buf:
            units.append(buf)

    # ③ 组块：标题开头强制开新块；内容在 CHUNK_SIZE 内累积（不腰斩段落）
    chunks: list[str] = []
    cur = ""
    for u in units:
        is_heading = u.lstrip().startswith("#")
        if cur and (is_heading or len(cur) + len(u) > CHUNK_SIZE):
            chunks.append(cur)
            cur = u
        else:
            cur = u if not cur else cur + "\n" + u
    if cur:
        chunks.append(cur)

    # ④ overlap：下一块开头补上一块结尾 OVERLAP 字（标题块除外，保持干净）
    if OVERLAP > 0 and len(chunks) > 1:
        final = [chunks[0]]
        for c in chunks[1:]:
            if c.lstrip().startswith("#"):
                final.append(c)
            else:
                final.append(final[-1][-OVERLAP:] + "\n" + c)
        chunks = final

    return [{"text": c, "source": source} for c in chunks if c.strip()]


async def build():
    # 重建 = 全量覆盖：先清空旧块再写，避免重复跑新旧叠加。
    # 踩坑：qdrant 本地模式 delete_collection 不可靠（删后旧点残留），改按点删除，
    # 与 upsert 走同一条已验证可落盘的路
    from backend.infrastructure.vector_store.qdrant_client import COLLECTION_NAME, get_client
    c = get_client()
    if c.collection_exists(COLLECTION_NAME):
        old_pts, _ = c.scroll(collection_name=COLLECTION_NAME, limit=10000, with_payload=False)
        old_ids = [p.id for p in old_pts]
        if old_ids:
            c.delete(collection_name=COLLECTION_NAME, points_selector=old_ids)
    ensure_collection()

    # 收集所有支持的文件
    files = []
    for pattern in SUPPORTED:
        files.extend(KB_DIR.glob(pattern))

    if not files:
        print(f"未找到知识库文件，请在 {KB_DIR} 下放置 .md / .pdf / .docx / .html 文件")
        return

    print(f"找到 {len(files)} 个文件：")
    all_chunks = []
    for filepath in sorted(files, key=lambda p: p.name):
        text = parse_file(filepath)
        if not text.strip():
            print(f"  {filepath.name}: ⚠️ 解析为空，跳过")
            continue
        chunks = chunk_text(text, filepath.name)
        all_chunks.extend(chunks)
        print(f"  {filepath.name}: {len(text)} 字符 → {len(chunks)} 个片段")

    if not all_chunks:
        print("\n没有可用的文本片段")
        return

    print(f"\n共 {len(all_chunks)} 个片段，正在向量化...")

    batch_size = 10  # DashScope text-embedding-v3 单次最多 10 条，超过会整批 400（空向量被跳过）
    all_points = []
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        vectors = await embed_batch(texts)

        for chunk, vec in zip(batch, vectors):
            if vec:
                all_points.append({
                    "id": str(uuid.uuid4()),
                    "vector": vec,
                    "payload": {"text": chunk["text"], "source": chunk["source"]},
                })
        print(f"  {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")

    if all_points:
        upsert_docs(all_points)
        print(f"\n完成！{len(all_points)} 个片段已存入 Qdrant")
    else:
        print("\n失败：向量化为空，请检查 DashScope API Key")

    c.close()  # 显式关闭落盘：qdrant 本地模式靠 close 持久化，别等解释器析构（会半路崩丢数据）


if __name__ == "__main__":
    asyncio.run(build())
