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
                replacement = f"![图解]({png_path.name})"
            elif svg_path.exists():
                replacement = f"![图解]({svg_path.name})"
            else:
                replacement = f"```latex\n{code}\n```\n*(图形渲染失败)*"
        else:
            # 渲染失败时保留原始代码
            replacement = f"```latex\n{code}\n```\n*(图形渲染失败，请检查LaTeX代码)*"
        
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