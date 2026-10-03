"""
AI 动画生成服务
让 AI 生成 Plotly 动画 HTML 文件
"""
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from server.config import HISTORY_DIR
from server.services.ai_service import ai_service


def _ensure_plotly_local(anim_dir: Path) -> str:
    """确保本地 plotly.min.js 存在，返回文件名；失败返回空串（手机端 CDN 被墙会导致动画白屏）"""
    target = anim_dir / "plotly.min.js"
    if target.exists() and target.stat().st_size > 100_000:
        return "plotly.min.js"
    urls = [
        "https://cdn.plot.ly/plotly-2.35.2.min.js",
        "https://cdn.staticfile.org/plotly.js/2.32.0/plotly.min.js",
        "https://cdn.bootcdn.net/ajax/libs/plotly.js/2.32.0/plotly.min.js",
        "https://fastly.jsdelivr.net/npm/plotly.js-dist-min@2.32.0/plotly.min.js",
    ]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as resp, open(target, "wb") as f:
                f.write(resp.read())
            if target.stat().st_size > 100_000:
                return "plotly.min.js"
        except Exception as e:
            print(f"[动画] 下载 plotly 失败 {u}: {e}")
    return ""


def generate_animation(ocr_text: str, solution: str = "", engine: str = None, theme: str = "dark") -> str | None:
    """
    根据题目内容生成动画 HTML 文件
    
    Args:
        ocr_text: OCR 识别的题目文本
        solution: AI 已生成的解答（可选，帮助 AI 理解题目）
        theme: 界面主题（dark/light）。生成时固化一版配色，同时在 HTML 内注入按 ?theme= 切换的脚本，
               避免“浅色界面下动画仍是深色背景”（11.1）
    
    Returns:
        str: 生成的 HTML 文件路径，失败返回 None
    """
    is_light = (theme or "").strip().lower() == "light"
    tpl = "plotly_white" if is_light else "plotly_dark"
    paper = "#ffffff" if is_light else "#1a1a2e"
    font_color = "#16181D" if is_light else "white"
    theme_word = "浅色" if is_light else "深色"
    prompt = f"""请根据以下题目，使用 Python 的 Plotly 库生成一个交互式动画 HTML 文件。
要求：
1. 动画要能动态展示题目的核心概念或解题过程
2. 如果涉及几何图形，要标注关键点、线、面的名称
3. 如果有函数图像，要展示参数变化对图像的影响
4. 动画要有播放/暂停按钮、滑块等交互控件
    5. 配色：使用{theme_word}主题——必须在代码中调用 `fig.update_layout(template='{tpl}')`，并设置 `paper_bgcolor='{paper}'`、`plot_bgcolor='{paper}'`，以及 `font=dict(color='{font_color}')`
5. 代码保存为 HTML 格式，命名为 figure.html
6. 只输出 Python 代码，不要写注释，不要有额外说明

题目：
{ocr_text}

参考解答：
{solution[:500] if solution else "无"}
"""
    
    # 调用 AI 生成代码
    code_response = ai_service._call_api([
        {"role": "system", "content": "你是一个擅长用 Plotly 制作教学动画的编程专家。"},
        {"role": "user", "content": prompt}
    ], max_tokens=4000, engine=engine)
    
    if not code_response:
        return None
    
    # 提取 Python 代码
    import re
    code_match = re.search(r'```python\s*\n(.*?)\n```', code_response, re.DOTALL)
    if code_match:
        code = code_match.group(1)
    else:
        code = code_response

    # 强制主题的后处理：如果生成的代码包含 fig 对象，则确保使用目标主题配色
    theme_snippet = f"""
try:
    fig.update_layout(template='{tpl}', paper_bgcolor='{paper}', plot_bgcolor='{paper}', font=dict(color='{font_color}'))
except Exception:
    pass
"""
    code = code + "\n" + theme_snippet
    
    # 保存并执行代码
    anim_dir = HISTORY_DIR / "animations"
    anim_dir.mkdir(exist_ok=True)
    
    py_file = anim_dir / "figure.py"
    html_file = anim_dir / "figure.html"
    
    py_file.write_text(code, encoding='utf-8')
    
    # 执行 Python 代码生成 HTML
    try:
        result = subprocess.run(
            ["python", str(py_file)],
            cwd=str(anim_dir),
            capture_output=True, text=True,
            timeout=30,
            encoding='utf-8', errors='replace'
        )
        
        if html_file.exists():
            print(f"动画生成成功: {html_file}")
            # Bug 3: 添加移动端适配 - 注入viewport和响应式样式
            html_content = html_file.read_text(encoding='utf-8')
            # 手机端 CDN 可能被墙：把 plotly 的 CDN script 换成本地 plotly.min.js
            local_plotly = _ensure_plotly_local(anim_dir)
            if local_plotly:
                html_content = re.sub(
                    r'<script[^>]*src="https?://cdn\.plot\.ly/[^"]*plotly[^"]*\.js"[^>]*></script>',
                    f'<script charset="utf-8" src="{local_plotly}"></script>',
                    html_content
                )
            viewport_meta = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">'
            responsive_css = '<style>html,body{width:100%;height:100%;margin:0;padding:0;overflow-x:hidden;} .plotly-graph-div{width:100% !important;} .main-svg{width:100% !important;}</style>'
            # 11.1 深浅色适配：iframe 用 ?theme=light|dark 指定主题时，运行时切换配色
            theme_script = """<script>
(function(){
  try{
    var m = new URLSearchParams(location.search).get('theme');
    if(!m) return;
    var light = (m === 'light');
    var bg = light ? '#ffffff' : '#1a1a2e', fg = light ? '#16181D' : '#ffffff';
    document.documentElement.style.background = bg;
    if(document.body){ document.body.style.background = bg; document.body.style.color = fg; }
    var gd = document.querySelector('.plotly-graph-div');
    if(gd && window.Plotly && gd.layout){
      Plotly.relayout(gd, {template: light ? 'plotly_white' : 'plotly_dark', paper_bgcolor: bg, plot_bgcolor: bg, 'font.color': fg});
    }
  }catch(e){}
})();
</script>"""
            if '<head>' in html_content:
                html_content = html_content.replace('<head>', f'<head>\n{viewport_meta}\n{responsive_css}')
            elif '<html>' in html_content:
                html_content = html_content.replace('<html>', f'<html>\n<head>\n{viewport_meta}\n{responsive_css}\n</head>')
            else:
                html_content = f'<!DOCTYPE html>\n<html>\n<head>\n{viewport_meta}\n{responsive_css}\n</head>\n<body>\n{html_content}\n</body>\n</html>'
            if '</body>' in html_content:
                html_content = html_content.replace('</body>', theme_script + '\n</body>')
            else:
                html_content += theme_script
            html_file.write_text(html_content, encoding='utf-8')
            py_file.unlink()  # 删除 .py 文件
            return str(html_file)
        else:
            print(f"动画生成失败: {result.stderr}")
            return None
            
    except Exception as e:
        print(f"执行动画代码失败: {e}")
        return None