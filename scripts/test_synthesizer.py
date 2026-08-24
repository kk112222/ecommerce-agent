import asyncio
from backend.core.llm.factory import create_llm
from backend.agents.synthesizer import Synthesizer

async def main():
    llm = create_llm()
    syn = Synthesizer(llm, None)   # registry 用不上，传 None

    goal = "为什么这周营业额很低，怎么提高销量"
    results = {
        "t1": "本周销售额 8200 元（120 单），较上周 13500 元下降 39%",
        "t2": "数码类 3500 元降幅最大，其中充电宝从 2100 元跌到 600 元",
        "t3": "充电宝库存仅剩 8 个，低于阈值 30，疑似缺货",
        "t4": "SVIP 用户本周消费占比从 40% 降到 25%，高价值客户活跃度下滑",
    }
    report = await syn.synthesize(goal, results)
    print(report)

asyncio.run(main())