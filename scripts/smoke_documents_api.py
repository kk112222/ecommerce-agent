"""生成文档链路端到端冒烟（要真后端 + 真 LLM，非自动化测试）

验证 P0-3 闭环：Agent 落盘 → SSE 推 document 事件 → 用事件里的信息调下载接口 → 拿到真文件。
顺带验一次越权：换个用户去下载同一个文件，必须 404。

跑法：先起后端，再 `PYTHONPATH=. .venv/Scripts/python scripts/smoke_documents_api.py`
"""
import json
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"


def register(username: str) -> str:
    """注册一个临时用户拿 token（已存在就登录）"""
    for path in ("/api/auth/register", "/api/auth/login"):
        r = requests.post(f"{BASE}{path}", json={"username": username, "password": "smoke-123456"})
        if r.status_code == 200:
            return r.json()["access_token"]
    raise SystemExit(f"注册/登录失败：{r.status_code} {r.text}")


def main():
    user = f"smoke_doc_{uuid.uuid4().hex[:6]}"
    token = register(user)
    h = {"Authorization": f"Bearer {token}"}
    sid = uuid.uuid4().hex[:8]

    # ① 真 LLM 走一遍文档链路，收集 SSE 事件
    prompt = ("把下面这段售后说明整理成一份正式文档，标题叫 退货政策说明，"
              "存成 docx 文件：\n7 天无理由退货，运费由我们承担，需保持吊牌完整。")
    events = []
    t0 = time.time()
    with requests.post(f"{BASE}/api/chat/stream", headers=h,
                       json={"message": prompt, "session_id": sid},
                       stream=True, timeout=300) as resp:
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                evt = json.loads(line[6:])
                events.append(evt)
                if evt.get("type") in ("document", "error"):
                    print(f"  [{time.time() - t0:.1f}s] 收到 {evt['type']} 事件")
    kinds = [e["type"] for e in events]
    print("① SSE 事件序列：", kinds)

    doc = next((e for e in events if e["type"] == "document"), None)
    assert doc, "没有收到 document 事件 —— 前端拿不到下载入口"
    print(f"② 落盘：{doc['path']}（{doc['bytes']} 字节）")

    # ② 用事件给的信息下载（模拟前端）
    url = f"{BASE}/api/documents/{doc['session_id']}/{doc['filename']}"
    r = requests.get(url, headers=h)
    assert r.status_code == 200, f"本人下载失败：{r.status_code} {r.text}"
    print(f"③ 本人下载：200，{len(r.content)} 字节，Content-Type={r.headers.get('content-type')}")
    assert len(r.content) == doc["bytes"], "下载到的字节数与落盘不一致"

    # ③ 列表接口能看到它
    r = requests.get(f"{BASE}/api/documents", headers=h)
    names = [d["filename"] for d in r.json()["documents"]]
    assert doc["filename"] in names, f"列表里没有：{names}"
    print(f"④ 列表接口：{names}")

    # ④ 越权：另一个用户拿同一个 URL
    other = register(f"smoke_doc_{uuid.uuid4().hex[:6]}")
    r = requests.get(url, headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 404, f"越权下载没被拦住：{r.status_code}"
    print("⑤ 换别人下载同一个 URL：404（归属校验生效）")

    print("文档链路端到端冒烟全绿 ✅")


if __name__ == "__main__":
    main()
