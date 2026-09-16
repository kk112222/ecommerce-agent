# 06 · 混合检索 RAG

> 第 05 章的客服链路要回答"退货要多久""运费谁出"——这些答案**不在数据库里**，
> 而在一堆政策文档里。这一章讲怎么把文档变成"能查的东西"：
> **解析 → 切块 → 向量化 → 混合检索 → 重排**。

---

## 本章目标

读完这一章，你会明白：

1. 一份 PDF / Word / 网页 怎么变成能检索的纯文本
2. 为什么**切块粒度要对着"答案"切**，而不是对着字符数切
3. 为什么**只做向量检索不够**，BM25 补的是什么
4. RRF 融合为什么不用"加权求和"——它绕开了什么问题
5. 重排（Cross-Encoder）和向量检索（Bi-Encoder）到底差在哪
6. 知识库重建后，**为什么不用重启后端**

---

## 一、先看痛点：把文档全塞进 prompt 不行吗

知识库在 `backend/data/kb/` 下，是**一堆格式混在一起**的文件：

```
account.txt      faq.md           invoice.html
membership.md    promo.docx       returns.md
shipping.md      cross_border.pdf
```

最省事的做法：把所有文件读成文本，全塞进 system prompt。三个问题：

| 问题 | 后果 |
|---|---|
| **token 爆炸** | 十来个文件就是几万 token，每次提问都付一遍，还挤掉对话历史 |
| **噪音淹没答案** | 问"退货要多久"，prompt 里还躺着"会员等级规则""跨境税费" |
| **没法定量** | 说"检索准不准"时，你手里没有任何数字 |

所以要做 **RAG**（Retrieval-Augmented Generation）：**先检索出相关的几段，再把这几段喂给 LLM**。

整条流水线分两个阶段，**离线建库**和**在线查询**是两套代码：

```
【离线·跑一次】scripts/build_kb.py
   解析 → 切块 → 向量化 → 存 Qdrant

【在线·每次提问】backend/infrastructure/vector_store/
   检索（BM25 + 向量）→ RRF 融合 → 重排 → 取 Top-5 → 给 LLM
```

---

## 二、第一步：解析——把各种格式统一成纯文本

`doc_processor.parse_file` 按后缀分发：

```python
def parse_file(filepath: Path) -> str:
    suffix = filepath.suffix.lower()
    if suffix == ".md":    return _parse_md(filepath)      # 直接读
    elif suffix == ".pdf": return _parse_pdf(filepath)     # pymupdf 逐页 get_text
    elif suffix == ".docx":return _parse_docx(filepath)    # python-docx
    elif suffix in (".html", ".htm"): return _parse_html(filepath)
    elif suffix in (".csv", ".tsv"):  return _parse_csv(filepath)
    else:                  return filepath.read_text(encoding="utf-8", errors="replace")
```

四个解析器各有一个**值得记的细节**：

| 格式 | 关键处理 | 为什么 |
|---|---|---|
| **PDF** | `fitz`（pymupdf）逐页 `get_text()`，空页跳过 | 只能拿文字层；**扫描件（图片 PDF）抽不出东西**——这是当前方案的硬边界 |
| **Word** | 段落**之外还读表格单元格** | 政策文档常把运费规则放表格里，只读 paragraphs 会漏 |
| **HTML** | 先 `decompose()` 掉 `script/style/nav/footer/header`，再用 `html2text` 转 Markdown | 去掉导航和脚本噪音；转成 Markdown 后 **`#` 标题会保留**，正好给切块用（见下一节） |
| **CSV** | `encoding="utf-8-sig"`，每行用 ` \| ` 拼列 | `utf-8-sig` 兼容 Excel 导出的 BOM 头——不然第一列会多一个不可见的 `` |

> **HTML 那条转 Markdown 的处理是关键设计**：它让"网页"和"md 文件"在下游**长得一样**，切块逻辑就不用分两套。

---

## 三、第二步：切块——本章最值钱的一节

`scripts/build_kb.py`：

```python
CHUNK_SIZE = 200  # 结构切块后每块 ≈ 1~2 个条款/Q&A（答案单元粒度）
OVERLAP = 40      # 块间重叠，防"答案骑在块边界上"
```

### 3.1 为什么不按字符数切

最常见的做法是"每 500 字符切一刀"。**这个项目放弃了它**，理由写在同一段注释里：

> 切分粒度对齐"**一个问题的答案单元**"，不是字符数。

对 RAG 来说，**切块是检索质量的天花板**。按字符数切会出这种事：

```
块 A：「7 天无理由退货。退货运费由买家承担，除非商品
块 B： 有质量问题…」
```

用户问"退货运费谁出"，检索到块 A，里面正好断在半句话上。**模型要么答错，要么答不出。**

### 3.2 四步切块法

```python
def chunk_text(text: str, source: str) -> list[dict]:
    raw = text.replace("\r\n", "\n")

    # ① 拆段落：优先按空行；完全无空行的（PDF 抽出来常是一整块）按行兜底
    if "\n\n" in raw:
        paras = [p.strip() for p in raw.split("\n\n") if p.strip()]
    else:
        paras = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # ② 超长段落先按行切成 ≤ CHUNK_SIZE 的小段
    units: list[str] = []
    for p in paras:
        if len(p) <= CHUNK_SIZE:
            units.append(p); continue
        # …（按行累加到 CHUNK_SIZE 就断开）

    # ③ 组块：标题开头强制开新块；否则在 CHUNK_SIZE 内累积（不腰斩段落）
    chunks: list[str] = []
    cur = ""
    for u in units:
        is_heading = u.lstrip().startswith("#")
        if cur and (is_heading or len(cur) + len(u) > CHUNK_SIZE):
            chunks.append(cur); cur = u
        else:
            cur = u if not cur else cur + "\n" + u
    if cur:
        chunks.append(cur)

    # ④ overlap：下一块开头补上一块结尾 OVERLAP 字（标题块除外）
    if OVERLAP > 0 and len(chunks) > 1:
        final = [chunks[0]]
        for c in chunks[1:]:
            if c.lstrip().startswith("#"):
                final.append(c)                    # 标题块保持干净
            else:
                final.append(final[-1][-OVERLAP:] + "\n" + c)
        chunks = final
```

逐条对应设计意图：

| 步 | 做什么 | 为什么 |
|---|---|---|
| ① | **优先按空行拆** | 空行是作者写下的"这里是两件事"，比任何算法都准 |
| ① | 无空行时**按行兜底** | PDF 抽出来的文字**经常没有空行**（整页压成一段），不给兜底就变成一块巨无霸 |
| ② | 超长段落**先切小** | 防止单个段落撑爆一个块 |
| ③ | **`#` 标题强制开新块** | **这是"结构化切块"的核心**——md 的 `## 退货政策` / html2text 出的 `#` 标题，是作者给的**天然答案边界**。标题开新块 = 每个块天然带上"我是讲什么的" |
| ③ | 累积到 `CHUNK_SIZE` 才断 | 段落**不腰斩**（宁可超一点也不把一句话切成两半） |
| ④ | 补 `OVERLAP` 重叠 | 就算真断在边界上，**下一块开头也有上文**，不会召回一半 |
| ④ | **标题块不补重叠** | 这是**取舍**：`## 退货政策` 前面糊上一段货运说明的文字，标题就不再"干净"了——而标题干净对**关键词检索**很重要（下一节讲） |

> 记住这个次序判断：**结构标记优先于长度限制，长度限制优先于平均切分**。有结构就用结构，没结构（PDF/txt）才退回按行。

---

## 四、第三步：向量化——两个必须记住的数字

```python
# scripts/build_kb.py
batch_size = 10   # DashScope text-embedding-v3 单次最多 10 条，超过会整批 400（空向量被跳过）

...

c.close()   # 显式关闭落盘：qdrant 本地模式靠 close 持久化，别等解释器析构（会半路崩丢数据）
```

两个**"不写就出玄学 bug"**的点：

- **批量上限 10**：embedding 接口单次最多 10 条，超了**整批 400**——而代码里处理方式是"空向量被跳过"，结果是**静默丢数据**（不报错，但知识库少了一半）。这种"成功了一半"比直接报错难查得多。
- **必须 `c.close()`**：Qdrant **本地文件模式靠 `close()` 落盘**。不显式关，就等解释器析构——而 `build_kb.py` 结束前如果出点别的事，**数据可能丢一半**。

> 还有个建库时的坑写在代码注释里：*"qdrant 本地模式 `delete_collection` 不可靠（删后旧点残留），改按点删除"*。所以"重建知识库"不是删集合重建，而是 **`scroll` 出所有 id → 按 id 删 → 再 upsert**——走和写入同一条已验证能落盘的路。

**维度这件事要盯死**：`qdrant_client.py` 里

```python
VECTOR_SIZE = 1024  # text-embedding-v3 的维度
```

集合一旦创建，**维度就固定了**——写入时向量长度不匹配会直接报错。所以**换 embedding 模型 = 必须重建集合**，只改代码不重建库是不行的。

> 顺手记一个**文档不一致**：`embeddings.py` 的模块 docstring 写的是 *"文字 → 1536 维向量"*，但 `text-embedding-v3` 实际是 **1024** 维、集合也按 1024 建的。**能跑起来说明代码是对的、那句注释是旧的**——这种"注释漂移"不影响运行，但会误导后来读代码的人（包括几个月后的自己）。改掉它只是删几个字的事，值得做。

---

## 五、第四步：检索——为什么要"混合"

这是本章标题的由来。**只做向量检索会漏掉一类问题。**

### 5.1 两种检索器各自瞎在哪

| 检索器 | 擅长 | 瞎在哪 |
|---|---|---|
| **向量（语义）** | "退钱怎么办" ↔ "退款流程"（**换说法也认**） | 精确串：商品编号、`SF-100`、专有名词——语义上"差不多"的词会挤掉精确匹配 |
| **BM25（关键词）** | "7 天无理由"、`cross_border` 这种**精确串一查一个准** | "退钱"和"退款"字面不重叠就查不到（**中文尤其明显**） |

**它们瞎的地方正好互补**，所以两路都跑，再融合。

### 5.2 BM25 这一路：中文怎么分词

```python
tokenized = [list(doc["text"]) for doc in documents]     # 建索引
query_tokens = list(query)                               # 查询
scores = self._bm25_index.get_scores(query_tokens)
```

**分词方式就是 `list(text)`——按单字切。** 注释写得很坦白：*"简单按字切，中文足够"*。

这是个**有意识的取舍**：

- 上 `jieba` 要装依赖、要维护词典、专用词（"无理由退货"）还可能被切碎；
- 按字切，**"退货"和"退货政策"必然有字面重叠**，召回率不会差，代价是精度靠后面的重排来补。

> 这也是**面试可以被追问的点**（"你们中文分词怎么做的？"）——答案不是"用了 jieba"，而是**"BM25 只是召回的一路，精度交给重排，所以按字切够用"**。**知道自己在哪一层偷懒、以及谁来兜底**，比用了多高级的分词器更能说明问题。

### 5.3 两路各取 `top_k * 2`

```python
async def search(self, query: str, top_k: int = 5) -> list[dict]:
    bm25_results = self.bm25_search(query, top_k=top_k * 2)        # 同步
    vector_results = await self.vector_search(query, top_k=top_k * 2)  # 异步
    fused = _rrf_fuse(bm25_results, vector_results)
    fused.sort(key=lambda x: x["score"], reverse=True)
    reranked = await rerank(query, fused)
    return reranked[:top_k]
```

**候选集取 2 倍（10 条），最后才收成 5 条**——因为融合和重排都是"**重新排序**"：候选池太小，好东西可能在第一轮就被切掉了，后面再怎么重排也捞不回来。**召回阶段要宽，排序阶段才收窄。**

---

## 六、RRF 融合：为什么不做加权求和

```python
def _rrf_fuse(bm25_results, vector_results, k: int = 60) -> list[dict]:
    """
    RRF 融合算法：不比较原始分数，只比较排名位置
    公式: RRF(d) = Σ 1/(k + rank_i(d))
    """
    scores: dict[str, dict] = {}   # key = text 前 100 字作为去重标识
    for rank, item in enumerate(bm25_results):
        scores[_key(item["text"])] = {"text": ..., "score": 1.0 / (k + rank + 1)}
    for rank, item in enumerate(vector_results):
        rr = 1.0 / (k + rank + 1)
        if key in scores:  scores[key]["score"] += rr      # 两路都命中 → 累加
        else:              scores[key] = {...}
```

### 6.1 它绕开的问题

最直觉的融合是 **`0.5 * bm25_score + 0.5 * vector_score`**。**这行不通**，因为：

- BM25 的分数是**无上界的正数**（可能 0.3，也可能 25.7，取决于语料）；
- 余弦相似度在 **[-1, 1]** 之间。

两个不同量纲的数**不能直接相加**——那个 `0.5` 权重是假的，实际上永远是 BM25 说了算。要修就得先归一化（min-max）或者调权重，**换一个知识库就得重调一遍**。

**RRF 全部绕开**：它**不看分数，只看排名**。第 1 名给 `1/(60+1)`，第 2 名给 `1/(60+2)`……**排名是天然同量纲的**。

`k=60` 是文献里的常用值，作用是**压平差距**：让第 1 名和第 2 名的分差不要太大，从而**多路共识**比**单路第一**更重要。

### 6.2 "共识加分"是 RRF 最妙的性质

两路都命中的文档，`score` 会**累加**：

```
某条 return 政策：BM25 第 1 名 (1/61) + 向量第 3 名 (1/63) ≈ 0.0323
另一条：          BM25 第 2 名 (1/62)                      ≈ 0.0161
```

**直接被拉开一倍。** 这正是我们想要的：**被两个独立检索器同时认可的，大概率真的相关**。这个性质不需要额外写代码，是公式自带的。

> **一个可讲的局限**（诚实版）：去重 key 用的是 `text[:100]`——**前 100 字相同才算同一条**。理论上两条不同的块如果前 100 字一样会被误合并（比如都以前缀"退货运费规则："开头）。当前语料里没出事，但换成模板化严重的文档就要改（比如用 `source + 首行` 或直接给每块一个稳定 id）。

---

## 七、重排：Bi-Encoder vs Cross-Encoder

```python
async def rerank(query: str, documents: list[dict], top_n: int = 10) -> list[dict]:
    if not documents:
        return []
    resp = TextReRank.call(model="gte-rerank-v2", query=query,
                           documents=[d["text"] for d in documents], top_n=top_n)
    if resp.status_code != 200:
        return documents           # ← 重排失败就退回原顺序，不丢结果
    ...
```

这是**两级排序**，两者是模型结构上的差别，不是"再排一次"这么简单：

| | 向量检索（Bi-Encoder） | 重排（Cross-Encoder） |
|---|---|---|
| 怎么算 | query 和 doc **分别**编码成向量，再算余弦 | **query + doc 拼在一起**送进模型，直接输出相关分 |
| 能不能预计算 | **能**——文档向量离线算好存库 | **不能**——必须现场两两算 |
| 成本 | 便宜（一次向量运算 + 近似搜索） | 贵（N 条候选就是 N 次模型推理） |
| 精度 | 一般 | **高**（query 和 doc 的词能互相"看见"） |

> **一句话理解**：Bi-Encoder 是"**各自拍张照，然后比照片像不像**"；Cross-Encoder 是"**两个人当面聊一次**"。当面聊当然准，但只能用在**小候选集**上——所以流程必然是 **召回（宽、快、便宜）→ 重排（窄、准、贵）**。这个"粗召回 + 精排序"的漏斗结构是搜推领域通用的。

**那个失败降级要单独说**：`status_code != 200` 就 `return documents`——**重排挂了，检索结果照给**，只是没重排。因为重排是"锦上添花"的一层，**它不该有能力把整个检索带崩**。这和前面几章反复出现的降级原则是同一个：**附加能力失败，退回原能力，不要往下传错误。**

---

## 八、接进 Agent：`search_knowledge_base`

检索器写完了，但它得变成**模型能调的工具**（第 02 章那套）。`backend/tools/service_tools/rag_search.py`：

```python
class RAGTool(BaseTool):
    spec = ToolSpec(
        name="search_knowledge_base",
        description="从电商知识库中检索相关文档（退货政策、物流说明、常见问题等）。"
                    "用于回答用户关于政策、流程、售后等问题。",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string", "description": "要检索的问题或关键词"}},
                    "required": ["query"]},
    )

    async def execute(self, query: str) -> ToolResult:
        retriever = get_retriever()
        results = await retriever.search(query, top_k=5)
        if not results:
            return ToolResult(success=True, data={"结果": "未找到相关文档"})
        formatted = [f"[{r['source']}] {r['text'][:300]}" for r in results]
        return ToolResult(success=True, data={"检索结果": formatted})
```

三个设计点：

**① 它属于 `service` 角色**（第 02 章的 `TOOL_SCOPES`）。客服 Agent 只能看到这一个工具，分析 Agent 看不到它——**它在 schema 里根本不存在**。

**② `[source]` 前缀要带上。** 每段结果前面标 `[returns.md]`，好处有二：模型能说"根据退货政策文档…"（**可溯源**）；片段之间不会串味。

**③ "检索不到"是 `success=True`，不是失败。**

```python
if not results:
    return ToolResult(success=True, data={"结果": "未找到相关文档"})
```

这个选择很讲究。**检索不到是正常的业务结果，不是工具故障。** 如果返回 `success=False`：

- 第 02 章契约层的错误文案是"**请补齐后重新调用**"——模型会以为参数错了；
- 于是它**换个措辞再查一遍**，查不到再查一遍……**白白烧掉好几轮**。

> 这个区分值得记：**`success=False` 是"你调用得不对"，`success=True + 空数据`是"调用对了，但没有"。** 混为一谈会让模型陷入无意义重试。

### 8.1 知识库重建后，为什么要自动失效

BM25 的语料是**全量加载进内存**的（`HybridRetriever(documents)`）。如果只在首次调用时加载一次，就会出这个经典事故：

> 跑完 `build_kb.py` 重建知识库 → 后端**还在用旧语料** → 演示时搜出来的还是老内容 → **必须重启后端才生效**。

注释直接点名了：*"演示时最容易翻车的地方"*。解法是**语料指纹**：

```python
def _fingerprint_of(docs: list[dict]) -> str:
    """语料指纹：对全部 (source, text) 排序后取 md5"""
    raw = "\n".join(sorted(f"{d.get('source','')}\x00{d.get('text','')}" for d in docs))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def get_retriever(force: bool = False) -> HybridRetriever:
    docs, fingerprint = _load_docs()
    if _retriever is None or force or fingerprint != _fingerprint:
        _docs_cache, _fingerprint = docs, fingerprint
        _retriever = HybridRetriever(docs)     # 变了才重建
    return _retriever
```

**为什么不拿"点 id 集合"当指纹**（看起来更省事）？注释也答了：

> 现在 `build_kb` 用 uuid4 生成 id，每次重建都会变、比 id 也能发现；但**只要哪天改成由内容决定的确定性 id，同长度重建就会被漏掉**。直接对内容取指纹，两种策略都覆盖。

这是**防御性设计**：不依赖"id 一定是随机的"这个当前实现细节。**内容变了就是变了**，怎么生成 id 都不影响。

> 代价是每次检索前都要 `scroll` 一遍全量语料。本地模式下是内存扫描，几十上百个片段可以忽略；注释里也写了**将来换远端 Qdrant 就加 TTL**，不必每次拉全量。

---

## 九、验证一下

```bash
# 建库（会先清空旧块）
PYTHONPATH=. python scripts/build_kb.py
```

期望输出：每个文件"多少字符 → 多少个片段"，最后"共 N 个片段已存入 Qdrant"。

```bash
# 直接问检索器（绕过 LLM）
PYTHONPATH=. python scripts/rag_eval.py
PYTHONPATH=. python scripts/rag_answer_eval.py
PYTHONPATH=. python scripts/rag_quality_eval.py
```

> 这三个脚本分别测**检索层 / 答案层 / 质量**，具体指标和"面试怎么讲这些数字"是另一份材料的主题（`12-RAG评测四层叙事.md`）。

**手工验证最小闭环**（最推荐，不用跑整套评测）：

```python
from backend.tools.service_tools.rag_search import RAGTool
r = await RAGTool().execute(query="退货要多久")
print(r.data)
```

然后**故意测边界**：

```
query="退货要多久"          → 应该命中 returns.md
query="退钱多久能到账"      → 换说法，测**向量**那一路
query="SF-100 运费"         → 精确串，测 **BM25** 那一路
query="今天天气怎么样"      → 应该 success=True + "未找到相关文档"
```

**前两个都命中，说明混合检索是真在工作**（只有 BM25 时第 2 条会挂，只有向量时第 3 条会挂）。最后一条验证的是 8 节那个"空结果不算失败"的设计。

---

## 本章小结

- **两阶段**：离线 `build_kb.py` 建库（解析→切块→向量化→入库），在线检索（BM25 + 向量 → RRF → 重排）
- **切块粒度对着"答案单元"切**：`#` 标题强制开新块、段落不腰斩、补 `OVERLAP=40` 防骑边界；**结构标记 > 长度限制 > 平均切分**
- **必须混合的原因**：向量认"换说法"，BM25 认"精确串"，两者瞎的地方正好互补
- **BM25 按字切中文**是**有意识的取舍**——精度交给重排兜底
- **RRF 不比重分数只比排名**（BM25 分和余弦分不同量纲，加权求和是假的）；**两路共识自动累加加分**
- **重排是 Cross-Encoder**：query+doc 一起过模型，准但贵 → 只能用在**粗召回之后**的窄候选集上；**它挂掉要退回原顺序，不能带崩检索**
- **检索不到 = `success=True` + 空数据**（不是失败，否则模型会无意义重试）
- **语料指纹**让知识库重建后**不用重启后端**；指纹取内容不取 id，是防御性设计

---

## 练手挑战

**挑战一（必做）：给 `chunk_text` 写测试**

准备一段带 `##` 标题的 md，断言：① 每条标题都在某个块的开头；② 除标题块外，相邻块的**开头 40 字**等于上一块的结尾 40 字（这就是 `OVERLAP` 在起作用）。

**验收**：改 `OVERLAP = 0` 后第二条断言失败——证明你测的确实是它。

**挑战二（推荐）：验证 RRF 的"共识加分"**

构造两份假结果：一条只在 BM25 第 1 名，另一条同时出现在 BM25 第 2 和向量第 1。调 `_rrf_fuse`，确认**后者分更高**——即使它两路都不是第一。

**挑战三（加分）：修掉第六节那个局限**

把去重 key 从 `text[:100]` 改成更稳的东西（提示：给每个片段在 payload 里存一个稳定的 `chunk_id`，`build_kb` 时写入、检索时带回）。注意说清：为什么 `text[:100]` 会在模板化文档上出错？

---

## 下一步

知识库解决了"**临时的、公共的**"知识。但还有一类信息既不在数据库、也不在知识库里——

**"这个用户上次说他关注的是充电宝类目"** 该存哪？第 07 章看**记忆系统**：三层记忆怎么分工、怎么在后台提炼、失败怎么重放。

→ [07 记忆系统](07-记忆系统.md)
