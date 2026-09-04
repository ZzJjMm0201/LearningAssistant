"""
LaTeX图形处理工具
- 从Markdown文本中提取LaTeX代码块
- 调用tikz_md_renderer进行渲染
- 替换代码块为图片引用
"""
import re
import os
import hashlib
from pathlib import Path
from typing import List, Tuple
from server.utils.tikz_md_renderer import render_latex_blocks, is_real_png


def _make_dark_png(png_path) -> None:
    """⑧ 生成深色版 PNG（白底黑字 → 黑底白字），文件名加 _dark 后缀
    供客户端深色模式加载 LaTeX 图时使用（反转像素亮度）"""
    try:
        from PIL import Image
        from PIL import ImageChops
        p = Image.open(png_path).convert("RGB")
        inverted = ImageChops.invert(p)
        dark_path = png_path.with_name(png_path.stem + "_dark" + png_path.suffix)
        inverted.save(dark_path)
        print(f"  [LaTeX] 生成深色版: {dark_path.name}")
    except Exception as e:
        print(f"  [LaTeX] 深色版生成失败(忽略): {e}")


_REVIEW_PROMPT = (
    "请审查这张数学图形渲染图，只关注【严重影响理解】的问题：文字重叠、标注错误、图形与题目明显不符、"
    "坐标轴/比例明显错误等。如果图形基本正确、不影响理解，只回复 OK。"
    "如果存在明显问题，用一句话描述问题所在（不要给修复代码，只描述问题）。"
)


def review_latex_image(png_path, ai_service, vision_model=None, engine=None) -> str:
    """⑦ 用视觉模型审查 LaTeX 渲染图，返回问题描述；无问题返回空字符串"""
    try:
        if ai_service is None:
            return ""
        text, _ = ai_service.recognize_image_with_vision(str(png_path), model=vision_model, prompt=_REVIEW_PROMPT)
        text = (text or "").strip()
        if not text or text.upper() in ("OK", "OK。", "OK！", "无", "无问题", "图形正确", "没问题"):
            return ""
        # 带前缀的情况，如 "OK，图形正确" — 视为无问题
        if text.upper().startswith("OK"):
            return ""
        return text[:200]
    except Exception as e:
        print(f"  [LaTeX审核] 视觉审图失败（忽略）: {e}")
        return ""


def extract_latex_blocks(text: str) -> List[Tuple[str, str, int, int]]:
    """
    提取文本中的LaTeX代码块
    返回: [(block_id, latex_code, start_pos, end_pos), ...]
    """
    pattern = r'```latex\s*\n(.*?)\n```'
    blocks = []
    
    for match in re.finditer(pattern, text, re.DOTALL):
        code = match.group(1).strip()
        if code:
            block_id = hashlib.md5(code.encode()).hexdigest()[:8]
            blocks.append((block_id, code, match.start(), match.end()))
    
    return blocks

def process_latex_blocks(md_text: str, output_dir: Path) -> str:
    """
    处理Markdown中的LaTeX代码块：
    1. 提取所有LaTeX块
    2. 渲染为SVG
    3. 替换为图片引用
    
    Args:
        md_text: 包含LaTeX代码块的Markdown文本
        output_dir: SVG输出目录
    
    Returns:
        处理后的Markdown文本
    """
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    
    blocks = extract_latex_blocks(md_text)
    if not blocks:
        return md_text
    
    # 从后往前替换，避免位置偏移
    result = md_text
    for block_id, code, start, end in reversed(blocks):
        svg_path = output_dir / f"diagram_{block_id}.svg"
        png_path = output_dir / f"diagram_{block_id}.png"
        
        # 渲染LaTeX为SVG + 真实PNG
        success = render_latex_blocks(code, png_path, engine="xelatex")
        
        if success:
            # 优先使用真正的PNG（客户端Glide可直接解码）
            if is_real_png(png_path):
                _make_dark_png(png_path)  # ⑧ 深色版
                replacement = f"\n\n![图解]({png_path.name})\n\n"
            elif svg_path.exists():
                replacement = f"\n\n![图解]({svg_path.name})\n\n"
            else:
                replacement = f"\n\n（*图形渲染失败*）\n\n"
        else:
            # 渲染失败：给出失败提示
            replacement = f"\n\n（*图形渲染失败*）\n\n"
        
        result = result[:start] + replacement + result[end:]
    
    return result

def process_latex_blocks_with_retry(md_text: str, output_dir: Path, ai_service=None, engine: str = None, model: str = None, max_retries: int = 3, vision_model: str = None, enable_review: bool = False) -> str:
    """
    处理Markdown中的LaTeX代码块（带AI修复重试 + ⑦ 视觉审核）：
    1. 编译失败 → 把关键报错发给AI修复，最多重试 max_retries 次
    2. enable_review 时：渲染成功后用视觉模型审图，明显有问题→再修复；3次仍未过→备注
    3. 重试仍失败 → 移除该图形块（客户端不显示代码）
    """
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    
    blocks = extract_latex_blocks(md_text)
    if not blocks:
        return md_text
    
    result = md_text
    for block_id, code, start, end in reversed(blocks):
        final_code = code
        cur_block_id = block_id
        success = False
        error_text = "编译失败"
        
        for attempt in range(max_retries + 1):
            png_path = output_dir / f"diagram_{cur_block_id}.png"
            svg_path = output_dir / f"diagram_{cur_block_id}.svg"
            err_out = []
            ok = render_latex_blocks(final_code, png_path, engine="xelatex", error_out=err_out)
            if ok:
                success = True
                break
            error_text = err_out[0] if err_out else "编译失败"
            if ai_service is not None and attempt < max_retries:
                print(f"[LaTeX] 第{attempt + 1}次编译失败，交给AI修复... 错误: {error_text[:160]}")
                fixed = ai_service.fix_latex(final_code, error_text, engine=engine, model=model)
                if fixed and fixed.strip() and fixed != final_code:
                    final_code = fixed
                    cur_block_id = hashlib.md5(final_code.encode()).hexdigest()[:8]
                else:
                    print(f"[LaTeX] AI未给出有效修复，停止重试")
                    break
        
        if success:
            png_path = output_dir / f"diagram_{cur_block_id}.png"
            svg_path = output_dir / f"diagram_{cur_block_id}.svg"
            # ⑦ 视觉审核：显严重问题时再修复（最多3轮），仍不过则备注
            review_note = ""
            if enable_review and is_real_png(png_path) and ai_service is not None:
                for rv in range(3):
                    suggestion = review_latex_image(png_path, ai_service, vision_model=vision_model, engine=engine)
                    if not suggestion:
                        break
                    print(f"  [LaTeX审核] 第{rv + 1}次发现图形问题: {suggestion[:100]}")
                    # 用视觉模型的问题描述让AI修复
                    fixed = ai_service.fix_latex(final_code, f"图形问题描述：{suggestion}", engine=engine, model=model)
                    if fixed and fixed.strip() and fixed != final_code:
                        final_code = fixed
                        cur_block_id = hashlib.md5(final_code.encode()).hexdigest()[:8]
                        old_png = png_path
                        png_path = output_dir / f"diagram_{cur_block_id}.png"
                        svg_path = output_dir / f"diagram_{cur_block_id}.svg"
                        if render_latex_blocks(final_code, png_path, engine="xelatex"):
                            continue
                        else:
                            png_path = old_png  # 修复失败，回退旧图
                            cur_block_id = hashlib.md5(final_code.encode()).hexdigest()[:8]
                    else:
                        break
                else:
                    review_note = "（⚠️图形可能有误，已多次修正仍未通过审核）"
            if is_real_png(png_path):
                _make_dark_png(png_path)  # ⑧ 深色版
                replacement = f"\n\n![图解]({png_path.name})\n\n"
                if review_note:
                    replacement += f">{review_note}\n\n"
            elif svg_path.exists():
                replacement = f"\n\n![图解]({svg_path.name})\n\n"
            else:
                replacement = f"\n\n>  ⚠️ 图形生成失败{review_note}\n\n"
        else:
            # 重试仍失败：移除该块并给出失败提示，不显示LaTeX代码
            print(f"[LaTeX] 重试{max_retries}次仍失败，移除该图形块: {error_text[:150]}")
            replacement = "\n\n（*图形渲染失败*）\n\n"
        
        result = result[:start] + replacement + result[end:]
    
    return result


def extract_question_info_from_solution(solution_text: str) -> dict:
    """
    从AI生成的完整解析末尾提取题目信息
    """
    info = {
        "subject": "未知",
        "difficulty": "未知", 
        "knowledge_points": [],
    }
    
    patterns = {
        "subject": r'\*\*学科[：:]\s*\*\*\s*(.+)',
        "difficulty": r'\*\*题目难度[：:]\s*\*\*\s*(.+)',
        "knowledge_points": r'\*\*知识点[：:]\s*\*\*\s*(.+)',
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, solution_text)
        if match:
            value = match.group(1).strip()
            if key == "knowledge_points":
                info[key] = [kp.strip() for kp in re.split(r'[、,，]', value) if kp.strip()]
            else:
                info[key] = value
    
    return info

def clean_markdown_for_display(md_text: str) -> str:
    """
    清理Markdown文本，优化显示效果
    """
    # 处理行内公式
    text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', r'$$\1$$', md_text)
    
    # 处理LaTeX公式中的特殊字符
    text = text.replace(r'\\(', '$$')
    text = text.replace(r'\\)', '$$')
    text = text.replace(r'\\[', '$$')
    text = text.replace(r'\\]', '$$')
    
    # 确保段落间有空行
    text = re.sub(r'([^\n])\n(#{1,6}\s)', r'\1\n\n\2', text)
    
    return text

def format_mind_map_for_display(mind_map_text: str) -> str:
    """
    格式化思维导图文本，便于显示
    """
    lines = mind_map_text.strip().split('\n')
    formatted = []
    
    for line in lines:
        # 替换Unicode树形字符
        line = line.replace('├', '├').replace('└', '└').replace('│', '│')
        line = line.replace('─', '─').replace('→', '→')
        
        # 确保缩进一致
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        
        # 统一使用4空格缩进
        new_indent = (indent // 2) * 2  
        formatted.append(' ' * new_indent + stripped)
    
    return '\n'.join(formatted)