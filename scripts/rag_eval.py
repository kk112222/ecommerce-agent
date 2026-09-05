"""RAG 检索质量评测（尺子）—— golden set + 三层检索 A/B

思路（对应外部评审第三节）：
  评测 = 建一份带真值的评测集，把"检索改没改好"变成数字，而不是凭感觉。
  本脚本对每条问题跑 4 种策略，统计真值块在不在 top-5：
    - BM25（纯字面）          retriever.bm25_search
    - 向量（纯语义）          retriever.vector_search
    - 混合 RRF（不精排）      _rrf_fuse(bm25, vector) 取 top5
    - 混合 + rerank（生产路径） retriever.search —— 和线上客服走的是同一个函数
  指标：Recall@5 / Hit@1 / MRR（真值首次出现的名次倒数）。

运行（需先停后端——qdrant 本地模式单进程锁，否则冲突）：
  cd ecommerce-agent
  PYTHONPATH=. .venv/Scripts/python scripts/rag_eval.py

真值选取规则：只选"预训练背不到"的虚构条款（运费/包邮/保修天数），
答错 = 真没检索到，不可能是模型自己会的 —— 这也是将来扰动测试的地基。
"""
import asyncio, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.tools.service_tools.rag_search import get_retriever
from backend.infrastructure.vector_store.retriever import _rrf_fuse

# (问题, 真值片段, 来源) —— 片段必须逐字存在于某个块（payload text 是原始 markdown）
# 片段尽量取不含 ** 加粗符号的子串，且只出现在目标块，避免歧义
GOLDEN = [
    # ============ returns.md 退货政策 ============
    ("收到货之后多少天内能申请退货？", "可申请退货", "returns.md"),
    ("内衣这一类商品可以退吗？", "内衣、定制商品不支持退货", "returns.md"),
    ("退货申请在哪里提交？", "提交退货申请", "returns.md"),
    ("退款会退到哪里？", "原路返回", "returns.md"),
    ("因为质量问题退货运费算谁的？", "商家承担来回运费", "returns.md"),
    ("只是不喜欢想退货，运费谁出？", "用户承担退货运费", "returns.md"),
    ("运费险能赔多少钱？", "8-12 元", "returns.md"),
    ("特别大的家具退货要自己寄回去吗？", "上门取件", "returns.md"),
    ("海外购的商品退货要多长时间？", "延长至 15 天", "returns.md"),
    ("想换个尺码怎么操作？", "重新下单", "returns.md"),
    # ============ shipping.md 物流配送 ============
    ("一般地区下单后多久能发货？", "1-3 天发货", "shipping.md"),
    ("发到新疆的件要多久能送到？", "7-15 天送达", "shipping.md"),
    ("跨境订单要多久才发货？", "7-14 天发货", "shipping.md"),
    ("满多少钱可以包邮？", "满 99 元包邮", "shipping.md"),
    ("不包邮的话普通快递收多少钱？", "普通快递 8 元", "shipping.md"),
    ("想寄顺丰要多少钱？", "顺丰 18 元", "shipping.md"),
    ("偏远地区的运费要额外加钱吗？", "加收 15 元附加费", "shipping.md"),
    ("物流信息更新会有延迟吗？", "2-4 小时延迟", "shipping.md"),
    ("等了好几天快递一直没到怎么办？", "申请催件", "shipping.md"),
    ("快递显示签收但实际没收到怎么办？", "48 小时内核实", "shipping.md"),
    # ============ faq.md 常见问题 ============
    ("下单之后还能改收货地址吗？", "修改地址", "faq.md"),
    ("怎么取消订单？", "秒退款", "faq.md"),
    ("可以货到付款吗？", "不支持货到付款", "faq.md"),
    ("收到的东西有质量问题找谁？", "拍照联系客服", "faq.md"),
    ("电子产品保修期多长？", "保修 1 年", "faq.md"),
    ("电子发票多久能收到？", "发送到邮箱", "faq.md"),
    ("SVIP 比 VIP 多什么权益？", "生日礼包", "faq.md"),
    ("要消费满多少才能升到 SVIP？", "满 20000 元", "faq.md"),
    ("一张订单能用几张优惠券？", "限用一张优惠券", "faq.md"),
    ("秒杀抢的商品能退货吗？", "7 天无理由退货", "faq.md"),
    # ============ membership.md 会员等级与积分（新增） ============
    ("会员等级会过期吗，有效期多久？", "有效期 12 个月", "membership.md"),
    ("消费积分能一直攒着不清零吗？", "积分有效期 24 个月", "membership.md"),
    ("退货的话已经到账的积分怎么办？", "退货订单已获积分自动扣回", "membership.md"),
    ("积分在结算时怎么抵钱？", "每 100 积分抵 1 元", "membership.md"),
    ("会员日是每月几号，全场有折扣吗？", "每月 8 号为会员日", "membership.md"),
    # ============ invoice.html 发票与报销（新增） ============
    ("能开增值税专用发票吗，需要填什么？", "增值税专用发票", "invoice.html"),
    ("发票抬头开错了能改吗？", "作废重开", "invoice.html"),
    ("电子发票一直没收到怎么办？", "重发发票", "invoice.html"),
    ("发票金额里包含运费吗？", "不单独开票", "invoice.html"),
    # ============ account.txt 账户安全与隐私（新增） ============
    ("忘记登录密码怎么重置？", "收验证码即可重置", "account.txt"),
    ("想换绑手机号有什么要求？", "先用原手机号验证", "account.txt"),
    ("注销账号之后还能反悔吗？", "15 天冷静期", "account.txt"),
    ("营销短信不想收了怎么退订？", "回复 TD 退订", "account.txt"),
    # ============ promo.docx 促销活动规则（新增） ============
    ("预售付了定金但没付尾款会怎样？", "转等额余额券", "promo.docx"),
    ("秒杀活动一个账号最多买几件？", "限购 1 件", "promo.docx"),
    ("拼团没拼成功会退款吗？", "未成团自动原路退款", "promo.docx"),
    ("优惠券领了多久会过期？", "有效期自领取起 7 天", "promo.docx"),
    # ============ cross_border.pdf 跨境与清关（新增） ============
    ("跨境订单清关要提供什么资料？", "身份证号", "cross_border.pdf"),
    ("买跨境商品要不要交税？", "行邮税", "cross_border.pdf"),
    ("跨境订单清关大概要多久？", "3-7 个工作日", "cross_border.pdf"),
    ("跨境订单发货后还能改收货地址吗？", "不支持修改收货地址与取消", "cross_border.pdf"),
]

TOP_K = 5  # 线上 rag_search 的 top_k 也是 5，保持一致
STRATEGIES = ["BM25", "向量", "混合RRF", "混合+精排"]


async def main():
    t0 = time.time()
    retriever = get_retriever()
    docs = retriever.documents
    texts = [d["text"] for d in docs]

    # 语料概览 + golden 完整性自检：每个真值片段应在语料里命中，且最好只命中 1 个块
    by_src: dict[str, int] = {}
    for d in docs:
        by_src[d.get("source", "?")] = by_src.get(d.get("source", "?"), 0) + 1
    print(f"语料块数 = {len(texts)}  分布 = {by_src}\n")
    for q, snip, src in GOLDEN:
        n = sum(1 for t in texts if snip in t)
        if n == 0:
            print(f"  [警告] 片段查无此块（可能 KB 没重建/已过期）：{q} → {snip!r}")
        elif n > 1:
            print(f"  [提示] 片段命中 {n} 个块（真值有歧义）：{q} → {snip!r}")

    agg = {s: {"hits5": 0, "hits1": 0, "rr": 0.0} for s in STRATEGIES}
    miss = 0  # 生产策略（混合+精排）答错的问题，用于人工复核
    src_stat = {s: {"ok": 0, "tot": 0} for s in sorted({g[2] for g in GOLDEN})}  # 按来源拆命中

    for q, snip, src in GOLDEN:
        # 三路候选一次性取好（bm25 本地零成本；vector 每次一次 embed）
        b10 = retriever.bm25_search(q, top_k=10)          # BM25 top10（供融合）
        v10 = await retriever.vector_search(q, top_k=10)  # 向量 top10（供融合）
        fused = _rrf_fuse(b10, v10)
        fused.sort(key=lambda x: x["score"], reverse=True)
        reranked = await retriever.search(q, top_k=TOP_K)  # 生产路径：混合 + rerank 精排

        cands = {
            "BM25":        [d["text"] for d in b10[:TOP_K]],
            "向量":        [d["text"] for d in v10[:TOP_K]],
            "混合RRF":     [d["text"] for d in fused[:TOP_K]],
            "混合+精排":   [d["text"] for d in reranked],
        }
        for name, tlist in cands.items():
            rank = next((i + 1 for i, t in enumerate(tlist) if snip in t), None)
            st = agg[name]
            if rank:
                st["hits5"] += 1
                st["hits1"] += rank == 1
                st["rr"] += 1.0 / rank
        prod_ok = any(snip in d["text"] for d in reranked)  # 生产路径是否命中
        src_stat[src]["tot"] += 1
        if prod_ok:
            src_stat[src]["ok"] += 1
        if not prod_ok:
            miss += 1
            if miss <= 3:  # 只打前几个，人工看看是检索问题还是真值问题
                print(f"  样例失败 [{src}] {q}\n    → 期望片段 {snip!r}\n    → 精排第1块: {reranked[0]['text'][:80]!r}")

    N = len(GOLDEN)
    print(f"\n{'策略':<8} {'Recall@5':>9} {'Hit@1':>7} {'MRR':>6}   正确/{N}")
    for name in STRATEGIES:
        st = agg[name]
        print(f"{name:<8} {st['hits5']/N:>8.0%} {st['hits1']/N:>6.0%} "
              f"{st['rr']/N:>6.3f}   {st['hits5']}/{N}")
    print(f"\n生产路径(混合+精排)答错 {miss}/{N} 条 | 总耗时 {time.time()-t0:.0f}s")
    print("\n按来源拆（生产路径 Hit@top5）：")
    for s, v in src_stat.items():
        print(f"  {s:<16} {v['ok']:>2}/{v['tot']:<2}")

    # 只读也显式关掉 qdrant，避免解释器析构报错刷屏
    from backend.infrastructure.vector_store.qdrant_client import get_client
    get_client().close()
    print("\n怎么读：BM25→向量 是两种召回方式的差距；向量→混合RRF 是融合收益；")
    print("混合RRF→混合+精排 是 rerank 的收益。改任何检索逻辑前后各跑一次对比数字。")


if __name__ == "__main__":
    asyncio.run(main())
