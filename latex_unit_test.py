# -*- coding: utf-8 -*-
"""LaTeX渲染链路单测：TikZ代码块 → 真PNG + URL替换"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
from server.utils.latex_processor import process_latex_blocks
from server.utils.tikz_md_renderer import is_real_png

sample = """这是一道导数题。

```latex
\\begin{tikzpicture}
\\draw[->] (-2,0) -- (2,0) node[right] {$x$};
\\draw[->] (0,-1) -- (0,3) node[above] {$y$};
\\draw[domain=-1.5:1.5, smooth, thick, blue] plot (\\x, {\\x*\\x});
\\node at (1,1.8) {$y=x^2$};
\\end{tikzpicture}
```

答案：$f'(x)=2x$。
"""

out_dir = Path("history") / "svgs_unittest"
out_dir.mkdir(exist_ok=True)

# 清理旧文件
for f in out_dir.iterdir():
    f.unlink()

result = process_latex_blocks(sample, out_dir)
print("=== 处理后的Markdown ===")
print(result)
print("=== 目录文件 ===")
ok = True
for f in sorted(out_dir.iterdir()):
    if f.suffix == ".png":
        ok = ok and is_real_png(f)
        print(f.name, f.stat().st_size, "bytes", "REAL-PNG" if is_real_png(f) else "BAD")
    else:
        print(f.name, f.stat().st_size, "bytes", "SVG")

# 模拟 solve_pipeline 的 URL 替换（动态Host）
import re
base_url = "http://10.100.55.231:8000/static/svgs_testid"
processed = re.sub(
    r'!\[([^\]]*)\]\((diagram_[^)]+\.(?:svg|png))\)',
    rf'![\1]({base_url}/\2)',
    result
)
urls = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', processed)
print("=== URL替换后 ===")
print(processed)
print("URL数:", len(urls), urls[:2])
print("UNITTEST", "PASS" if urls and ok else "FAIL")
