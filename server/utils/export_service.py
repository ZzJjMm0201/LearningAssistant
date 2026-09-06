"""④ 导出服务：服务端生成 PDF / Word（含中文、公式转可读文本、颜色标注）
- PDF 用 reportlab（中文字体 STSong-Light CID）
- Word 用 python-docx
"""
import re
from pathlib import Path
from server.config import REPORT_DIR


def _latex_to_text(expr: str) -> str:
    """把 LaTeX 表达式转为可读纯文本（保留符号，去掉命令）"""
    # \frac{a}{b} → (a/b)
    expr = re.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1/\2)', expr)
    expr = re.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', expr)
    expr = re.sub(r'\\text\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\mathrm\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\mathbf\{([^{}]*)\}', r'\1', expr)
    expr = re.sub(r'\\boldsymbol\{([^{}]*)\}', r'\1', expr)
    # 符号命令 → Unicode
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
    # 去掉剩余 \ 命令
    expr = re.sub(r'\\([a-zA-Z]+)', '', expr)
    # 去花括号
    expr = expr.replace('{', '').replace('}', '')
    # 下标 _ 后接字符 → 变下标式纯文本（保留）
    expr = re.sub(r'_(\d+|\w)', r'(\1)', expr)
    expr = re.sub(r'\^(\d+|\w)', r'^\1', expr)
    expr = expr.replace('\\', '')
    return expr.strip()


def _convert_formula(m: "re.Match") -> str:
    return _latex_to_text(m.group(1))


def _markdown_to_plain(md: str, strip_color: bool = True) -> str:
    """markdown → 纯文本（图片占位、公式转可读；strip_color=True 时移除颜色标记，否则保留供Word上色）"""
    if not md:
        return ""
    t = md
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '[图]', t)
    t = re.sub(r'```[a-zA-Z]*\n', '', t)
    t = re.sub(r'```', '', t)
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', t)
    t = re.sub(r'\$\$([^$]+)\$\$', _convert_formula, t, flags=re.DOTALL)
    t = re.sub(r'(?<!\$)\$([^$\n]+?)(?<!\$)\$', _convert_formula, t)
    if strip_color:
        t = re.sub(r'\[\[#[0-9A-Fa-f]{6}\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]', r'\1', t, flags=re.DOTALL)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _escape_xml(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def export_pdf(title: str, markdown_text: str, out_path: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        style_title = ParagraphStyle('t', fontName='STSong-Light', fontSize=18, leading=22, spaceAfter=10)
        style_body = ParagraphStyle('b', fontName='STSong-Light', fontSize=11, leading=15, spaceAfter=4)  # ⑤ 行距调小
        doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm)
        flow = [Paragraph(_escape_xml(title), style_title)]
        for para in _markdown_to_plain(markdown_text, strip_color=True).split('\n'):
            p = para.strip()
            if p:
                flow.append(Paragraph(_escape_xml(p), style_body))
        doc.build(flow)
        return out_path.exists()
    except Exception as e:
        print(f"[Export] PDF生成失败: {e}")
        return False


def export_word(title: str, markdown_text: str, out_path: Path) -> bool:
    try:
        from docx import Document
        from docx.shared import Pt
        doc = Document()
        doc.add_heading(title, level=1)
        for para in _markdown_to_plain(markdown_text, strip_color=False).split('\n'):
            p = para.strip()
            if not p:
                continue
            # ⑤ 解析颜色标记 [[#RRGGBB]文字[[#RRGGBB] 分段设置字体颜色
            runs = []
            last = 0
            for m in re.finditer(r'\[\[#([0-9A-Fa-f]{6})\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]', p, re.DOTALL):
                if m.start() > last:
                    runs.append((p[last:m.start()], None))
                runs.append((m.group(2), m.group(1)))
                last = m.end()
            if last < len(p):
                runs.append((p[last:], None))
            if len(runs) == 1 and runs[0][1] is None:
                paragraph = doc.add_paragraph(p)
            else:
                paragraph = doc.add_paragraph()
                for text, color in runs:
                    run = paragraph.add_run(text)
                    if color:
                        run.font.color.rgb = __import__('docx').shared.RGBColor(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
            # ⑤ 行距调小（单倍行距）
            paragraph.paragraph_format.line_spacing = 1.0
            paragraph.paragraph_format.space_after = Pt(4)
        doc.save(str(out_path))
        return out_path.exists()
    except Exception as e:
        print(f"[Export] Word生成失败: {e}")
        return False
