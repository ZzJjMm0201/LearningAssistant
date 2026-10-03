"""AI 动画：让模型写一段 Plotly 代码生成交互式教学动画。

安全设计：AI 生成的 Python 代码在受限子进程中执行——
  - 独立临时目录为 cwd
  - 无 stdin、超时强杀
  - 不继承环境变量中的密钥
这是本服务里风险最高的一环，因此必须说明：即使做了这些限制，
执行模型生成的代码仍不具备真正的沙箱保证。若要用于多租户环境，
应改为"只生成 HTML/JS 数据"而不是执行 Python。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger("aiworker.animation")

_PLOTLY_URLS = [
    "https://cdn.plot.ly/plotly-2.35.2.min.js",
    "https://cdn.staticfile.org/plotly.js/2.32.0/plotly.min.js",
    "https://cdn.bootcdn.net/ajax/libs/plotly.js/2.32.0/plotly.min.js",
    "https://fastly.jsdelivr.net/npm/plotly.js-dist-min@2.32.0/plotly.min.js",
]

_THEME_SCRIPT = """<script>
(function(){
  try{
    var m = new URLSearchParams(location.search).get('theme');
    if(!m) return;
    var light = (m === 'light');
    var bg = light ? '#ffffff' : '#1a1a2e', fg = light ? '#16181d' : '#ffffff';
    document.documentElement.style.background = bg;
    if(document.body){ document.body.style.background = bg; document.body.style.color = fg; }
    var gd = document.querySelector('.plotly-graph-div');
    if(gd && window.Plotly && gd.layout){
      Plotly.relayout(gd, {template: light ? 'plotly_white' : 'plotly_dark',
        paper_bgcolor: bg, plot_bgcolor: bg, 'font.color': fg});
    }
  }catch(e){}
})();
</script>"""


def _ensure_plotly_local(anim_dir: Path) -> str:
    """确保本地有 plotly.min.js。

    手机端访问 CDN 常被墙，动画会白屏。四个 CDN 依次兜底，全失败就返回空——
    此时生成的 HTML 依赖远程 CDN，至少桌面端还能看。
    """
    target = anim_dir / "plotly.min.js"
    if target.exists() and target.stat().st_size > 100_000:
        return "plotly.min.js"
    for url in _PLOTLY_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(target, "wb") as f:
                f.write(resp.read())
            if target.stat().st_size > 100_000:
                return "plotly.min.js"
        except Exception as e:  # noqa: BLE001
            log.warning("下载 plotly 失败 %s: %s", url, e)
    return ""


def generate(text: str, solution: str, engine_name: str, theme: str,
             output_dir: Path, base_url: str) -> dict:
    """生成动画 HTML。返回 {html_url}。"""
    from ..ai import engine

    if not available_engines_check(engine_name):
        return {"html_url": "", "message": "未配置可用的模型 API Key"}

    is_light = (theme or "").lower() == "light"
    template = "plotly_white" if is_light else "plotly_dark"
    paper = "#ffffff" if is_light else "#1a1a2e"
    font_color = "#16181d" if is_light else "white"
    theme_word = "浅色" if is_light else "深色"

    prompt = f"""请根据以下题目，用 Python 的 Plotly 库生成一个交互式动画 HTML。

要求：
1. 动画要能动态展示题目的核心概念或解题过程
2. 几何图形要标注关键点、线、面的名称
3. 函数图像要展示参数变化对图像的影响
4. 必须有播放/暂停按钮或滑块等交互控件
5. 代码中调用 fig.update_layout(template='{template}')，并设置
   paper_bgcolor='{paper}'、plot_bgcolor='{paper}'、font=dict(color='{font_color}')（{theme_word}主题）
6. 最后调用 fig.write_html('figure.html')（可加 include_plotlyjs='cdn'）
7. 只输出 Python 代码，不要注释，不要额外说明

题目：
{text[:2000]}

参考解答：
{solution[:500] if solution else "无"}
"""
    resp = engine.client().complete(
        [{"role": "system", "content": "你是擅长用 Plotly 制作教学动画的编程专家。"},
         {"role": "user", "content": prompt}],
        engine=engine_name, temperature=0.2, max_tokens=5000,
    )
    if not resp:
        return {"html_url": "", "message": "模型未返回内容"}

    m = re.search(r"```python\s*\n(.*?)\n```", resp, re.DOTALL)
    code = m.group(1) if m else resp

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"anim_{abs(hash(text)) % 10**10}"

    # 在独立临时目录执行，避免生成的代码污染项目目录
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        py_file = work / "figure.py"
        py_file.write_text(code, encoding="utf-8")

        # 沙箱化环境：剥离所有密钥
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8",
               "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
               "TEMP": str(work), "TMP": str(work)}
        if sys.platform != "win32":
            env["HOME"] = str(work)

        try:
            r = subprocess.run(
                [sys.executable, str(py_file)], cwd=str(work), env=env,
                capture_output=True, timeout=60, text=True,
                encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return {"html_url": "", "message": "动画生成超时（代码执行超过 60 秒）"}
        except Exception as e:  # noqa: BLE001
            return {"html_url": "", "message": f"动画生成失败: {e}"}

        html_file = work / "figure.html"
        if not html_file.exists():
            err = (r.stderr or "")[-300:]
            return {"html_url": "", "message": f"动画代码未产出 HTML: {err}"}

        content = html_file.read_text(encoding="utf-8", errors="replace")

    # 手机端 CDN 兜底
    local_plotly = _ensure_plotly_local(output_dir)
    if local_plotly:
        content = re.sub(
            r'<script[^>]*src="https?://[^"]*plotly[^"]*\.js"[^>]*></script>',
            f'<script charset="utf-8" src="{base_url.rstrip("/")}/plotly.min.js"></script>',
            content,
        )

    # 移动端适配 + 深浅色切换
    viewport = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">'
    responsive = "<style>html,body{width:100%;height:100%;margin:0;padding:0;overflow-x:hidden;}" \
                 ".plotly-graph-div{width:100% !important;}.main-svg{width:100% !important;}</style>"
    if "<head>" in content:
        content = content.replace("<head>", f"<head>\n{viewport}\n{responsive}", 1)
    else:
        content = f'<!DOCTYPE html>\n<html>\n<head>{viewport}{responsive}</head>\n<body>\n{content}\n</body>\n</html>'

    if "</body>" in content:
        content = content.replace("</body>", _THEME_SCRIPT + "\n</body>", 1)
    else:
        content += _THEME_SCRIPT

    out = output_dir / f"{stem}.html"
    out.write_text(content, encoding="utf-8")
    log.info("动画已生成: %s", out)
    return {"html_url": f"{base_url.rstrip('/')}/{out.name}"}


def available_engines_check(engine_name: str) -> bool:
    from ..ai import engine

    try:
        engine.resolve(engine_name)
        return True
    except RuntimeError:
        return False