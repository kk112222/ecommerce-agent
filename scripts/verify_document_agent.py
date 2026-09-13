"""文档 Agent 离线验证（stub LLM，不联网、不烧配额）

覆盖：
1. 路径安全：LLM 给的 ../ 名字不能逃出 outputs/；非法格式报错
2. write_document：md/txt/docx 三种格式真落盘，返回相对路径
3. optimize_document：按指令改写（stub 返回）
4. 全链路：DocumentAgent + ReActAgent + registry 调 write_document 真写文件
5. 接线：INTENTS 含 document；supervisor 能构建；register_all_tools 带 context 注册文档工具
跑完清理临时产物。
"""
import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

logging.disable(logging.CRITICAL)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.document import DocumentAgent
from backend.agents.intent_classifier import INTENTS
from backend.agents.supervisor import build_supervisor
from backend.core.llm.base import LLMResponse, ToolCall
from backend.core.tool.registry import ToolRegistry
from backend.infrastructure.doc_output import OUTPUTS_DIR, safe_output_path, write_document
from backend.tools import register_all_tools

TUID = 999999   # 临时用户 id，测完删整个目录


class _StubLLM:
    """按预设序列依次返回真实 LLMResponse；用于驱动 ReAct 循环"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen = []

    async def chat(self, messages, tools=None, temperature=0.0):
        self.seen.append(list(messages))        # 存快照：ReAct 会往原列表继续追加 tool 消息
        return self._responses.pop(0) if self._responses else LLMResponse(content="（stub 无更多响应）")


def main():
    # 1) 路径安全
    p = safe_output_path(r"../../evil", "md", user_id=TUID, session_id="s1")
    assert p.is_relative_to(OUTPUTS_DIR), p
    assert p.name == "evil.md", p.name
    p2 = safe_output_path(r"a/b/c:报告*.md", "docx", user_id=TUID)   # 路径成分+非法字符都要清洗
    assert p2.is_relative_to(OUTPUTS_DIR) and p2.name.startswith("报告") and p2.suffix == ".docx", p2
    try:
        safe_output_path("x", "exe", user_id=TUID)
        raise AssertionError("非法格式应报错")
    except ValueError:
        pass
    print("[1] 路径安全 ok：../ 与非法字符被清洗，格式白名单生效")

    # 2) 三种格式落盘
    tag = f"verify_{int(time.time())}"
    info_md = write_document("# 标题\n正文", tag, "md", user_id=TUID, session_id="s1")
    info_txt = write_document("纯文本", tag, "txt", user_id=TUID, session_id="s1")
    info_docx = write_document("# 标题\n正文", tag, "docx", user_id=TUID, session_id="s1")
    for info in (info_md, info_txt, info_docx):
        assert (ROOT / info["path"]).exists(), info
    assert info_md["path"].startswith(f"outputs/user_{TUID}/"), info_md
    print("[2] md/txt/docx 落盘 ok：", info_md["path"], "|", info_docx["bytes"], "bytes")

    # 3) optimize_document
    reg = ToolRegistry()
    register_all_tools(reg, _StubLLM([LLMResponse(content="（已精简的全文）")]),
                       context={"user_id": TUID, "session_id": "s1"})
    r = asyncio.run(reg.execute("optimize_document", content="很长很长的原文", instruction="精简"))
    assert r.success and "已精简" in r.data["optimized"], r
    print("[3] optimize_document ok：", r.data["optimized"])

    # 4) 全链路：ReAct 先调 write_document，再给最终说明
    stub = _StubLLM([
        LLMResponse(content="", tool_calls=[ToolCall(id="call_1", name="write_document", arguments={
            "filename": "竞品分析报告", "content": "# 竞品分析\n差距在价格", "format": "docx"})]),
        LLMResponse(content="已生成文档，保存路径见工具结果。"),
    ])
    reg2 = ToolRegistry()
    register_all_tools(reg2, stub, context={"user_id": TUID, "session_id": "s1"})
    out = asyncio.run(DocumentAgent(stub, reg2).run(
        goal="生成一份竞品分析报告", uploaded_data="我们的T恤卖99，竞品卖79"))
    assert "已生成" in out, out
    assert reg2.get_tool("write_document").user_id == TUID      # 上下文注入成功
    assert stub.seen[0][-1].content == "生成一份竞品分析报告"      # 用户目标在最后
    assert any("上传文档" in m.content for m in stub.seen[0])     # 上传文档注入成功
    print("[4] DocumentAgent 全链路 ok：ReAct 调 write_document 成功，上下文注入正确")

    # 5) 接线
    assert "document" in INTENTS, INTENTS
    g = build_supervisor(llm=None, registry=ToolRegistry())
    assert "document" in g.nodes, list(g.nodes)
    print("[5] 接线 ok：INTENTS 含 document，supervisor 有 document 节点")

    # 清理临时产物
    shutil.rmtree(OUTPUTS_DIR / f"user_{TUID}", ignore_errors=True)
    print("文档 Agent 验证全绿 ✅")


if __name__ == "__main__":
    main()
