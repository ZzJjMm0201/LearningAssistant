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

def render_latex_blocks(latex_code: str, output_path: Path, engine: str = "xelatex") -> bool:
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
        
        pdf_file = tmpdir / "diagram.pdf"
        if not pdf_file.exists():
            return False
        
        svg_file = tmpdir / "diagram.svg"
        if _pdf_to_svg(pdf_file, svg_file):
            shutil.copy(svg_file, output_path)
            print(f"  图形渲染成功: {output_path}")
            return True
        
        print(f"  警告: PDF已生成但无法转换为SVG")
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