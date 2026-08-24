"""临时验证脚本：测试 supervisor SSE 事件流（plan → subtask → report）"""
import sys
import json
import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 终端 GBK 打印 emoji 会崩

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwiZXhwIjoxNzg2ODEzMzAwfQ.A9WHPtCSpp3VzSrpCjLHD1Ns9rcWVwZiWDGv2v516I4"


def main():
    with httpx.stream(
        "POST", "http://localhost:8000/api/chat/stream",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"message": "为什么这周营业额很低，怎么提高销量"},
        timeout=300,
    ) as resp:
        print("状态码:", resp.status_code)
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            t = evt.get("type")
            if t == "plan":
                print("\n【计划】")
                for s in evt["plan"]:
                    print(f"  {s['id']}: {s['task']}")
            elif t == "subtask":
                print(f"\n【子任务 {evt.get('id')}】")
                print(f"  {evt.get('result', '')[:150]}")
            elif t == "report":
                print(f"\n【报告】")
                print(evt["report"][:800])
            elif t == "session":
                print(f"\n【会话】{evt['session_id']}")


if __name__ == "__main__":
    main()
