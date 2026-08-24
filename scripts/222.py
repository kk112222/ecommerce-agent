import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from backend.core.llm.factory import create_llm
from backend.core.memory.extractor import ProfileExtractor

async def main():
    llm = create_llm()
    ex = ProfileExtractor(llm)
    conv = '用户：帮我看下数码类目的转化率\n用户：再写个小红书风格的文案\n用户：我们店退货率最近有点高'
    result = await ex.extract(conv)
    print('提炼结果:', result)

asyncio.run(main())