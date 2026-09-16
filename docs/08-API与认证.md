# 08 · API 与认证

> 前面七章都在讲"Agent 怎么想"。这一章讲"**它怎么被用起来**"：
> 接口怎么暴露、**谁能调**、流式怎么推给浏览器、服务怎么启动和收尾。

---

## 本章目标

读完这一章，你会明白：

1. 一个请求从 HTTP 进来，到拿到 `current_user`，中间发生了什么
2. JWT 怎么签、怎么验，为什么要用 **bcrypt** 存密码
3. 为什么"token 验过了"**还要再查一次库**
4. 三层数据隔离靠什么保证（会话 / 文件 / 记忆）
5. SSE 为什么必须在 `finally` 里发结束信号
6. **SSE 接口的异常为什么不能靠全局中间件兜底**
7. 服务启动时该做什么（以及为什么那些事失败**不能让服务起不来**）

---

## 一、先看痛点：Agent 不能只是个 Python 对象

到第 07 章为止，所有东西都还是"能在脚本里跑"的状态：

```python
graph = build_supervisor(llm, registry)
final = await graph.invoke({"goal": "..."})
```

要变成一个**能给别人用的产品**，缺三样：

| 缺什么 | 后果 |
|---|---|
| **HTTP 接口** | 前端调不到 |
| **认证** | 谁都能读别人的会话、文件和记忆 |
| **流式推送** | 一份 30 秒才生成的报告，用户要盯着空白页等 30 秒 |

`backend/api/` 就是这个"外壳"。目录结构本身就说明了分层：

```
backend/api/
├── app.py            装配：中间件 + 路由 + lifespan
├── middleware.py     请求日志 + 异常兜底
├── deps.py           依赖注入：get_current_user
├── schemas/          请求/响应的 Pydantic 模型
└── routes/           七个路由模块
    ├── auth.py       注册 / 登录      ← 本章重点
    ├── chat.py       对话 + SSE       ← 第 05/07 章见过
    ├── session.py    会话列表 / 重命名 / 删除
    ├── memory.py     记忆管理
    ├── documents.py  文档下载
    ├── upload.py     文件上传
    └── dashboard.py  经营看板
```

**每个路由文件只管"HTTP 这层的事"**：解析请求、拿当前用户、调下层、组装响应。业务逻辑在 `agents/` 和 `core/` 里——这是从第 01 章就开始的依赖倒置一路延续下来的。

---

## 二、认证：JWT + bcrypt

`backend/core/security.py` 全部只有 25 行，但它承担了整个系统的安全边界。

### 2.1 密码：不存明文，存 bcrypt 哈希

```python
def hash_password(password: str) -> str:
    """密码 → bcrypt 哈希（自动加盐）"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
```

**两个必答的面试点**：

**① 为什么不存明文？** 数据库一旦泄露，所有用户的密码直接暴露。而且用户到处复用密码，泄露一个等于泄露一串。

**② 为什么用 bcrypt 而不是 MD5/SHA256？**

| | MD5 / SHA256 | bcrypt |
|---|---|---|
| 设计目标 | **快**（校验文件完整性） | **慢**（故意拖时间） |
| 破解成本 | 一张彩虹表 / 显卡每秒算几十亿次 | 单次约 100ms，**暴力枚举成本极高** |
| 盐 | 要自己加 | `gensalt()` **自动加盐**并存在哈希串里 |

**"慢"在这里是优点。** 注意哈希串是自带盐的——`bcrypt.checkpw` 能从哈希串里读出盐，所以**不需要单独存一个 salt 字段**。

### 2.2 Token：签什么、怎么签

```python
SECRET_KEY = settings.jwt_secret_key        # 从配置读，不写死在代码里
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = settings.jwt_expire_minutes   # 默认 24 小时，.env 可调

def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> int:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    return int(payload["sub"])
```

四个设计点：

| 点 | 说明 |
|---|---|
| **`sub` 存 user_id，且必须是字符串** | JWT 规范要求 `sub` 是字符串；而且直接放 user_id 意味着**token 里没有任何敏感信息**（没有用户名、没有密码哈希） |
| **`exp` 是必填的** | 不设过期 = token 一旦泄露永久有效。过期校验**由 `jwt.decode` 自动完成**，超时会抛 `ExpiredSignatureError` |
| **`HS256`（对称签名）** | 同一个 `SECRET_KEY` 签名和验签。够用且简单；要"第三方能验签但不能签发"才需要 RS256 |
| **`SECRET_KEY` 从 settings 读** | 写死在代码里 = 提交到 Git = 谁都能签发任意用户的 token。代码注释专门点了这一句 |

**JWT 的结构**（`header.payload.signature`，点分三段，Base64 编码）——注意：

> **payload 只是 Base64 编码，不是加密。** 任何人拿到 token 都能解出 `{"sub": "3", "exp": ...}`。
> **所以 JWT 里绝不能放密码、手机号这类敏感信息。** 它的价值在于**签名防篡改**（改了 payload 签名就对不上），而不是保密。

### 2.3 顺手一处不一致

```python
# security.py
"exp": datetime.utcnow() + timedelta(minutes=...)
```

```python
# core/memory/store.py
def _utcnow() -> datetime:
    """naive UTC 当前时间 —— 与 SQLite func.now()(UTC) 同口径，避免用将被弃用的 datetime.utcnow"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

**同一个项目里，记忆模块专门封了个 `_utcnow()` 来避开 `datetime.utcnow()`，而这里还在直接用。** `datetime.utcnow()` 在 Python 3.12 起已被标记弃用（它返回的是 naive datetime，容易被误当本地时间）。

**功能上没错**（JWT 只要求是个 UTC 时间戳），但既然项目里已经有正确写法了，统一一下更好——这类"同一件事两种写法"是新人读代码时最容易困惑的地方。

---

## 三、依赖注入：`get_current_user`

认证写好了，但**每个接口都要用它**。FastAPI 的解法是**依赖注入**：`backend/api/deps.py`

```python
security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 token 解析出 user_id → 查库 → 返回 User 对象"""
    # 1. 从请求头取 token（HTTPBearer 已帮你取好了）
    token = credentials.credentials
    # 2. 验证 token，取出 user_id（无效会抛 jwt 异常）
    try:
        user_id = verify_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效的token或已过期")
    # 3. 查库拿用户
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user
```

接口里只要一行：

```python
@router.post("/chat")
async def chat(request: ChatRequest, current_user: User = Depends(get_current_user)):
    ...
```

### 3.1 这 15 行干了三件事

| 步 | 谁做的 | 做了什么 |
|---|---|---|
| 1 | **`HTTPBearer`** | 自动从 `Authorization: Bearer xxx` 头里取出 token；**没带就自动返回 403**，不用自己判空 |
| 2 | `verify_token` | 验签 + 验过期，取出 `user_id` |
| 3 | **查库** | 拿 `User` 对象 |

### 3.2 为什么第 3 步非要查库？token 不是已经验过了吗

这是**最该问出来的一个问题**。答案是：**"token 有效"和"这个用户还在"是两件事。**

```
用户被删除了 / 被禁用了
    ↓
他手里的 token 在过期前依然"验签通过"（签名没变、exp 没到）
    ↓
不查库的话，接口照样给他放行
```

**JWT 是自包含的**（服务器不存 session），代价就是**它无法感知"用户状态变了"**。所以必须**每次请求回查一次库**，用"这个人现在还在不在"来兜底。

> 面试可以这样讲：**"JWT 省掉了查 session 的存储，但省不掉查用户——除非你接受'删了用户他还能用到 token 过期'。"**

### 3.3 两种 401 分开报

```python
except jwt.PyJWTError:
    raise HTTPException(status_code=401, detail="无效的token或已过期")
...
if user is None:
    raise HTTPException(status_code=401, detail="用户不存在")
```

状态码一样（都是 401），但**文案分开**。区别在于：

- 第一种是"**你得重新登录**"（token 坏了）；
- 第二种是"**登录也没用**"（账号没了）。

对前端来说，第一种要跳登录页，第二种该提示"账号不存在"。**错误信息的第一受益人是排查问题的人**——这一条从第 02 章的工具契约层开始，一路贯到这里。

---

## 四、数据隔离：谁能看到谁的东西

认证解决了"你是谁"，**授权**解决"你能碰什么"。这个项目的做法非常朴素：**每个查询都带 `user_id`**。

三个独立存储，三处都要隔离：

| 存哪 | 隔离方式 | 在哪一章 |
|---|---|---|
| SQLite 消息/会话 | `where(session_id == sid, user_id == uid)` **双条件** | 03 |
| SQLite 上传文件 | 同上 | 03 |
| SQLite 长期记忆 | `where(user_id == uid)` | 07 |
| **Qdrant 向量** | `Filter(must=[FieldCondition(key="user_id", ...)])` | 06/07 |
| **生成的文件** | 按 `user_id` 分目录落到 `outputs/` | 02 |

**最容易被忽略的是后两个**。向量库和文件系统**不是关系数据库**，没有"where 条件"这种天然隔离——**必须手动加过滤**：

```python
# qdrant_client.py
if user_id is not None:
    query_filter = Filter(must=[
        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
    ])
```

```python
# 文档工具注册时注入 user_id，生成物隔离到各自的 outputs/ 子目录
register_document_tools(registry, llm, context={
    "user_id": current_user.id, "session_id": sid, "on_document": queue.put_nowait,
})
```

> **关键在 `user_id is not None` 这个判断**：不传就**不过滤**（全库搜）。这在"自己查自己"的场景下是无害的，但如果哪天后端某处忘了传 `user_id`，就变成了**跨用户泄漏**，而且**不会报错**。
>
> 更稳的写法是**默认必须过滤**，只在显式传入 `user_id=None` 时才放开——不过当前调用方都传了，属于可以改进的点。

**一处特别值得学的细节**（第 03 章提过，这里是它的落地）：

```python
# store.py delete_session
"""注意：用户画像长期记忆（qdrant user_memories / user_profile 表）
是跨会话的"这个人"的记忆，不属于某个会话，删单个会话不清它。"""
```

**删会话要连带删消息、删上传文件，但不删长期记忆。** 这个边界不写下来，后来的人（或者三个月后的自己）很容易"顺手一起删了"。

---

## 五、SSE：流式怎么推

第 01 章讲过 `chat_stream` 里的 **Queue 桥接**（同步线程 → 异步队列）。这里是同一套手法的**第二次出现**，但桥的两端换了：

| | 第 01 章（LLM 层） | 第 08 章（API 层） |
|---|---|---|
| 生产者 | `requests` 的同步读流线程 | **Agent 图**（`on_event` 回调） |
| 队列 | `asyncio.Queue[str]` | `asyncio.Queue[dict]` |
| 消费者 | `chat_stream` 生成器 | **`event_stream` 生成器**（推 SSE） |
| 结束信号 | `None` | `None` |

```python
async def event_stream():
    async def run_graph():                 # 后台任务：跑整个多 Agent 图
        try:
            graph = build_supervisor(llm, registry, on_event=lambda evt: queue.put(evt))
            final = await graph.invoke({...})
            report = final.get("report", "")
            if report:
                await save_message(sid, current_user.id, "assistant", report)
            _spawn(_extract_in_background(...))
        except BudgetExceeded as e:
            ...
        except Exception as e:
            logger.exception("Agent 链路异常")
            queue.put_nowait({"type": "error", "message": f"分析失败：{e}"})
        finally:
            queue.put_nowait({"type": "usage", "usage": budget.snapshot()})
            await queue.put(None)          # 结束信号：无论成败必达

    task = asyncio.create_task(run_graph())

    while True:                            # 边等边推
        evt = await queue.get()
        if evt is None:
            break
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    await task
    yield f"data: {json.dumps({'type': 'session', 'session_id': sid}, ...)}\n\n"
```

### 5.1 为什么要拆成两个任务

**这是 SSE 的核心结构**：`run_graph` 是**生产者**，`event_stream` 的 while 循环是**消费者**。

如果写成"跑完再推"，那就退化成了普通接口（用户还是要等全程）。**拆开之后，图每产出一个事件就立刻到浏览器**——这就是"打字机效果"和"实时活动流"的来源。

```python
task = asyncio.create_task(run_graph())    # 图在后台跑
while True:                                 # 主协程只负责转发
    evt = await queue.get()
```

### 5.2 结束信号必须在 `finally` 里

```python
finally:
    queue.put_nowait({"type": "usage", "usage": budget.snapshot()})
    await queue.put(None)          # 结束信号：无论成败必达
```

注释写得很直白：*"Agent/LLM 链路失败也必须把结束信号送出去（finally），**否则 SSE 永远挂起、前端无限转圈**。"*

**如果 `None` 没送出去会怎样？** 消费者的 `await queue.get()` 会**永久阻塞**——连接不关、前端转圈、浏览器一直在等。这是个**不会报错、只会挂住**的故障，最难查。

**为什么用 `queue.put`（`await`）而不用 `put_nowait`** 送结束信号？因为它**必须成功**——队列满也不能丢。前面的业务事件丢了最多是少一次界面更新，**结束信号丢了就是挂起**。

### 5.3 事件推送的次序是有讲究的

```
… token 事件（逐字） → report 事件（完整报告）
usage 事件（本轮花了多少）
None（结束）
session 事件（告诉前端 session_id）
```

两个细节：

- **`usage` 必须在 `None` 之前推**。注释：*"无论成败都让用户看到这轮花了多少，也是排查'这轮为什么慢/贵'的第一手数据。"* 推在 `None` 之后就到不了了（流已结束）。
- **`session` 事件最后单独推**（在 `await task` 之后）。为什么不在 `finally` 里一起？因为新会话的 `sid` 要等图跑完才知道——而且它**必须送达**，所以放在流的最后、确定 `run_graph` 已经结束之后。

### 5.4 响应头两个都不能少

```python
return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    },
)
```

| 头 | 作用 |
|---|---|
| `Cache-Control: no-cache` | 防止中间层/浏览器缓存整段流 |
| **`X-Accel-Buffering: no`** | **告诉 Nginx「别缓冲」** |

**`X-Accel-Buffering` 这个头值得单独记**：Nginx 默认会**缓冲上游响应**，攒够一块才发给客户端。结果就是——**后端明明在逐字推，浏览器却要等全部生成完才一次性显示**。本地开发（直连 uvicorn）一切正常，**一上 Nginx 就"流式失效"**，是最经典的部署期 bug。

> 一句经验：**流式的 bug 十有八九是"中间有东西在缓冲"**（Nginx / 网关 / 某些云负载均衡）。

---

## 六、SSE 接口的异常为什么不能靠中间件兜底

这是本章**最值得想明白的一个点**，也是很容易踩的坑。

先看中间件：

```python
class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception(f"[{request.method}] {request.url.path} 发生未捕获异常")
            return JSONResponse(status_code=500, content={"error": "服务器内部错误", ...})
```

看起来很周全。**但对 SSE 接口它没用。** 原因在 HTTP 协议本身：

```
SSE 的响应是"边算边发"的
  → StreamingResponse 返回时，状态码 200 和响应头【已经发出去了】
  → 图的异常发生在【响应头之后】
  → 这时候中间件想改成 500 已经来不及了
```

**响应头一旦发出，HTTP 状态码就改不了了。** 所以：

- 中间件能兜住的是"**还没开始响应就炸了**"（比如参数校验、数据库连接失败）；
- **图跑到一半炸了，只能靠流内的 `error` 事件告诉前端**。

所以 `run_graph` 里的 `try/except` **不是冗余的**——它是 SSE 场景下**唯一**能把错误送出去的通道：

```python
except Exception as e:
    logger.exception("Agent 链路异常")
    queue.put_nowait({"type": "error", "message": f"分析失败：{e}"})
```

> **结论**：SSE 接口需要**自己一套"流内错误协议"**。普通接口的错误是"HTTP 状态码 + body"，流式接口的错误是"**一个 type=error 的事件**"。这不是设计冗余，是协议限制逼出来的。

---

## 七、三个中间件：注册顺序就是包裹顺序

```python
# ===== 中间件（注册顺序 = 从内到外的包裹顺序）=====

# ① 最内层 — 跨域处理（OPTIONS 预检直接拦截，不进入业务逻辑）
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_methods=["*"], allow_headers=["*"])

# ② 中间层 — 请求耗时日志
app.add_middleware(RequestLogMiddleware)

# ③ 最外层 — 异常兜底
app.add_middleware(ExceptionHandlerMiddleware)
```

**注意 `add_middleware` 的顺序是"后加的在最外层"**（和直觉相反），所以：

```
请求 → ExceptionHandler → RequestLog → CORS → 路由
```

这个次序是**刻意**排的，每一层的位置都有理由：

| 层 | 为什么在这个位置 |
|---|---|
| **ExceptionHandler 最外层** | 才能兜住**所有**内层抛的异常，包括日志中间件自己的 |
| **RequestLog 在中间** | 它在 ExceptionHandler **里面**，所以**异常请求也能被打上耗时日志**（否则报错的请求全都没有耗时记录，恰恰这些最需要看） |
| **CORS 最内层** | OPTIONS 预检**直接被它拦掉**，不进入业务逻辑 |

### 两个细节

**① CORS 用白名单，不用 `"*"`**

```python
# 白名单而非 "*"：接口带 JWT，通配符等于把跨域边界完全放开
allow_origins=settings.cors_origins
```

注释把理由写清楚了：**接口是带 JWT 的**。`allow_origins=["*"]` 配合 `allow_credentials` 时浏览器本来就会拒绝，但即使不带 cookie，"任意网站都能读你的 API 响应"也是不该开的门。

**② 耗时既进日志也进响应头**

```python
logger.info(f"[{request.method}] {request.url.path} → {response.status_code} ({elapsed_ms:.2f}ms)")
response.headers["X-Request-Time-ms"] = f"{elapsed_ms:.2f}"
```

日志给运维看，**响应头给前端/调试看**——用 `curl -i` 就能看到每个请求花了多久，不用去翻服务端日志。

**③ 异常兜底不泄 traceback**

```python
# 生产环境不泄路 traceback，这里用通用提示
return JSONResponse(status_code=500, content={"error": "服务器内部错误", "path": str(request.url.path)})
```

**`logger.exception` 记完整栈给开发看，返回给用户的是通用文案**——客户端拿不到堆栈、SQL 语句、文件路径这些信息。这是安全基线：报错详情只该出现在服务端日志里。

---

## 八、启动与收尾：`lifespan`

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动：幂等补建缺失的表（新增表不用重跑 init_db，create_all 只建缺的，不清已有数据）

    顺带做一次记忆对账（P1-5）… 对账失败绝不能让服务起不来（记忆是辅助功能）。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        report = await reconcile_memory()
        if report["repaired"] or report["orphans"] or report["failed"]:
            logging.getLogger(__name__).warning("启动记忆对账：%s", report)
    except Exception:
        logging.getLogger(__name__).exception("启动记忆对账失败（不影响服务启动）")
    try:
        await retry_pending_extracts()
    except Exception:
        logging.getLogger(__name__).exception("重放记忆提炼失败（不影响服务启动）")
    yield


app = FastAPI(title="掌柜 · 电商运营 AI 助手 API", lifespan=lifespan)
```

### 8.1 启动做三件事

| 事 | 对应哪一章 | 为什么放启动时 |
|---|---|---|
| `create_all` | 03 | **幂等补表**：新加了模型不用重跑 `init_db.py`（那个会清库），启动时补缺的 |
| `reconcile_memory` | 07 | **校准索引**：SQLite 是事实源、Qdrant 是索引，进程被杀会留下不一致 |
| `retry_pending_extracts` | 07 | **重放失败任务**：上次没跑完的记忆提炼，开机补跑 |

**这三个都是"幂等的自愈动作"**——正因为幂等，放启动时反复执行才安全。这也是第 07 章那条"**重放安全的前提是写路径幂等**"的又一次兑现。

### 8.2 "失败不影响启动"是刻意的

两个 `try/except` 都只记日志、**不 raise**。注释：*"对账失败绝不能让服务起不来（记忆是辅助功能）。"*

**判断标准很简单：这个功能挂了，主流程还能不能跑？**

```
对账挂了   → 记忆召回可能不全，但对话照样能聊   → 记日志，继续启动
建表挂了   → 服务根上就废了（没有 try 包住）    → 直接崩，必须让人看见
```

**该崩的地方让它崩，该降级的地方安静降级。** 这和前面几章的降级原则是同一个判断标准：**核心能力必须硬失败（不然是静默的灾难），辅助能力必须软降级（不然是没必要的雪崩）。**

---

## 九、验证一下

**① 认证链路**

```bash
# 注册（直接返回 token）
curl -X POST localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"123456","name":"测试"}'

# 登录
curl -X POST localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"123456"}'

# 不带 token → 403（HTTPBearer 拦的）
curl -i localhost:8000/api/session/list

# 带错 token → 401「无效的token或已过期」
curl -i localhost:8000/api/session/list -H "Authorization: Bearer 瞎写的"
```

三种结果（**能拿到 token / 403 / 401**）说明认证三层都在工作。

**② SSE 流式**

```bash
# -N 关掉 curl 自己的缓冲，否则你看不到"逐字"效果
curl -N -X POST localhost:8000/api/chat/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message":"本周销售情况"}'
```

期望：**能明显看出事件是一条条到的**，最后有 `usage` 和 `session`，流正常关闭（不是超时断开）。

**③ 隔离验证**（最该做的一项）

用 A 用户的 token 去拉 B 用户的会话 id：

```bash
curl -i localhost:8000/api/session/<B的sid>/messages -H "Authorization: Bearer <A的token>"
```

期望：**拿到空列表或 404，绝不能返回 B 的消息**。顺手也测一下文档下载接口——生成物是按 `user_id` 分目录的，不该能跨用户下载。

**④ 中间件**

```bash
curl -i localhost:8000/不存在  # 看响应头有没有 X-Request-Time-ms
```

---

## 本章小结

- **分层**：`routes`（HTTP 层）→ `deps`（认证）→ `schemas`（数据模型）；业务逻辑全在 `agents/`、`core/`
- **密码用 bcrypt**：故意"慢"、自动加盐、盐存在哈希串里；**JWT 的 payload 只是 Base64，不是加密**，不能放敏感信息
- **JWT 验过了还要查库**：token 有效 ≠ 用户还在（JWT 自包含的代价就是感知不到用户状态变化）
- **隔离靠"每个查询都带 user_id"**：SQLite 用 where，**Qdrant 用 Filter、文件系统按目录**——后两个没有天然隔离，必须手动加
- **SSE = 生产者/消费者两个任务**：`run_graph` 往队列推、`event_stream` 边等边推；**结束信号 `None` 必须在 `finally` 里发**（不发就是永久挂起）
- **SSE 的异常不能靠全局中间件**：响应头已发出、状态码改不了 → 必须走**流内的 `error` 事件**
- **中间件注册顺序 = 包裹顺序**（后加的在最外层）；CORS 用白名单、异常兜底不泄 traceback
- **启动做三件幂等的自愈动作**（补表 / 对账 / 重放）；判断"失败要不要让服务起不来"的标准是 **核心能力硬失败、辅助能力软降级**

---

## 练手挑战

**挑战一（必做）：给 `/chat/stream` 加一个"心跳"**

SSE 长连接如果中间层有超时（Nginx 默认 `proxy_read_timeout` 60s），一次分析超过 60 秒没产出事件就会被断开。加一个后台任务**每 15 秒推一条 `{"type": "ping"}`**。

**提示**：参考 `_spawn` 的写法；**别忘了在 `finally` 里取消它**——不然流结束了心跳还在推。

**验收**：故意让图跑 90 秒以上，连接不被断开。

**挑战二（推荐）：把 Qdrant 的过滤改成"默认必须过滤"**

现在 `search_similar` 是 `user_id is not None` 才过滤。改成一个**默认需要的参数**（比如 `user_id: int`，去掉默认值），然后跑一遍 `tests/` 找哪些调用点会因此报错。

**验收**：所有跨用户可查的调用点都浮出水面——**这一步的价值就是"让忘记传 user_id 在编译期/测试期就暴露，而不是等到泄漏发生"**。

**挑战三（加分）：统一 `utcnow`**

把 `security.py` 里的 `datetime.utcnow()` 换成和 `store.py` 一致的写法（抽到公共位置）。验证：签发 → 解码 token，`exp` 时间戳和之前**应该完全一致**（都是 UTC 时间戳），确认这是纯粹的写法统一、不改变行为。

---

## 下一步

到这里，从 LLM 抽象层到 API 外壳，后端整条链路都通了。最后一章看**前端**——活动流怎么把 SSE 事件渲染成"看得见的进度"、图表从哪来、以及**整个项目做下来值得总结什么**。

→ [09 前端与总结](09-前端与总结.md)
