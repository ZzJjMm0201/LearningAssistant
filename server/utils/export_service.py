"""④ 导出服务：服务端生成 PDF / Word
- 优先使用 pandoc（markdown→docx/pdf，保留标题/表格/代码块/图片结构）
- 无 pandoc 时回退 python-docx(Word) / reportlab(PDF)
- LaTeX 渲染出的图片直接嵌入（不再输出 [图] 占位）；公式源码转可读纯文本
"""
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from server.config import REPORT_DIR


def _latex_to_text(expr: str) -> str:
    """把 LaTeX 表达式转为可读纯文本（保留符号，去掉命令）"""
    expr = re.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1/\2)', expr)
    expr = re.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', expr)
    expr = re.sub(r'\\text\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\mathrm\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\mathbf\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\boldsymbol\{([^{}]*)\}', r'\1', expr)
    syms = {
        r'\times': '×', r'\cdot': '·', r'\rightarrow': '→', r'\leftarrow': '←',
        r'\Rightarrow': '⇒', r'\Leftrightarrow': '⇔', r'\leq': '≤', r'\geq': '≥',
        r'\neq': '≠', r'\ne': '≠', r'\approx': '≈', r'\pm': '±', r'\div': '÷',
        r'\sum': '∑', r'\int': '∫', r'\pi': 'π', r'\theta': 'θ', r'\alpha': 'α',
        r'\beta': 'β', r'\gamma': 'γ', r'\infty': '∞', r'\Delta': 'Δ', r'\angle': '∠',
        r'\circ': '°', r'\degree': '°', r'\perp': '⊥', r'\parallel': '∥', r'\in': '∈',
    }
    for k, v in syms.items():
        expr = expr.replace(k, v)
    expr = re.sub(r'\\([a-zA-Z]+)', '', expr)
    expr = expr.replace('{', '').replace('}', '')
    expr = re.sub(r'_(\d+|\w)', r'(\1)', expr)
    expr = re.sub(r'\^(\d+|\w)', r'^\1', expr)
    expr = expr.replace('\\', '')
    return expr.strip()


def _convert_formula(m: "re.Match") -> str:
    return _latex_to_text(m.group(1))


_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)')


def _resolve_image_url(url: str, host: str) -> str:
    """把图片 URL 归一为可下载的完整 http(s) URL；无法解析返回空"""
    url = (url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/static/"):
        h = host or ""
        if "://" in h:
            scheme, h = h.split("://", 1)
        else:
            scheme = "http"
        return f"{scheme}://{h}{url}"
    return ""


def _download_images(md: str, host: str, tmp: Path) -> str:
    """下载 markdown 内图片到本地目录，返回图片路径替换后的 markdown"""
    def repl(m: "re.Match"):
        url = _resolve_image_url(m.group(1), host)
        if not url:
            return m.group(0)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
        except Exception:
            return m.group(0)
        lower = url.lower()
        suffix = ".png"
        if ".jpg" in lower or ".jpeg" in lower:
            suffix = ".jpg"
        elif ".gif" in lower:
            suffix = ".gif"
        elif ".svg" in lower:
            suffix = ".svg"
        import uuid
        lp = tmp / f"img_{uuid.uuid4().hex[:8]}{suffix}"
        lp.write_bytes(data)
        return f"![]({lp.as_posix()})"
    return _IMAGE_RE.sub(repl, md)


def _pandoc_exe() -> str:
    """定位 pandoc 可执行文件（winget 装到 %LOCALAPPDATA%\Pandoc，PATH 可能未刷新）"""
    w = shutil.which("pandoc")
    if w:
        return w
    for cand in [
        os.path.expandvars(r"%LOCALAPPDATA%\Pandoc\pandoc.exe"),
        os.path.expandvars(r"%ProgramFiles%\Pandoc\pandoc.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Pandoc\pandoc.exe"),
    ]:
        if Path(cand).exists():
            return cand
    return ""


def _pandoc_available() -> bool:
    return bool(_pandoc_exe())


def _iter_blocks(md: str):
    """把 markdown 拆成结构化块：('heading', text, level) / ('image', path, alt) / ('para', text)"""
    for raw in md.split("\n"):
        line = raw.rstrip()
        m = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$', line)
        if m:
            yield ("image", m.group(2).strip(), m.group(1).strip())
            continue
        m = re.match(r'^(#{1,6})\s*(.*)$', line)
        if m and m.group(2).strip():
            yield ("heading", m.group(2).strip(), len(m.group(1)))
            continue
        if line.strip():
            yield ("para", line, None)


def _strip_inline(text: str, strip_color: bool = False) -> str:
    """段落内联：去代码围栏/加粗/公式→文本；可选保留颜色标记"""
    t = text
    t = re.sub(r'\$\$([^$]+)\$\$', _convert_formula, t, flags=re.DOTALL)
    t = re.sub(r'(?<!\$)\$([^$\n]+?)(?<!\$)\$', _convert_formula, t)
    t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', t)
    t = re.sub(r'`([^`]+)`', r'\1', t)
    if strip_color:
        t = re.sub(r'\[\[#[0-9A-Fa-f]{6}\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]', r'\1', t, flags=re.DOTALL)
    return t


def _escape_xml(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _add_colored_runs(doc_para, text: str):
    """python-docx：按颜色标记分段设置文字颜色"""
    from docx.shared import RGBColor
    runs = []
    last = 0
    for m in re.finditer(r'\[\[#([0-9A-Fa-f]{6})\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]', text, re.DOTALL):
        if m.start() > last:
            runs.append((text[last:m.start()], None))
        runs.append((m.group(2), m.group(1)))
        last = m.end()
    if last < len(text):
        runs.append((text[last:], None))
    if not runs:
        runs = [(text, None)]
    for seg, color in runs:
        run = doc_para.add_run(seg)
        if color:
            run.font.color.rgb = RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _export_via_pandoc(md: str, out_path: Path, fmt: str) -> bool:
    """用 pandoc 转换；fmt: docx/pdf"""
    import subprocess
    pandoc = _pandoc_exe()
    if not pandoc:
        return False
    tmp = Path(tempfile.mkdtemp(prefix="export_md_"))
    md_file = tmp / "content.md"
    md_file.write_text(md, encoding="utf-8")
    try:
        if fmt == "docx":
            r = subprocess.run([pandoc, str(md_file), "-o", str(out_path)],
                               capture_output=True, timeout=120)
        else:
            # PDF 需引擎；优先 xelatex（中文），否则 pdflatex/wkhtmltopdf
            engines = ["xelatex", "pdflatex", "wkhtmltopdf", "weasyprint"]
            last_err = ""
            for eng in engines:
                if eng in ("xelatex", "pdflatex") and shutil.which(eng) is None:
                    continue
                if eng in ("wkhtmltopdf", "weasyprint") and shutil.which(eng) is None:
                    continue
                r = subprocess.run([pandoc, str(md_file), "-o", str(out_path), f"--pdf-engine={eng}"],
                                   capture_output=True, timeout=180)
                if r.returncode == 0 and out_path.exists():
                    return True
                last_err = (r.stderr or b"").decode("utf-8", "ignore")[-300:]
            # 无可用引擎：让 pandoc 自己选默认（可能报错）
            r = subprocess.run([pandoc, str(md_file), "-o", str(out_path)],
                               capture_output=True, timeout=180)
            if r.returncode != 0:
                print(f"[Export] pandoc pdf 失败: {last_err}")
        return out_path.exists()
    except Exception as e:
        print(f"[Export] pandoc 转换失败: {e}")
        return False


def export_pdf(title: str, markdown_text: str, out_path: Path, host: str = "") -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="export_img_"))
    try:
        md = _download_images(markdown_text, host, tmp)
        # 标题单独加在最前（heading 1）
        md = f"# {title}\n\n" + md
        # PDF 直接用 reportlab（pandoc 需 LaTeX/MiKTeX，缺包会卡住/失败）
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Image
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        st_h1 = ParagraphStyle('h1', fontName='STSong-Light', fontSize=18, leading=22, spaceAfter=8)
        st_h2 = ParagraphStyle('h2', fontName='STSong-Light', fontSize=15, leading=19, spaceAfter=6)
        st_body = ParagraphStyle('b', fontName='STSong-Light', fontSize=11, leading=15, spaceAfter=4)
        doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                                leftMargin=20 * mm, rightMargin=20 * mm,
                                topMargin=18 * mm, bottomMargin=18 * mm)
        flow = [Paragraph(_escape_xml(title), st_h1)]
        for kind, a, b in _iter_blocks(md):
            if kind == "heading":
                flow.append(Paragraph(_escape_xml(a), st_h1 if b <= 1 else st_h2))
            elif kind == "image":
                p = Path(a)
                if p.exists():
                    from reportlab.lib.utils import ImageReader
                    try:
                        img = Image(str(p))
                        iw, ih = ImageReader(str(p)).getSize()
                        maxw = 160 * mm
                        if iw > maxw:
                            scale = maxw / iw
                            img = Image(str(p), width=maxw, height=ih * scale)
                        flow.append(img)
                    except Exception:
                        pass
            else:
                txt = _strip_inline(a, strip_color=True)
                if txt.strip():
                    flow.append(Paragraph(_escape_xml(txt), st_body))
        doc.build(flow)
        return out_path.exists()
    except Exception as e:
        print(f"[Export] PDF生成失败: {e}")
        return False


def export_word(title: str, markdown_text: str, out_path: Path, host: str = "") -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="export_img_"))
    try:
        md = _download_images(markdown_text, host, tmp)
        md = f"# {title}\n\n" + md
        # ④ 颜色标记 [[#RRGGBB]文字[[#RRGGBB] → HTML span（pandoc 转 Word 保留为彩色文字，不再原样输出标记）
        md = re.sub(
            r'\[\[#([0-9A-Fa-f]{6})\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]',
            r'<span style="color:#\1">\2</span>', md, flags=re.DOTALL
        )
        if _pandoc_available():
            if _export_via_pandoc(md, out_path, "docx"):
                return True
            print("[Export] pandoc DOCX 失败，回退 python-docx")
        # 回退 python-docx
        from docx import Document
        from docx.shared import Pt
        doc = Document()
        doc.add_heading(title, level=1)
        for kind, a, b in _iter_blocks(md):
            if kind == "heading":
                doc.add_heading(a, level=min(b, 4))
            elif kind == "image":
                p = Path(a)
                if p.exists():
                    try:
                        doc.add_picture(str(p))
                    except Exception:
                        pass
            else:
                txt = _strip_inline(a, strip_color=False)
                if not txt.strip():
                    continue
                paragraph = doc.add_paragraph()
                _add_colored_runs(paragraph, txt)
                paragraph.paragraph_format.line_spacing = 1.0
                paragraph.paragraph_format.space_after = Pt(4)
        doc.save(str(out_path))
        return out_path.exists()
    except Exception as e:
        print(f"[Export] Word生成失败: {e}")
        return False
