"""
AI 服务：承载需要 Python 生态的重活。

Go 网关通过 HTTP 调用本服务：

    POST /ocr              OCR 识别（视觉模型 或 Paddle 双通道）
    POST /ocr/boxes        OCR + 文字块坐标（供批注定位）
    POST /split            多题切分
    POST /solve/stream     流式解题（SSE）
    POST /ask              非流式追问
    POST /ask/stream       流式追问（SSE）
    POST /extend/stream    知识延伸（SSE）
    POST /annotate         生成批注图
    POST /animation        生成 Plotly 动画
    POST /keyframes        视频抽关键帧
    POST /latex/render     LaTeX 渲染闭环
    POST /report/mistakes  易错点梳理
    POST /report/ai        AI 版学情报告
    POST /pomodoro/recommend 专注时长推荐
    POST /export           导出 Word / PDF
    GET  /health           健康检查

本服务只监听 127.0.0.1：不对外网暴露，避免"AI 生成的代码被执行"这类高危接口被直接触达。
"""

import os
import sys

# Windows GBK 控制台打印 ⁻₂ 等 Unicode 会崩线程 → 强制 UTF-8 + 替换
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import logging
import logging.handlers
import threading
from pathlib import Path

# PaddlePaddle 兼容性修复：必须在任何 paddle 导入前设置
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

BASE_DIR = Path(__file__).resolve().parent.parent


def setup_logging() -> logging.Logger:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "aiworker.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [fh, sh]
    return logging.getLogger("aiworker")


log = setup_logging()


def main() -> None:
    parser = argparse.ArgumentParser(description="学习助手 AI 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--dir", default=str(BASE_DIR), help="项目根目录")
    parser.add_argument("--preload-ocr", action="store_true", help="启动时预加载本地 PaddleOCR 模型")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    sys.path.insert(0, str(root))

    try:
        import uvicorn
    except ImportError:
        log.error("缺少依赖：pip install -r requirements.txt")
        sys.exit(1)

    # 本地 PaddleOCR 内存占用大，仅在明确要求时预加载
    if args.preload_ocr:
        def _preload():
            try:
                from aiworker.ocr import preload_local_model
                preload_local_model()
            except Exception as e:
                log.warning("OCR 预加载失败（不影响启动）: %s", e)

        threading.Thread(target=_preload, daemon=True).start()
        log.info("OCR 模型预加载已触发（后台）")
    else:
        log.info("未要求预加载 OCR 模型（启动更快，首次识别会稍慢）")

    log.info("AI 服务启动: %s:%d", args.host, args.port)
    uvicorn.run(
        "aiworker.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,  # 访问日志由网关统一记录
    )


if __name__ == "__main__":
    main()