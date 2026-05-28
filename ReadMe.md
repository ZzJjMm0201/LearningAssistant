# 学习助手 (Learning Assistant) 2.0

## 📖 项目简介

学习助手是一款基于 **AI + AR + 手势识别** 的智能学习辅助系统。用户只需将题目放在摄像头前，通过简单的手势即可获得 AI 解题、知识延伸、学情报告和交互式动画等服务。

**核心理念**：无感辅助——在学生需要的时候提供恰到好处的帮助，不打断学习心流。

### 主要功能

| 手势 | 功能 | 说明 |
|------|------|------|
| ✋ 5指 | **拍照解题** | OCR识别 → 题库搜索 → AI多轮对话 → LaTeX图形渲染 → 思维导图 |
| 🖖 4指 | **AI动画** | 生成 Plotly 交互式动画，动态展示题目核心概念 |
| 🤟 3指 | **数据报告** | 基于历史数据的可视化统计报告（学科分布、难度、知识点） |
| ✌️ 2指 | **AI报告** | AI生成的温暖鼓励性学情报告 |
| ☝️ 1指 | **知识延伸** | 易错点详解 + 知识拓展 + 科普内容 |

### 系统架构

```
┌─────────────────┐         HTTP/SSE         ┌─────────────────┐
│   客户端 (Android) │ ◄──────────────────────► │   服务端 (Windows) │
│                  │                          │                  │
│  • CameraX 相机   │                          │  • FastAPI 服务   │
│  • MediaPipe 手势 │                          │  • PaddleOCR 识别 │
│  • Compose UI    │                          │  • DeepSeek AI   │
│  • Markwon 渲染  │                          │  • LaTeX 渲染    │
│  • WebView 动画  │                          │  • Plotly 图表   │
└─────────────────┘                          └─────────────────┘
```

---

## 🛠️ 开发过程中遇到的困难及解决方案

### 一、手势识别

| 困难 | 解决方案 |
|------|----------|
| MediaPipe 原生库缺失导致崩溃 | 添加 Gradle 依赖 `com.google.mediapipe:tasks-vision` |
| 模拟器 x86_64 架构不兼容 | 改用 arm64-v8a 模拟器 + API 34 系统镜像 |
| 16KB 页面大小不兼容 | 降级 targetSdk 到 34 |
| 相机权限时序问题 | 使用 `StateFlow` 追踪权限状态，权限就绪后才创建相机预览 |
| YUV→RGB 帧转换 bitmap 为 null | 改用 RGBA_8888 输出格式，跳过 JPEG 压缩 |
| 手指计数不准确（拇指和四指） | 利用"手指朝上"的几何假设，用投影法判断伸直 |
| 手势触发太慢 | 降低检测阈值、缩短保持时间、添加短暂无手容忍 |

### 二、相机与拍照

| 困难 | 解决方案 |
|------|----------|
| `imageCapture` 为空 | 用回调 `onImageCaptureReady` 在 CameraX 绑定后获取引用 |
| CameraX 前置摄像头验证失败 | 直接使用 `DEFAULT_BACK_CAMERA`，不验证前置 |
| 模拟器摄像头调用宿主机 | 配置 AVD 的 Camera 设置为 `Webcam0` |

### 三、网络通信

| 困难 | 解决方案 |
|------|----------|
| 明文 HTTP 被禁止 | 添加 `network_security_config.xml` 允许局域网明文 |
| 模拟器无法访问宿主机 IP | 使用 `10.0.2.2`（模拟器访问宿主机的固定地址） |
| SSE 流接收崩溃（主线程网络） | 所有网络操作包裹在 `Dispatchers.IO` 中 |
| 请求 ID 不匹配 | 统一使用 `session_id` 作为 `request_id` |

### 四、AI 对话流程

| 困难 | 解决方案 |
|------|----------|
| DeepSeek API 余额不足 | 备用混元 API |
| AI 返回内容分类错误 | 改用消息顺序（第1/2/3/4条）分类，不再依赖内容特征 |
| 行内公式渲染失败 | 正则 `(?<!\$)\$(?!\$)` 精准替换单个 `$` 为 `$$` |
| 公式对齐下沉 | 配置 `JLatexMathPlugin` 的基线对齐 |

### 五、LaTeX 图形渲染

| 困难 | 解决方案 |
|------|----------|
| `pdflatex` 不支持中文 | 改用 `xelatex` + `ctex` 包 |
| MiKTeX 管理员/用户不同步 | 用普通用户身份运行 `initexmf --update-fndb` |
| 编码错误 | 指定 `encoding='utf-8', errors='replace'` |
| PDF 转 SVG/PNG | 集成 Inkscape 命令行转换 |
| 图片在模拟器无法加载 | 通过 FastAPI `FileResponse` 提供 HTTP 访问 |

### 六、客户端 UI

| 困难 | 解决方案 |
|------|----------|
| Markwon 版本 API 差异 | 使用 `JLatexMathPlugin.create(fontSize, configure)` |
| 思维导图无换行 | 用正则预处理，在每个树节点前添加换行 |
| WebView 动画过大 | 设置 `setInitialScale(50)` + 注入 CSS 缩放 |
| Compose `viewModel()` 未解析 | 添加 `lifecycle-viewmodel-compose` 依赖 |
| 方法名与父类冲突 | 将 `requestPermissions()` 改为 `requestCameraPermissions()` |

### 七、OCR 识别

| 困难 | 解决方案 |
|------|----------|
| PaddleOCR API 超时 | 降级到本地 PaddleOCR 模型 |
| 识别内容为图片链接 | 正常现象，PaddleOCR-VL 将非文字区域返回为图片描述 |
| 本地模型首次加载慢 | 模型缓存，第二次起秒级加载 |

---

## 📦 技术栈

### 客户端
- **语言**：Kotlin
- **UI**：Jetpack Compose + Material3
- **相机**：CameraX
- **手势识别**：MediaPipe Hands
- **Markdown 渲染**：Markwon + JLatexMathPlugin
- **网络**：OkHttp + SSE
- **图表**：Android WebView

### 服务端
- **语言**：Python
- **框架**：FastAPI + Uvicorn
- **OCR**：PaddleOCR / PaddleOCR-VL API
- **AI**：DeepSeek API / 腾讯混元
- **LaTeX 渲染**：xelatex + Inkscape
- **图表**：Plotly
- **数据库**：SQLite + SQLAlchemy

---

## 🚀 快速开始

### 服务端
```bash
cd server
pip install -r requirements.txt
python main.py
```

### 客户端
1. Android Studio 打开 `client/` 目录
2. 将 `hand_landmarker.task` 放入 `app/src/main/assets/`
3. 修改 `ApiService.kt` 中的 `BASE_URL`（模拟器用 `10.0.2.2`，真机用实际 IP）
4. 运行应用

---

## 📝 待完成

- [ ] 专注度检测模型部署
- [ ] 服务端自动发现（UDP 广播已实现，需联调）
- [ ] 历史记录查询界面
- [ ] 多轮对话优化（目前仅支持一轮追问）

---

## 📄 许可

本项目仅供学习和研究使用。