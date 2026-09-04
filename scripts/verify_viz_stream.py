"""端到端验证聊天输出自适应：登录 → 发数据分析问题 → 收 SSE 报告 → 检查末尾 viz 块

用 requests 流式读（read 不限时，适合几十秒的分析链路）。
运行（后端已起在 8000）：PYTHONPATH=. .venv/Scripts/python scripts/verify_viz_stream.py
"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests

BASE = "http://127.0.0.1:8000"


def main():
    # 登录拿 JWT（测试账号 zhangsan / admin123）
    tok = requests.post(f"{BASE}/api/auth/login",
                        json={"username": "zhangsan", "password": "admin123"},
                        timeout=20).json().get("access_token")
    assert tok, "登录失败"
    print("登录 OK")

    question = "帮我分析最近7天每天销售额的趋势，以及哪类商品卖得最好"
    t0 = time.time()
    r = requests.post(f"{BASE}/api/chat/stream", json={"message": question, "session_id": ""},
                      headers={"Authorization": f"Bearer {tok}"},
                      stream=True, timeout=(10, None))   # 连接 10s，读不限时
    r.raise_for_status()

    report, chunks, seen = "", 0, set()
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            evt = json.loads(line[6:])
        except Exception:
            continue
        if evt["type"] in seen:
            continue
        seen.add(evt["type"])
        if evt["type"] == "token":
            chunks += 1
        elif evt["type"] == "report":
            report = evt.get("report", "")
            break          # 拿到完整报告即收尾
    cost = time.time() - t0
    print(f"SSE 阶段收到: {sorted(seen)} | token 片段 {chunks} 次 | 总耗时 {cost:.0f}s | 报告 {len(report)} 字")

    if not report:
        print("FAIL 没收到 report"); return

    idx = report.find("```viz")
    if idx == -1:
        print("FAIL 报告无 viz 块（LLM 选择纯文字）。报告开头 220 字：\n" + report[:220])
        return
    close = report.find("```", idx + 6)
    raw = report[idx + 6:close if close != -1 else None]
    try:
        cfg = json.loads(raw.strip())
        assert cfg["type"] in ("line", "bar", "column", "table"), f"未知类型 {cfg['type']}"
        assert isinstance(cfg["data"], list) and cfg["data"], "data 为空"
        assert all({"x", "y"} <= set(d) for d in cfg["data"][:5]), "数据点缺 x/y"
        assert report[:idx].strip(), "viz 前无正文结论"
        print(f"PASS viz 块 type={cfg['type']} title={cfg.get('title')!r} 行数={len(cfg['data'])}")
        print(f"PASS 正文在前({len(report[:idx])}字)、数值字段规范、JSON 可解析")
    except Exception as e:
        print(f"FAIL viz 解析：{e}\n原始前 260 字：{raw[:260]}")


if __name__ == "__main__":
    main()
