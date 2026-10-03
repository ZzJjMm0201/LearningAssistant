"""LaTeX/TikZ 渲染闭环。

流程：提取 ```latex 代码块 → 编译 → 转 PNG → 回填 Markdown URL。
编译失败时把错误信息交给 LLM 修代码，最多重试 N 次；仍失败则移除该块
（宁可少一张图，也不要把一堆编译不通过的源码甩给用户看）。

外部依赖：xelatex（MiKTeX/TeX Live）+ pdftocairo 或 Inkscape。
缺失时所有图解辅助静默降级为纯文本，不影响解题主流程。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("aiworker.latex")

# 匹配 ```latex 代码块
_BLOCK_RE = re.compile(r"```latex\s*\n(.*?)\n```", re.DOTALL)

_PREAMBLE = r"""
\documentclass[tikz,border=2pt]{standalone}
\usepackage{tikz}
\usepackage{pgfplots}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage[version=4]{mhchem}
\usepackage{ctex}
\pgfplotsset{compat=1.18}
\begin{document}
%s
\end{document}
"""

# MiKTeX 在 Windows 上把引擎命名成 miktex-xxx
_MIKTEX_ALIASES = {
    "pdflatex": "miktex-pdftex",
    "xelatex": "miktex-xetex",
    "lualatex": "miktex-luatex",
}

_MIKTEX_DIRS = [
    r"C:\Program Files\MiKTeX\miktex\bin\x64",
    r"C:\Program Files (x86)\MiKTeX\miktex\bin",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "MiKTeX", "miktex", "bin", "x64"),
]

_setup_done = False


def setup_miktex() -> None:
    """把 MiKTeX 的 bin 目录加进 PATH。幂等。"""
    global _setup_done
    if _setup_done:
        return
    _setup_done = True
    for d in _MIKTEX_DIRS:
        if os.path.isdir(d):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            log.info("MiKTeX 已加入 PATH: %s", d)
            break


def available_engines() -> list[str]:
    """探测可用的 LaTeX 引擎。"""
    setup_miktex()
    out = []
    for eng in ("xelatex", "pdflatex", "lualatex"):
        if shutil.which(eng) or shutil.which(_MIKTEX_ALIASES.get(eng, "")):
            out.append(eng)
    return out


def _resolve_engine(preferred: str = "xelatex") -> str | None:
    setup_miktex()
    for eng in ([preferred] if preferred else []) + ["xelatex", "pdflatex"]:
        if exe := shutil.which(eng):
            return exe
        if alias := _MIKTEX_ALIASES.get(eng):
            if exe := shutil.which(alias):
                return exe
    return None


def _is_real_png(path: Path) -> bool:
    """按魔数判断是不是真 PNG。Inkscape 在部分环境下会写出空文件或伪装扩展名。"""
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _pdf_to_png(pdf: Path, png: Path) -> bool:
    """PDF → PNG。pdftocairo 优先（MiKTeX 自带），退回 Inkscape。"""
    if shutil.which("pdftocairo"):
        try:
            r = subprocess.run(
                ["pdftocairo", "-png", "-singlefile", "-r", "250", str(pdf), str(png.with_suffix(""))],
                capture_output=True, timeout=40,
            )
            if r.returncode == 0 and _is_real_png(png):
                return True
        except Exception as e:  # noqa: BLE001
            log.warning("pdftocairo 转 PNG 失败: %s", e)

    inkscape = shutil.which("inkscape")
    if inkscape:
        for args in (
            [str(pdf), "--export-type=png", f"--export-filename={png}", "--export-dpi=150"],
            [str(pdf), f"--export-png={png}"],
        ):
            try:
                r = subprocess.run([inkscape, *args], capture_output=True, timeout=60)
                if r.returncode == 0 and _is_real_png(png):
                    return True
            except Exception as e:  # noqa: BLE001
                log.warning("Inkscape 转 PNG 失败: %s", e)
    return False


def _limit_width(png: Path, max_width: int = 1000) -> None:
    """限制 PNG 宽度，避免超出手机屏幕导致整页排版崩掉。"""
    try:
        from PIL import Image

        img = Image.open(png)
        if img.width > max_width:
            h = int(img.height * max_width / img.width)
            img.resize((max_width, h), Image.LANCZOS).save(png)
    except Exception as e:  # noqa: BLE001
        log.debug("缩放失败（忽略）: %s", e)


def _compile(code: str, out_dir: Path, engine_name: str, error_out: list[str] | None = None) -> bool:
    """编译一段 LaTeX，产出 PNG。"""
    exe = _resolve_engine(engine_name)
    if not exe:
        log.warning("未找到 LaTeX 引擎，图形渲染不可用")
        return False

    full = code if ("\\documentclass" in code or "\\begin{document}" in code) else _PREAMBLE % code

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tex = td / "fig.tex"
        tex.write_text(full, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Windows 上 pdflatex/xelatex 是 .bat 包装器，必须 shell=True
        use_shell = sys.platform == "win32"

        for attempt in range(2):  # 首次 + 一次重试（引用/字体问题常需第二遍）
            try:
                r = subprocess.run(
                    [exe, "-interaction=nonstopmode", "-halt-on-error", "fig.tex"],
                    cwd=str(td), capture_output=True, timeout=40,
                    encoding="utf-8", errors="replace", env=env, shell=use_shell,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("LaTeX 执行异常: %s", e)
                return False

            if r.returncode == 0:
                pdf = td / "fig.pdf"
                if pdf.exists():
                    png = td / "fig.png"
                    if _pdf_to_png(pdf, png):
                        _limit_width(png)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        import shutil as sh

                        sh.copy(png, out_dir / "out.png")
                        return True
                log.warning("PDF 已生成但转 PNG 失败")
                return False

            if error_out is not None:
                errs = [ln for ln in (r.stdout or "").split("\n") if ln.startswith("!")]
                error_out.append("\n".join(errs[:5]) or (r.stdout or "")[-400:] or "编译失败")
            if attempt == 0:
                continue
    return False


def _extract_blocks(md: str) -> list[tuple[str, str, int, int]]:
    """提取所有 latex 代码块：[(block_id, code, start, end)]。"""
    out = []
    for m in _BLOCK_RE.finditer(md):
        code = m.group(1).strip()
        if code:
            out.append((hashlib.md5(code.encode()).hexdigest()[:8], code, m.start(), m.end()))
    return out


def _make_dark_png(png_path: Path) -> None:
    """生成反色版供深色模式使用。"""
    try:
        from PIL import Image, ImageChops

        ImageChops.invert(Image.open(png_path).convert("RGB")).save(
            png_path.with_name(png_path.stem + "_dark.png")
        )
    except Exception as e:  # noqa: BLE001
        log.debug("生成深色版失败（忽略）: %s", e)


def render_markdown(md: str, out_dir: Path, base_url: str, engine_name: str = "xelatex",
                    fix_fn=None, max_retries: int = 2) -> str:
    """把 Markdown 里的 latex 块渲染成图片并回填 URL。

    fix_fn(code, error) -> 新代码；提供它就启用"编译失败让 AI 修"的重试闭环。
    """
    blocks = _extract_blocks(md)
    if not blocks:
        return md

    out_dir = Path(out_dir)
    result = md

    # 从后往前替换，避免前面替换导致后面下标偏移
    for block_id, code, start, end in reversed(blocks):
        cur_code, cur_id, success = code, block_id, False
        err_text = "编译失败"

        for attempt in range(max_retries + 1):
            png_name = f"diagram_{cur_id}.png"
            if _compile(cur_code, out_dir / cur_id, engine_name):
                png_path = out_dir / cur_id / "out.png"
                target = out_dir / png_name
                if png_path.exists():
                    import shutil as sh

                    sh.copy(png_path, target)
                    png_path.unlink(missing_ok=True)
                    _make_dark_png(target)
                    url = f"{base_url.rstrip('/')}/{png_name}"
                    result = result[:start] + f"\n\n![图解]({url})\n\n" + result[end:]
                else:
                    result = result[:start] + "\n\n> ⚠️ 图形生成失败\n\n" + result[end:]
                success = True
                break

            if fix_fn is None or attempt >= max_retries:
                break
            try:
                from ..ai import engine as engine_mod

                fixed = fix_fn(cur_code, err_text)
            except Exception as e:  # noqa: BLE001
                log.warning("AI 修 LaTeX 失败: %s", e)
                break
            if not fixed or not fixed.strip() or fixed == cur_code:
                break
            cur_code = fixed
            cur_id = hashlib.md5(cur_code.encode()).hexdigest()[:8]

        if not success:
            # 移除该块：把编译不通过的源码留给用户没有意义
            result = result[:start] + "\n\n> ⚠️ 图形渲染失败\n\n" + result[end:]

    return result