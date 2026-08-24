"""构建知识库：读取 Markdown/PDF/Word/网页 → 切块 → embedding → 存入 Qdrant"""
import asyncio
import uuid
from pathlib import Path

from backend.infrastructure.vector_store.embeddings import embed_batch
from backend.infrastructure.vector_store.qdrant_client import ensure_collection, upsert_docs
from backend.infrastructure.vector_store.doc_processor import parse_file

KB_DIR = Path(__file__).parent.parent / "backend" / "data" / "kb"
CHUNK_SIZE = 300  # 每块最多 300 字
SUPPORTED = ("*.md", "*.pdf", "*.docx", "*.html", "*.htm", "*.txt")


def chunk_text(text: str, source: str) -> list[dict]:
    """把一大段文字切成小块，保留来源信息"""
    if not text.strip():
        return []
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    current = ""
    for p in paragraphs:
        if len(current) + len(p) < CHUNK_SIZE:
            current += ("\n" if current else "") + p
        else:
            if current:
                chunks.append({"text": current, "source": source})
            current = p
    if current:
        chunks.append({"text": current, "source": source})
    return chunks


async def build():
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

    batch_size = 25
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


if __name__ == "__main__":
    asyncio.run(build())
