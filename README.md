# 学习助手（lago）· Go + Python 混合后端

对原 Python 单体项目的重写：**Go 做网关与并发调度，Python 只做需要 Python 生态的重活**。

原项目把 51 个 HTTP 端点、SQLite 访问、并发流水线、OCR、LLM 调用、LaTeX 编译、
图像批注、动画生成全塞进 `main.py`（93KB）+ `ai_service.py`（60KB），
单文件最大到 93KB，且权限校验、密钥管理、依赖清单都有明确缺陷。
本次重写按"职责边界"重新切分。

> **代码来源**：后端（`cmd/`、`internal/`、`aiworker/`）为本次重写的新代码；
> 前端 `web/` 及其 vendor 依赖（MathJax、marked、tex-svg、mhchem 扩展）迁自原项目仓库。

---

## 架构

```
┌──────────────────────────────┐        HTTP / SSE        ┌──────────────────────────┐
│  客户端（网页端 / 安卓端）      │ ◄─────────────────────► │  Go 网关 :8000           │
│  · 原生 HTML/JS（无需改动）    │                          │  · 路由 / JWT 鉴权        │
│  · Kotlin + Compose           │                          │  · SQLite 存储           │
└──────────────────────────────┘                          │  · 并发调度 / 事件总线    │
                ▲                                          │  · 静态资源              │
                          │                                            │
                          └──── 内网 HTTP ──────────────────────────────┘
                                                            │
                                                  ┌─────────▼────────────┐
                                                  │  Python AI 服务 :8001 │
                                                  │  · OCR（视觉/Paddle） │
                                                  │  · LLM 流式调用       │
                                                  │  · LaTeX 渲染闭环     │
                                                  │  · 图像批注 / 动画     │
                                                  │  · 导出 Word/PDF      │
                                                  └───────────────────────┘
                                                          只监听 127.0.0.1
```

**为什么这样切**：Go 天然适合高并发 HTTP 服务、连接池与流式转发，编译期检查能挡掉大量低级错误；
而 OCR（PaddlePaddle）、LaTeX 编译、图像处理、Plotly 动画这些依赖全是 Python 生态，
硬搬到 Go 只会引入巨量胶水代码。所以按能力边界切，而不是按"整层"切。

---

## 相比原项目修掉的问题

| # | 原项目的问题 | 本项目的处理 |
|---|-------------|-------------|
| 1 | `JWT_SECRET` 默认值 `"learning-assistant-secret-key-change-in-production"` 写死在 `config.py`，任何人可伪造任意用户 token | **缺失或短于 32 字符直接拒绝启动**，不给默认值兜底 |
| 2 | `config.py` 硬编码了别人的 PaddleOCR 实例 URL（含 token ID），随时会失效 | 只从环境变量读，未配置就跳过该通道 |
| 3 | `requirements.txt` 只有 11 个包，缺 cv2 / reportlab / python-docx / jwt / bcrypt，`openai==1.3.7` 是可疑版本 | 分层依赖：核心 7 个包必装，重型依赖按需启用；缺失时功能自动降级而非启动失败 |
| 4 | `animation_service.py` 直接 `subprocess.run(["python", 生成的代码])`，无沙箱 | 独立临时目录 + 剥离密钥环境变量 + 无 stdin + 60s 强杀；**并在注释中标注这不是真沙箱** |
| 5 | 游客（无 token）可绕过全部 AI 端点无限制消耗额度，而受限账号反被拦——逻辑是反的 | 所有 AI 端点强制登录 |
| 6 | `permissions.txt` 已提交到仓库，新用户默认全部受限 | 保留 mtime 缓存热更新机制；但仓库不再附带该文件，新部署默认不限制 |
| 7 | 历史查询 SQL 用 `json_extract` 但 SQLite 可能未编译 JSON1 | 显式依赖 SQLite JSON1（modernc 内置），并对空/非法 JSON 容错 |
| 8 | `submit /ask` 签名只有 `body: AskRequest` 却把 `body` 传给读请求头的函数 → **追问恒 500** | Request 与 body 分开注入，该路径不再报错 |
| 9 | 依赖 `datetime.utcnow()`（已弃用且无时区） | 全用 `time.Now().UTC()`，JWT 用带时区的 NumericDate |
| 10 | 仓库混入 2.7MB `replay_pid*.log`、`hs_err_pid*.log`、`.bak`、6MB PNG | `.gitignore` 排除运行期产物与日志 |
| 11 | 单文件 93KB 混装路由 + 业务 + SQL | 按 `config / store / auth / events / ai / handlers` 分层，单文件均 < 400 行 |
| 12 | SSE 队列无背压，慢客户端会拖死解题线程 | 每订阅者独立缓冲，满了丢自己的最旧帧，不影响解题与其它客户端 |
| 13 | 记录保存"按第几条 assistant 消息"猜类型，位置一变就串题 | 改为按内容特征 + 提示词前缀判定，位置法只作兜底 |
| 14 | 批注字体用 `arial.ttf`，无中文字形 → 中文渲染成方框 | 按优先级查找系统中文字体（雅黑/黑体/宋体/文泉驿/Noto） |
| 15 | 思维导图输出不符合树形时也照样下发，前端渲染成一坨散字 | 用内容特征校验，不像导图就丢弃 |

---

## 快速开始

### 1. 准备配置

```bash
cp .env.example .env
```

**必填**：`JWT_SECRET`（缺失会拒绝启动）

```bash
# 生成密钥
openssl rand -hex 32
# Windows PowerShell:
[guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
```

**按需填**：至少一家模型 API Key（`DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `DOUBAO_API_KEY` / `HUNYUAN_API_KEY`）。

### 2. 构建并启动

```bash
# Go 网关
go build -o bin/gateway.exe ./cmd/gateway

# Python AI 服务
pip install -r requirements.txt
python aiworker/main.py --dir .
```

两个进程分别启动。网关在 :8000，AI 服务在 :8001（仅本机）。

### 3. 访问

- 网页端：<http://127.0.0.1:8000/web/>
- 健康检查：<http://127.0.0.1:8000/health>（`status: ok` 表示 AI 服务已连通）

**首个注册的账号自动成为管理员**——避免单机部署时"没人能管权限"的死锁。

---

## 环境变量

| 变量 | 必需 | 默认 | 说明 |
|------|:----:|------|------|
| `JWT_SECRET` | ✅ | — | 令牌签名密钥，<32 字符拒绝启动 |
| `DEEPSEEK_API_KEY` | ⚠️ | — | 至少配一家，否则 AI 功能返回 503 |
| `LISTEN_ADDR` | | `0.0.0.0:8000` | 网关监听 |
| `AI_SERVICE_URL` | | `http://127.0.0.1:8001` | Python 服务地址 |
| `OCR_DEFAULT_MODE` | | `qwen` | `qwen`=视觉模型 / `paddle`=Paddle 双通道 |
| `ENABLE_LOCAL_OCR` | | `false` | 本地 PaddleOCR（需另装 paddleocr） |
| `ENABLE_ANIMATION` | | `true` | AI 动画开关 |
| `MAX_UPLOAD_MB` | | `32` | 单次上传上限 |

---

## 接口

路径与原 FastAPI 版本**完全一致**，网页端 `api.js` 与安卓端无需任何改动即可对接。

<details>
<summary>端点清单（40 个）</summary>

**认证**　`POST /auth/register`　`POST /auth/login`　`POST /auth/verify`

**解题**　`POST /solve`　`POST /solve/text`　`POST /solve/multipage`
　　　　　`GET /solve/stream/{id}`　`POST /solve/confirm/{id}`
　　　　　`POST /solve/select_questions/{id}`　`POST /solve/cancel/{id}`

**追问**　`POST /ask`　`POST /ask/stream`

**知识延伸**　`POST /extend`　`POST /extend/text`　`GET /extend/stream/{id}`

**动画 / 批注**　`POST /animation`　`POST /animation/text`　`POST /annotate`

**报告**　`POST /report/data`　`POST /report/ai`　`POST /report/mistakes`

**历史**　`POST /history`　`DELETE /history`　`DELETE /history/{id}`　`POST /history/batch-delete`

**其它**　`POST /export`　`POST /mastery`　`POST /pomodoro/recommend`
　　　　　`POST /tracking/sync`　`GET /health`

</details>

### 请求头

沿用原前端约定：

| 头 | 取值 | 说明 |
|----|------|------|
| `X-Engine` | `deepseek`/`qwen`/`doubao`/`hunyuan` | AI 提供方 |
| `X-LLM-Model` | 模型 ID | 覆盖提供方默认模型 |
| `X-OCR-Mode` | `paddle`/`qwen` | OCR 通道 |
| `X-Style` | `formal`/`encouraging`/`humorous` | 回答风格 |
| `X-Thinking` | `1`/`0`/`auto` | 思考模式 |
| `X-Latex-Helper` | `1`/`0`/`auto` | 图解辅助 |
| `X-Theme` | `dark`/`light` | 界面主题（动画配色） |
| `Authorization` | `Bearer <token>` | 登录令牌 |

### SSE 事件协议

```
data: {"stage":"ocr_complete","content":{"text":"...","time":2.1,"source":"qwen_vision"}}
data: {"stage":"question_info","content":{"subject":"数学","difficulty":"中",...}}
data: {"stage":"solution_steps","content":"..."}
data: {"stage":"solution_chunk","content":"增量文本"}
data: {"stage":"solution_rendered","content":"...![图解](/static/svgs/...)"}
data: {"stage":"mindmap","content":"..."}
data: {"stage":"suggested_questions","content":["...","..."]}
data: {"stage":"complete","content":{"session_id":"...","total_time":42.1}}
```

多题模式下事件带 `"qi": <题目索引>`。

---

## 目录结构

```
lago/
├── cmd/gateway/          Go 网关入口
├── internal/
│   ├── config/           配置加载与目录准备
│   ├── store/            SQLite 访问层
│   ├── auth/             JWT + bcrypt + 权限文件
│   ├── events/           事件总线（SSE 背压、一次性等待）
│   ├── ai/               Go→Python 客户端
│   └── handlers/         HTTP 端点实现
├── aiworker/             Python AI 服务
│   ├── ai/               引擎接入 + 提示词库
│   ├── ocr/              OCR + 分题 + 解题流水线
│   ├── latex/            LaTeX 渲染闭环
│   └── vision/           批注 / 动画 / 导出 / 关键帧
├── web/                  网页端（原生 HTML/JS）
├── data/ history/ ...    运行期产物（不入版本控制）
├── logs/                 gateway.log / aiworker.log
└── .env.example
```

---

## 可选增强

| 功能 | 依赖 | 缺失时的行为 |
|------|------|-------------|
| LaTeX 图形渲染 | MiKTeX/TeX Live + `pdftocairo` 或 Inkscape | 跳过渲染，解析照常输出纯文本 |
| 本地 PaddleOCR | `pip install paddleocr paddlepaddle` | 走云端或视觉通道 |
| 视频多页拍摄 | `pip install opencv-python-headless` | 该端点返回明确错误 |
| PDF 导出 | `pip install reportlab` | 返回缺失提示 |
| Word 导出 | `pip install python-docx` | 返回缺失提示 |

LaTeX 引擎安装（Windows）：

```powershell
winget install MiKTeX.MiKTeX
# PDF 转 PNG 依赖（MiKTeX 自带 pdftocairo，或装 Inkscape）
winget install Inkscape.Inkscape
```

---

## 已知限制

1. **AI 动画执行模型生成的 Python 代码**——已做隔离（临时目录 + 剥离环境变量 + 超时），
   但这不是真沙箱。若要部署为多租户服务，应改为让模型只输出 HTML/JS 数据而非执行 Python。
2. **好未来题库搜题已移除**——原项目因其额度耗尽而全局关闭，且 `requirements.txt` 未包含它的依赖。
   需要时可在 `search_service` 基础上重写。
3. **手写体擦除模块未重写**——原项目 `server/handwriting_removal/` 是一个独立的
   PyTorch 轻量 U-Net，与主流程无耦合，且仓库未包含训练权重。如需要可单独移植。

---

## 安全说明

- AI 服务只应监听 `127.0.0.1`，不要对外暴露。
- `.env` 含密钥，已被 `.gitignore` 排除。
- 静态资源只暴露 5 个白名单目录（图片/报告/导出/批注/动画），路径穿越已阻断。
- 用户数据严格按 `user_id` 隔离；删除操作同样带用户过滤，防止越权删他人记录。
- 请求体解析用 `DisallowUnknownFields`，拼错的字段名会报错而非静默忽略。