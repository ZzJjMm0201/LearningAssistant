"""
简化的TikZ/LaTeX渲染工具
- 接收LaTeX代码
- 编译为PDF
- 转换为SVG
"""
import os
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

def is_real_png(path: Path) -> bool:
    """检查文件是否为真正的PNG（通过魔数判断）"""
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except Exception:
        return False


def render_latex_blocks(latex_code: str, output_path: Path, engine: str = "xelatex", error_out: list = None) -> bool:
    """
    将LaTeX/TikZ代码渲染为图片。

    output_path 为预期的PNG路径；渲染成功后：
    - 优先输出真正的PNG（output_path）
    - 同时输出SVG（output_path.with_suffix('.svg')）供回退使用
    """
    # MiKTeX兼容：如果engine是标准名但找不到，尝试miktex-前缀
    engine_exe = engine
    if not shutil.which(engine):
        miktex_fallback = {
            "pdflatex": "miktex-pdftex",
            "xelatex": "miktex-xetex",
            "lualatex": "miktex-luatex",
        }.get(engine)
        if miktex_fallback and shutil.which(miktex_fallback):
            engine_exe = miktex_fallback
            print(f"  [LaTeX] 使用MiKTeX兼容引擎: {engine_exe}")
    
    if not shutil.which(engine_exe):
        print(f"警告: 未找到{engine}/{engine_exe}")
        return False
    
    engine = engine_exe
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tex_file = tmpdir / "diagram.tex"
        
        if r"\documentclass" in latex_code or r"\begin{document}" in latex_code:
            full_tex = latex_code
        else:
            full_tex = r"""
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
""" % latex_code
        
        tex_file.write_text(full_tex, encoding='utf-8')
        
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        
        for i in range(2):
            # Windows上.bat包装器需要shell=True
            use_shell = sys.platform == 'win32'
            result = subprocess.run(
                [engine, "-interaction=nonstopmode", "-halt-on-error", 
                 str(tex_file.name)],
                cwd=str(tmpdir),
                capture_output=True,
                timeout=30,
                encoding='utf-8',
                errors='replace',
                env=env,
                shell=use_shell
            )
            if result.returncode != 0:
                stdout = result.stdout or ""
                print(f"LaTeX编译失败 (尝试{i+1}):")
                error_lines = [l for l in stdout.split('\n') if l.startswith('!')]
                for line in error_lines[:5]:  # 最多5行
                    print(f"  {line}")
                if error_out is not None:
                    error_out.append("\n".join(error_lines[:5]) or (stdout[-300:] if stdout else "编译失败"))
        
        pdf_file = tmpdir / "diagram.pdf"
        if not pdf_file.exists():
            return False

        svg_file = tmpdir / "diagram.svg"
        svg_ok = _pdf_to_svg(pdf_file, svg_file)
        if svg_ok:
            shutil.copy(svg_file, output_path.with_suffix(".svg"))

        # 优先转换为真正的PNG（客户端Glide可直接解码）
        png_file = tmpdir / "diagram.png"
        png_ok = _pdf_to_png(pdf_file, png_file)
        if png_ok and png_file.exists():
            # 限制PNG宽度不超过1000px，避免超出手机屏幕宽度
            _limit_png_width(png_file, max_width=1000)
            shutil.copy(png_file, output_path)
            print(f"  图形渲染成功(PNG): {output_path}")
            return True

        if svg_ok:
            print(f"  图形渲染成功(SVG): {output_path.with_suffix('.svg')} (PNG转换不可用)")
            return True

        print(f"  警告: PDF已生成但无法转换为SVG/PNG")
        return False


def _limit_png_width(path: Path, max_width: int = 1000) -> None:
    """限制PNG宽度（PIL缩放），避免LaTeX图形超出手机屏幕"""
    try:
        from PIL import Image
        img = Image.open(path)
        if img.width > max_width:
            h = int(img.height * max_width / img.width)
            img = img.resize((max_width, h), Image.LANCZOS)
            img.save(path)
            print(f"  图片已缩放到 {max_width}px 宽 (原{img.width}px)")
    except Exception as e:
        print(f"  图片缩放失败(忽略): {e}")


def _pdf_to_png(pdf_path: Path, png_path: Path) -> bool:
    """PDF转换为PNG（供Glide直接显示）"""
    # 方法1: pdftocairo（MiKTeX自带，最可靠）
    if shutil.which("pdftocairo"):
        try:
            output_prefix = str(png_path.with_suffix(""))
            result = subprocess.run(
                ["pdftocairo", "-png", "-singlefile", "-r", "250", str(pdf_path), output_prefix],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0 and is_real_png(png_path):
                print(f"  pdftocairo 转PNG成功")
                return True
        except Exception as e:
            print(f"  pdftocairo 转PNG异常: {e}")

    # 方法2: Inkscape 直接导出PNG
    inkscape_path = shutil.which("inkscape")
    if inkscape_path:
        try:
            result = subprocess.run(
                [inkscape_path, str(pdf_path), "--export-type=png", "--export-filename", str(png_path), "--export-dpi=150"],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0 and is_real_png(png_path):
                print(f"  Inkscape 转PNG成功")
                return True
        except Exception as e:
            print(f"  Inkscape 转PNG异常: {e}")

    # 方法3: 从SVG转PNG（若SVG已生成）
    svg_file = pdf_path.with_suffix(".svg")
    if svg_file.exists() and inkscape_path:
        try:
            result = subprocess.run(
                [inkscape_path, str(svg_file), "--export-type=png", "--export-filename", str(png_path), "--export-dpi=150"],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0 and is_real_png(png_path):
                print(f"  Inkscape(SVG) 转PNG成功")
                return True
        except Exception as e:
            print(f"  Inkscape(SVG) 转PNG异常: {e}")

    return False

def _pdf_to_svg(pdf_path: Path, svg_path: Path) -> bool:
    """PDF转换为SVG"""
    # 方法0: Inkscape（最可靠）
    inkscape_path = shutil.which("inkscape")
    if inkscape_path:
        print(f"  尝试 Inkscape: {inkscape_path}")
        try:
            # 新版本 Inkscape
            result = subprocess.run(
                [inkscape_path, str(pdf_path), 
                 "--export-type=svg", "--export-filename", str(svg_path)],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0 and svg_path.exists():
                print(f"  Inkscape 转换成功")
                return True
            
            # 旧版本 Inkscape
            result2 = subprocess.run(
                [inkscape_path, str(pdf_path), "--export-plain-svg", str(svg_path)],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result2.returncode == 0 and svg_path.exists():
                print(f"  Inkscape (旧版) 转换成功")
                return True
                
            print(f"  Inkscape 失败: {result.stderr[:200] if result.stderr else '无输出'}")
        except Exception as e:
            print(f"  Inkscape 异常: {e}")
            return False
    
    # 方法1: pdf2svg
    if shutil.which("pdf2svg"):
        result = subprocess.run(
            ["pdf2svg", str(pdf_path), str(svg_path), "1"],
            capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0 and svg_path.exists():
            return True
    
    # 方法2: dvisvgm
    if shutil.which("dvisvgm"):
        result = subprocess.run(
            ["dvisvgm", "--pdf", str(pdf_path), "-n", "-o", str(svg_path)],
            capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0 and svg_path.exists():
            return True
    
    # 方法3: pdftocairo
    if shutil.which("pdftocairo"):
        output_prefix = str(svg_path.with_suffix(''))
        result = subprocess.run(
            ["pdftocairo", "-svg", str(pdf_path), output_prefix],
            capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        if svg_path.exists():
            return True
    
    return False

def get_available_latex_engines() -> list:
    """检测可用的LaTeX引擎（支持MiKTeX命名）"""
    engines = ["pdflatex", "xelatex", "lualatex"]
    miktex_map = {
        "pdflatex": "miktex-pdftex",
        "xelatex": "miktex-xetex",
        "lualatex": "miktex-luatex",
    }
    result = []
    for engine in engines:
        if shutil.which(engine):
            result.append(engine)
        else:
            # Try MiKTeX naming
            miktex_engine = miktex_map.get(engine)
            if miktex_engine and shutil.which(miktex_engine):
                result.append(miktex_engine)
    return result