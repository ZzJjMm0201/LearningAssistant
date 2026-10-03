"""导出：把 Markdown 导出为 Word / PDF。

公式统一降级为可读纯文本（$(1/2)$、√x、≤、→ …）：
Word/PDF 里放 LaTeX 源码对读者没有意义，用户要的是能读懂的答案。
图片先下载到临时目录再嵌入，避免 PDF 渲染时因外链失效而丢图。
"""

from __future__ import annotations

import logging
import re
import tempfile
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger("aiworker.export")


def latex_to_text(expr: str) -> str:
    """把 LaTeX 转为可读纯文本。"""
    expr = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1/\2)", expr)
    expr = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", expr)
    for cmd in ("text", "mathrm", "mathbf", "boldsymbol", "mathit"):
        expr = re.sub(r"\\%s\{([^{}]*)\}" % cmd, r"\1", expr)

    syms = {
        r"\times": "×", r"\cdot": "·", r"\rightarrow": "→", r"\to": "→",
        r"\leftarrow": "←", r"\Rightarrow": "⇒", r"\Leftrightarrow": "⇔",
        r"\leq": "≤", r"\geq": "≥", r"\neq": "≠", r"\ne": "≠",
        r"\approx": "≈", r"\pm": "±", r"\div": "÷", r"\infty": "∞",
        r"\Delta": "Δ", r"\angle": "∠", r"\perp": "⊥", r"\parallel": "∥",
        r"\in": "∈", r"\cup": "∪", r"\cap": "∩", r"\alpha": "α",
        r"\beta": "β", r"\gamma": "γ", r"\theta": "θ", r"\lambda": "λ",
        r"\mu": "μ", r"\sigma": "σ", r"\omega": "ω", r"\pi": "π",
    }
    for k, v in syms.items():
        expr = expr.replace(k, v)

    # 上下标转成括号，避免信息丢失
    expr = re.sub(r"_(\d+|[a-zA-Z])", r"(\1)", expr)
    expr = re.sub(r"\^(\d+|[a-zA-Z])", r"^\1", expr)
    expr = re.sub(r"\\[a-zA-Z]+", "", expr)
    expr = expr.replace("{", "").replace("}", "").replace("\\", "")
    return expr.strip()


def _strip_inline(text: str, keep_color: bool = False) -> str:
    t = re.sub(r"\$\$(.+?)\$\$", lambda m: latex_to_text(m.group(1)), text, flags=re.DOTALL)
    t = re.sub(r"(?<!\$)\$([^$\n]+?)(?<!\$)\$", lambda m: latex_to_text(m.group(1)), t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"~~([^~]+)~~", r"\1", t)
    if not keep_color:
        t = re.sub(r"\[\[#[0-9A-Fa-f]{6}\]\]| \[\[#[0-9A-Fa-f]{6}\]\]", "", t)
    return t


_COLOR_RE = re.compile(r"\[\[#([0-9A-Fa-f]{6})\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]", re.DOTALL)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _iter_blocks(md: str):
    """把 Markdown 拆成结构化块。"""
    for raw in md.split("\n"):
        line = raw.rstrip()
        m = _IMG_RE.fullmatch(line.strip())
        if m:
            yield ("image", m.group(2).strip(), m.group(1).strip())
            continue
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if m and m.group(2).strip():
            yield ("heading", m.group(2).strip(), len(m.group(1)))
            continue
        if line.strip():
            yield ("para", line, None)


def _resolve_url(url: str, host: str) -> str:
    url = (url or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/static/") and host:
        return f"http://{host}{url}" if "://" not in host else f"{host}{url}"
    return ""


def _download_images(md: str, host: str, tmp: Path) -> str:
    """把 markdown 里的图片下载到本地，返回替换后的 markdown。"""
    def repl(m):
        url = _resolve_url(m.group(1), host)
        if not url:
            return m.group(0)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
        except Exception as e:  # noqa: BLE001
            log.warning("下载图片失败 %s: %s", url, e)
            return m.group(0)

        lower = url.lower()
        suffix = ".jpg" if (".jpg" in lower or ".jpeg" in lower) else (
            ".gif" if ".gif" in lower else (".svg" if ".svg" in lower else ".png")
        )
        p = tmp / f"img_{uuid.uuid4().hex[:8]}{suffix}"
        p.write_bytes(data)
        return f"![]({p.as_posix()})"

    return _IMG_RE.sub(repl, md)


def export(title: str, content: str, fmt: str, host: str, output_dir: Path) -> dict:
    """导出为 Word 或 PDF，返回 {path, name}。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = "word" if fmt in ("word", "docx") else "pdf"
    name = f"{_safe(title)}_{uuid.uuid4().hex[:6]}.{'docx' if fmt == 'word' else 'pdf'}"
    out = output_dir / name

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        md = _download_images(content, host, tmp)
        try:
            if fmt == "word":
                ok = _export_word(title, md, out)
            else:
                ok = _export_pdf(title, md, out)
        except Exception as e:  # noqa: BLE001
            log.exception("导出失败")
            return {"path": "", "name": "", "message": str(e)}
        if not ok:
            return {"path": "", "name": "", "message": "导出依赖缺失（Word 需 python-docx，PDF 需 reportlab）"}

    return {"path": str(out), "name": name}


def _safe(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", s or "export")
    return s.strip()[:40] or "export"


def _export_pdf(title: str, md: str, out: Path) -> bool:
    """用 reportlab 直接生成 PDF。

    不用 pandoc：pandoc 走 PDF 需要 LaTeX 环境，而本服务的 LaTeX 只用于画图，
    让导出再依赖它会让"能看图但导不出 PDF"变成常态。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    st_h1 = ParagraphStyle("h1", fontName="STSong-Light", fontSize=17, leading=22, spaceAfter=8)
    st_h2 = ParagraphStyle("h2", fontName="STSong-Light", fontSize=14, leading=19, spaceBefore=6, spaceAfter=5)
    st_body = ParagraphStyle("b", fontName="STSong-Light", fontSize=10.5, leading=15, spaceAfter=4)

    doc = SimpleDocTemplate(
        str(out), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )
    flow: list = [Paragraph(_esc(title), st_h1)]
    for kind, a, b in _iter_blocks(md):
        if kind == "heading":
            flow.append(Paragraph(_esc(a), st_h1 if (b or 1) <= 1 else st_h2))
        elif kind == "image":
            p = Path(a)
            if not p.exists():
                continue
            try:
                from reportlab.lib.utils import ImageReader

                iw, ih = ImageReader(str(p)).getSize()
                maxw = 160 * mm
                flow.append(Image(str(p), width=min(iw, maxw),
                                  height=ih * (min(iw, maxw) / iw)))
            except Exception as e:  # noqa: BLE001
                log.warning("PDF 嵌图失败: %s", e)
        else:
            txt = _strip_inline(a)
            if txt.strip():
                flow.append(Paragraph(_esc(txt), st_body))
    doc.build(flow)
    return out.exists() and out.stat().st_size > 0


def _export_word(title: str, md: str, out: Path) -> bool:
    """用 python-docx 生成 Word，并保留批注文字的颜色。"""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading(title, level=1)

    for kind, a, b in _iter_blocks(md):
        if kind == "heading":
            doc.add_heading(_strip_inline(a), level=min(b or 1, 4))
        elif kind == "image":
            p = Path(a)
            if p.exists():
                try:
                    doc.add_picture(str(p))
                except Exception as e:  # noqa: BLE001
                    log.warning("Word 嵌图失败: %s", e)
        else:
            txt = _strip_inline(a, keep_color=True)
            if not txt.strip():
                continue
            para = doc.add_paragraph()
            last = 0
            for m in _COLOR_RE.finditer(txt):
                if m.start() > last:
                    para.add_run(txt[last : m.start()])
                run = para.add_run(m.group(2))
                run.font.color.rgb = RGBColor(
                    int(m.group(1)[0:2], 16), int(m.group(1)[2:4], 16), int(m.group(1)[4:6], 16)
                )
                last = m.end()
            if last < len(txt):
                para.add_run(txt[last:])
            para.paragraph_format.line_spacing = 1.15
            para.paragraph_format.space_after = Pt(4)

    doc.save(str(out))
    return out.exists() and out.stat().st_size > 0


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")