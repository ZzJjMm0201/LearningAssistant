# 学习助手 · Learning Assistant

> 基于 **AI + AR + 手势识别** 的个性化学习辅助系统
> 把题目放到摄像头前，动动手指，就能拿到 **AI 解题 / 知识延伸 / AI 动画 / AI 批注 / 学情报告**。

**核心理念：无感辅助** —— 在学生需要的时候，提供恰到好处的帮助，不打断学习心流。

---

## ✨ 功能一览

| 手势 | 功能 | 说明 |
|------|------|------|
| ✋ 5 指 | **拍照解题** | OCR 识别 → 题库检索 → AI 多轮推理 → LaTeX 图形渲染 → 思维导图 → 预判问题 |
| ✌️ 4 指 | **AI 动画** | 生成交互式动画，动态演示题目核心概念 |
| 🤟 3 指 | **AI 批注** | 像老师一样在作业图上圈错、划线、写批注，可下载带批注的图片 |
| ✌️ 2 指 | **AI 报告** | 基于历史数据生成温暖鼓励式的学情报告 |
| ☝️ 1 指 | **知识延伸** | 易错点详解 + 知识拓展 + 相似题推荐 + 延伸追问 |

**其他能力**

- **多题分题**：一张照片里有多道题时，自动切分并让用户勾选要解答的题目，多题并发解答
- **学情报告（数据版）**：学科分布、难度分布、知识点覆盖、正确率趋势等可视化图表
- **历史记录**：全部作答记录可检索、筛选、导出（Word / PDF）
- **多端支持**：Windows 服务端 + 网页端 + 安卓客户端（Kotlin / Compose）
- **番茄钟**：内置专注计时，并按本轮题目难度给出专注时长建议

---

## 🏗️ 系统架构

```
┌──────────────────────┐        HTTP / SSE        ┌──────────────────────┐
│   客户端（Android）    │ ◄──────────────────────► │   服务端（Windows）    │
│                      │                          │                      │
│  · CameraX 相机       │                          │  · FastAPI 服务       │
│  · MediaPipe 手势     │                          │  · PaddleOCR 识别     │
│  · Compose UI        │                          │  · 大模型（DeepSeek/Qwen）│
│  · Markwon 渲染       │                          │  · LaTeX / TikZ 渲染  │
│  · WebView 动画       │                          │  · Plotly 图表        │
└──────────────────────┘                          └──────────────────────┘
            ▲                                                  ▲
            │                                                  │
            └──────────── 网页端（原生 HTML / JS） ──────────────┘
```

### 模块划分

| 目录 | 说明 |
|------|------|
| `main.py` | 服务端入口（FastAPI），所有 HTTP / SSE 端点 |
| `server/` | 服务端业务代码 |
| `server/services/` | AI 服务、解题流水线、批注、报告生成、权限等核心服务 |
| `server/utils/` | LaTeX 处理、分题切分、渲染等工具 |
| `server/database/` | ORM 模型与会话 |
| `web/` | 网页端（原生 HTML / CSS / JS，无需构建） |
| `client/` | 安卓客户端（Kotlin + Jetpack Compose） |
| `data/` | 运行期数据库（**不纳入版本控制**） |
| `history/` | 运行期作答记录与图片（**不纳入版本控制**） |

---

## 🚀 快速开始（服务端）

```bash
# 1. 创建虚拟环境
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux / macOS

# 2. 安装依赖
pip install -r server/requirements.txt

# 3. 配置密钥
copy .env.example .env           # 然后填入你自己的 API Key
#   也可直接在 server/config.py 中配置

# 4. 启动服务
python main.py
```

启动后：

- 网页端：<http://127.0.0.1:8000/web/>
- 健康检查：<http://127.0.0.1:8000/health>

### 安卓客户端

用 Android Studio 打开 `client/` 目录，把 `BASE_URL` 指向你的服务端地址（设置页里也可随时修改），然后构建安装即可。

---

## ⚙️ 配置说明

配置分两层，**优先读环境变量 / `.env`**，未配置则回退到 `server/config.py` 的默认值：

| 配置项 | 说明 |
|--------|------|
| `QWEN_API_KEY` | 通义千问（阿里云百炼 OpenAI 兼容端点） |
| `DOUBAO_API_KEY` / `HUNYUAN_API_KEY` | 豆包 / 混元等备用通道 |
| `VISION_PROVIDER` | 视觉识别通道：`qwen` 或 `deepseek` |
| `JWT_SECRET` | 登录令牌签名密钥 |

> ⚠️ **请勿提交 `.env`**。仓库已通过 `.gitignore` 忽略它，只保留 `.env.example` 作为模板。

### AI 使用权限

AI 类接口按账号控制使用权限，规则写在项目根目录 `permissions.txt`：

```
# 用户名=True 表示放行，未列出或 =False 表示受限
student_name=True
```

- 管理员账号（`is_admin=true`）始终可用
- 文件不存在时视为不限制
- 修改后**无需重启**，按文件修改时间即时生效

---

## 🔌 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/solve/image` · `/solve/text` | 提交题目（图片 / 文字） |
| `GET`  | `/solve/stream/{id}` | SSE 流式获取解题过程 |
| `POST` | `/solve/select_questions/{id}` | 多题分题后回传要解答的题号 |
| `POST` | `/ask` | 追问（多轮对话） |
| `POST` | `/extend` · `/animate` · `/annotate` | 知识延伸 / 动画 / 批注 |
| `POST` | `/report/data` · `/report/ai` | 数据版 / AI 版学情报告 |
| `POST` | `/history` | 查询历史记录（按日期、学科、类型筛选） |
| `GET`  | `/health` | 健康检查 |

---

## 🧗 开发过程中遇到的困难与解法（节选）

| 困难 | 解法 |
|------|------|
| 手写体与印刷体混杂，OCR 结果难区分 | 提示词约束：手写内容统一转 *斜体*，并按实际颜色加 `[[#RRGGBB]]` 标记 |
| 一张图里多道题，需要精确切分 | 视觉通道让模型在独立大题间输出 `%%%` 分隔符；无分隔符时用 LLM 锚点切分 |
| 推理型模型把 token 全用在思考上，正文为空 | 给长输出步骤显式提高 `max_tokens`（思考与正文共用额度） |
| 数学公式在网页与安卓端渲染不一致 | 服务端统一预处理 LaTeX，网页用 MathJax、安卓用 JLatexMath，并对不支持的语法降级 |
| 解题结果按「第几条消息」分类会串题 | 改为按「该条回复前面的用户提问」判定类型，并以内容特征兜底 |

---

## 📄 文档

- `ARCHITECTURE.md` —— 更详细的架构与模块说明
- `HowToUseVENV.md` —— 虚拟环境使用说明
- `README.md` —— 本文档

---

## 📜 许可

本项目用于学习与研究。使用第三方 API（通义千问 / DeepSeek 等）时，请遵守对应平台的服务条款。
