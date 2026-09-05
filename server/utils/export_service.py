"""④ 导出服务：服务端生成 PDF / Word（含中文、公式转纯文本）
- PDF 用 reportlab（中文字体 STSong-Light CID）
- Word 用 python-docx
"""
import re
from pathlib import Path
from server.config import REPORT_DIR


def _markdown_to_text(md: str) -> str:
    """把 markdown 转为可用于导出的纯文本：去图片/代码块围栏/加粗标记，公式 $...$ 取中间文字"""
    if not md:
        return ""
    t = md
    # 图片 → [图]
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '[图]', t)
    # 代码块围栏 ``` 去掉（LaTeX 代码块导出时移除，避免一堆 TIKZ 源码）
    t = re.sub(r'```[a-zA-Z]*\n', '', t)
    t = re.sub(r'```', '', t)
    # 标题 # 去掉
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)
    # 加粗/斜体标记去掉
    t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', t)
    # 行内/块级公式：把 $...$ / $$...$$ 的内容提取为纯文本（去掉 LaTeX 反斜杠命令基本保留文字）
    t = re.sub(r'\$\$([^$]+)\$\$', _latex_to_text, t, flags=re.DOTALL)
    t = re.sub(r'(?<!\$)\$([^$\n]+?)(?<!\$)\$', _latex_to_text, t)
    # 颜色标记 [[#RRGGBB]x[[#RRGGBB] → x
    t = re.sub(r'\[\[#[0-9A-Fa-f]{6}\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]', r'\1', t, flags=re.DOTALL)
    # 多余空行压缩（保留换行便于分段）
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _latex_to_text(m: "re.Match") -> str:
    expr = m.group(1)
    # 去掉常见 LaTeX 命令反斜杠，保留文字与符号
    expr = re.sub(r'\\(?:times|cdot|cdot|rightarrow|leftarrow|leq|geq|neq|approx|pm|div|sum|int|frac|sqrt|text|boldsymbol|mathbf|mathrm|quad|qquad|\\|\{|\})', lambda x: {
        '\\times': '×', '\\cdot': '·', '\\rightarrow': '→', '\\leftarrow': '←',
        '\\leq': '≤', '\\geq': '≥', '\\neq': '≠', '\\approx': '≈', '\\pm': '±',
        '\\div': '÷', '\\sum': '∑', '\\int': '∫',
    }.get(x.group(0), ''), expr)
    # 去剩余花括号
    expr = expr.replace('{', '').replace('}', '')
    expr = re.sub(r'\\([a-zA-Z]+)', '', expr)
    expr = expr.replace('^', '^').replace('_', ' ').replace('=', '=').replace('\\', '')
    return expr


def export_pdf(title: str, markdown_text: str, out_path: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        style_title = ParagraphStyle('t', fontName='STSong-Light', fontSize=18, leading=24, spaceAfter=12)
        style_body = ParagraphStyle('b', fontName='STSong-Light', fontSize=11, leading=18, spaceAfter=6)
        doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        flow = [Paragraph(title, style_title)]
        for para in _markdown_to_text(markdown_text).split('\n'):
            p = para.strip()
            if p:
                flow.append(Paragraph(p.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), style_body))
        doc.build(flow)
        return out_path.exists()
    except Exception as e:
        print(f"[Export] PDF生成失败: {e}")
        return False


def export_word(title: str, markdown_text: str, out_path: Path) -> bool:
    try:
        from docx import Document
        doc = Document()
        doc.add_heading(title, level=1)
        for para in _markdown_to_text(markdown_text).split('\n'):
            p = para.strip()
            if p:
                doc.add_paragraph(p)
        doc.save(str(out_path))
        return out_path.exists()
    except Exception as e:
        print(f"[Export] Word生成失败: {e}")
        return False
