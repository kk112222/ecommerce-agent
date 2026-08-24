"""阶段2 向量记忆 · 真实链路验收（阶段A：后端存活时跑）

验证走后端 API 的两轮真实对话链路：
  1. 第一轮表达偏好 → 触发后台画像提炼 + 向量化落库（_extract_and_save）
  2. 第二轮触发 recall_user_memories 语义召回 → 回复体现画像 → 链路不报错

数据层铁证（qdrant / sqlite）在阶段B查，见 verify_stage2_db.py（需先停后端，
因为 qdrant 本地模式单进程独占锁）。

用法：cd ecommerce-agent && PYTHONPATH=. .venv/Scripts/python scripts/verify_stage2.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

BASE = "http://localhost:8000/api"


def main():
    tok = requests.post(f"{BASE}/auth/login",
                        json={"username": "zhangsan", "password": "admin123"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    def chat(msg):
        r = requests.post(f"{BASE}/chat", headers=H,
                          json={"message": msg, "session_id": "verify-stage2"}, timeout=180)
        reply = r.json().get("reply", r.json())
        return r.status_code, reply

    # ① 第一轮：表达工作偏好 → 触发后台提炼落库
    s1, r1 = chat("我是负责男装的运营，关注转化率和标题点击率，写标题喜欢突出促销卖点。"
                  "帮我看看男装近7天哪些品类卖得最好？")
    print(f"[1] 第一轮对话 HTTP {s1} | 回复前120字：{r1[:120].replace(chr(10), ' ')}")
    print("    （等 4 秒让后台 _extract_and_save 提炼 + 向量化落库…）")

    # ② 第二轮：表达新偏好（女装/库存）+ 触发 recall_user_memories，验证合并累积
    import time
    time.sleep(4)
    s2, r2 = chat("我刚接手了女装类目，现在也很关注库存告警。结合我之前说的，标题应该怎么写？")
    print(f"[2] 第二轮对话 HTTP {s2} | 回复前120字：{r2[:120].replace(chr(10), ' ')}")
    print("    本轮表达新偏好：女装 + 库存告警 —— 阶段B将验证旧画像(男装/卖点)是否被保留")


if __name__ == "__main__":
    main()
