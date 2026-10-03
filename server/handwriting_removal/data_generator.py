"""
自动生成手写擦除训练数据
生成"作业页面"背景 + 模拟手写批注 → 配对训练数据
输出: input_X.jpg (有手写) / target_X.jpg (干净)

使用方式:
    python data_generator.py --output_dir ./dataset --num_pairs 2000
"""

import os
import random
import math
import argparse
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
from tqdm import tqdm


# ─── 题库 ─────────────────────────────────────────────────────────────────

MATH_PROBLEMS = [
    "1. 计算：12 + 25 = _____",
    "2. 计算：36 × 4 = _____",
    "3. 计算：81 ÷ 9 = _____",
    "4. 计算：125 - 78 = _____",
    "5. 解方程：x + 15 = 30，x = _____",
    "6. 计算：7 × 8 + 15 = _____",
    "7. 长方形周长公式：C = _____",
    "8. 圆的面积公式：S = _____",
    "9. 计算：15 + ( -3 ) = _____",
    "10. 解方程：3x + 5 = 20，x = _____",
    "11. 梯形面积公式：S = _____",
    "12. 分数化简：12/18 = _____",
    "13. 计算：3.14 × 5² = _____",
    "14. 小数乘法：0.25 × 4 = _____",
    "15. 计算：π ≈ _____",
    "16. 计算：√16 + √25 = _____",
    "17. 因式分解：x² - 4 = _____",
    "18. 勾股定理：a² + b² = _____",
    "19. 解方程组：{x + y = 5, x - y = 1}",
    "20. 二次函数顶点坐标公式：(-b/2a, _____)",
    "21. 计算：sin 30° = _____",
    "22. 计算：cos 60° = _____",
    "23. 等差数列求和：S_n = n(a₁ + a_n)/____",
    "24. 计算：(-2)³ = _____",
    "25. 完全平方公式：(a + b)² = _____",
]

CHINESE_PROBLEMS = [
    "1. 下列词语中书写完全正确的一项是（  ）",
    "2. 将下列句子翻译成现代汉语。",
    "3. 默写《静夜思》：床前明月光，_____",
    "4. 写出下列词语的反义词。",
    "5. 请用'不仅…而且…'造句。",
    "6. 根据拼音写汉字：___ (jìng) 爱",
    "7. 下列句子没有语病的是（  ）",
    "8. 补全成语：画蛇添（  ）",
    "9. 写出下列诗句的作者。",
    "10. 请用'虽然…但是…'造句。",
    "11. 判断下列词语是褒义词还是贬义词。",
    "12. 阅读理解：本文的中心思想是什么？",
    "13. 组词：清（  ）（  ）（  ）",
    "14. 默写《春晓》：春眠不觉晓，_____",
    "15. 修改病句：他不但学习好，而且品德好。",
]

ENGLISH_PROBLEMS = [
    "1. Fill in the blank: I ____ a student. (am/is/are)",
    "2. Translate: 你好 → _____",
    "3. Change to past tense: go → _____",
    "4. Fill in: There ____ two apples on the table. (is/are)",
    "5. Write the plural: child → _____",
    "6. Complete: She ____ to school yesterday. (go)",
    "7. Fill in: He is ____ tallest boy in class. (a/an/the)",
    "8. Write the opposite: big → _____",
    "9. Complete: I have ____ (许多) friends.",
    "10. Fill in: The cat is ____ the box. (in/on/under)",
]

ALL_PROBLEMS = MATH_PROBLEMS + CHINESE_PROBLEMS + ENGLISH_PROBLEMS

# 手写批注内容
HANDWRITING_NOTES = [
    "✓", "×", "△", "○",
    "注意！", "错了", "更正", "√",
    "再检查一遍", "这里不对",
    "提示", "补充",
    "~", "……",
    "good", "well done",
    "认真审题", "关键点",
    "此题为必考题",
    "背下来",
    "公式要记住",
    "粗心了", "下次注意",
    "看课本P25",
    "写过程",
    "别跳步",
    "验算一遍",
    "画图辅助",
]

# 数学答案 / 数字笔迹
HANDWRITING_ANSWERS = [
    "37", "144", "9", "47",
    "15", "71", "2(a+b)", "πr²",
    "12", "5", "(a+b)h/2",
    "2/3", "78.5", "1", "3.14",
    "9", "(x+2)(x-2)", "c²",
    "x=3, y=2",
    "(4ac-b²)/4a",
    "1/2", "1/2",
    "n", "-8",
    "a²+2ab+b²",
    "√2 ≈ 1.414",
    "S = vt",
    "F = ma",
    "V = IR",
    "E = mc²",
]

# 常见手写标记
MARKER_TYPES = ["underline", "circle", "strikethrough"]


def get_font_paths():
    """获取系统中可用的字体路径"""
    fonts = {}

    # 印刷体
    printed_candidates = [
        ("songti", r"C:\Windows\Fonts\simsun.ttc"),
        ("yahei", r"C:\Windows\Fonts\msyh.ttc"),
        ("heiti", r"C:\Windows\Fonts\simhei.ttf"),
    ]
    for name, path in printed_candidates:
        if os.path.exists(path):
            fonts[name] = path

    # 手写体
    handwriting_candidates = [
        ("kaiti", r"C:\Windows\Fonts\simkai.ttf"),
        ("fangsong", r"C:\Windows\Fonts\simfang.ttf"),
    ]
    for name, path in handwriting_candidates:
        if os.path.exists(path):
            fonts[name] = path

    if not fonts:
        raise FileNotFoundError(
            "未找到系统字体，请确保 C:\\Windows\\Fonts 下有 simsun.ttc / msyh.ttc / simkai.ttf"
        )
    return fonts


def random_color(style="printed"):
    """随机颜色"""
    if style == "printed":
        return random.choice([(0, 0, 0), (10, 10, 10), (20, 20, 20)])
    elif style == "handwriting":
        return random.choice([
            (0, 0, 0),         # 黑色钢笔/铅笔
            (30, 30, 30),      # 深灰铅笔
            (20, 20, 200),     # 蓝色圆珠笔
            (180, 20, 20),     # 红色批改笔
            (40, 80, 40),      # 墨绿色
        ])


def draw_text_wrapping(draw, text, font, x, y, max_width, fill, line_spacing=5):
    """
    带换行的文字绘制
    返回最后绘制位置的 y 坐标
    """
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        text_width = bbox[2] - bbox[0]
        if text_width > max_width and current_line:
            lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)

    current_y = y
    for line in lines:
        draw.text((x, current_y), line, fill=fill, font=font)
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1] + line_spacing
        current_y += line_height

    return current_y


def draw_printed_content(draw, img_size, font_paths):
    """
    在图片上绘制印刷体题目内容
    返回: 内容区域 (x1, y1, x2, y2)
    """
    margin = 30
    y = margin
    current_x = margin
    max_width = img_size - 2 * margin

    # 加载字体
    try:
        title_font = ImageFont.truetype(font_paths.get("heiti", font_paths["songti"]), 28)
    except Exception:
        title_font = ImageFont.load_default()

    try:
        problem_font = ImageFont.truetype(font_paths["songti"], random.choice([20, 22, 24]))
    except Exception:
        problem_font = ImageFont.load_default()

    # 题目标题
    draw.text((current_x, y), "作业练习", fill=(0, 0, 0), font=title_font)
    y += 45

    # 分割线
    draw.line([(margin, y), (img_size - margin, y)], fill=(180, 180, 180), width=1)
    y += 20

    # 随机选择 3-6 道题
    num_problems = random.randint(3, 6)
    selected_problems = random.sample(ALL_PROBLEMS, min(num_problems, len(ALL_PROBLEMS)))

    for problem in selected_problems:
        if y > img_size - 80:
            break
        indent = random.randint(0, 20)
        y = draw_text_wrapping(
            draw, problem, problem_font,
            current_x + indent, y, max_width - indent,
            fill=random_color("printed"),
            line_spacing=8
        )
        y += random.randint(10, 25)

    return (margin, 30, img_size - margin, y)


def overlay_handwriting(base_img, content_region, font_paths):
    """
    在 base_img 上叠加手写笔迹
    直接修改 base_img
    """
    draw = ImageDraw.Draw(base_img)
    img_size = base_img.width

    # 加载手写字体
    hw_font_path = font_paths.get("kaiti", font_paths.get("fangsong", list(font_paths.values())[0]))
    hw_font_size = random.choice([18, 20, 22, 24, 26])
    small_hw_font_size = random.choice([14, 15, 16])
    try:
        handwritten_font = ImageFont.truetype(hw_font_path, hw_font_size)
        small_hw_font = ImageFont.truetype(hw_font_path, small_hw_font_size)
    except Exception:
        handwritten_font = ImageFont.load_default()
        small_hw_font = ImageFont.load_default()

    # 获取内容区域边界
    if content_region:
        cx1, cy1, cx2, cy2 = content_region
    else:
        cx1, cy1, cx2, cy2 = 20, 20, img_size - 20, img_size - 20

    # 1. 在题目旁边写答案 (2-5 个)
    num_answers = random.randint(2, 5)
    for _ in range(num_answers):
        ax = random.randint(cx1, max(cx1 + 10, cx2 - 80))
        ay = random.randint(cy1 + 20, max(cy1 + 30, cy2 - 20))
        answer = random.choice(HANDWRITING_ANSWERS)
        hw_color = random_color("handwriting")
        draw.text((ax, ay), answer, fill=hw_color, font=small_hw_font)

    # 2. 写字词批注 (1-4 个)
    num_notes = random.randint(1, 4)
    for _ in range(num_notes):
        nx = random.randint(cx1, max(cx1 + 10, cx2 - 100))
        ny = random.randint(cy1, max(cy1 + 5, cy2 - 30))
        note = random.choice(HANDWRITING_NOTES)
        hw_color = random_color("handwriting")
        draw.text((nx, ny), note, fill=hw_color, font=handwritten_font)

    # 3. 画标记 (下划线、圈注等)
    num_markers = random.randint(1, 3)
    for _ in range(num_markers):
        marker_type = random.choice(MARKER_TYPES)
        mx = random.randint(cx1, cx2 - 50)
        my = random.randint(cy1 + 10, max(cy1 + 15, cy2 - 10))
        mw = random.randint(30, min(120, cx2 - mx))

        hw_color = random_color("handwriting")
        if marker_type == "underline":
            draw.line([(mx, my), (mx + mw, my)], fill=hw_color, width=random.randint(1, 3))
        elif marker_type == "circle":
            r = random.randint(12, 22)
            draw.ellipse([mx - r, my - r, mx + r, my + r],
                         outline=hw_color, width=random.randint(1, 2))
        elif marker_type == "strikethrough":
            draw.line([(mx, my - 8), (mx + mw, my - 8)],
                      fill=(180, 20, 20), width=random.randint(1, 2))

    # 4. 随机装饰性笔迹 (0-3 条短划线)
    num_doodles = random.randint(0, 3)
    for _ in range(num_doodles):
        sx = random.randint(20, img_size - 20)
        sy = random.randint(cy1, cy2)
        ex = sx + random.randint(-30, 30)
        ey = sy + random.randint(-10, 30)
        hw_color = random_color("handwriting")
        draw.line([(sx, sy), (ex, ey)], fill=hw_color, width=random.randint(1, 2))

    # 5. 打勾/打叉 (50% 概率)
    if random.random() < 0.5:
        mark = random.choice(["✓", "×"])
        mx2 = random.randint(cx1, max(cx1, cx2 - 30))
        my2 = random.randint(cy1, max(cy1, cy2 - 30))
        hw_color = random_color("handwriting")
        try:
            big_font = ImageFont.truetype(hw_font_path, random.choice([28, 32, 36]))
        except Exception:
            big_font = handwritten_font
        draw.text((mx2, my2), mark, fill=hw_color, font=big_font)


def generate_one_pair(idx, img_size, font_paths):
    """
    生成一对训练数据
    返回: (input_img, target_img)
    """
    # 1. 创建白色背景，加轻微噪点
    base = Image.new('RGB', (img_size, img_size), 'white')
    if random.random() < 0.5:
        noise = np.random.randint(0, 8, (img_size, img_size, 3), dtype=np.uint8)
        noise_img = Image.fromarray(noise, 'RGB')
        base = Image.blend(base, noise_img, 0.02)

    draw = ImageDraw.Draw(base)

    # 2. 绘制印刷体内容
    content_region = draw_printed_content(draw, img_size, font_paths)

    # 3. 复制一份作为 target (干净图)
    target = base.copy()

    # 4. 在 base 上叠加手写 → input 图
    overlay_handwriting(base, content_region, font_paths)

    # 5. 偶尔加一点模糊
    if random.random() < 0.3:
        blur_r = random.uniform(0.3, 1.0)
        base = base.filter(ImageFilter.GaussianBlur(radius=blur_r))
        target = target.filter(ImageFilter.GaussianBlur(radius=blur_r))

    return base, target


def generate_dataset(output_dir, num_pairs=2000, img_size=512):
    """生成完整数据集"""
    # 加载字体 (提前验证)
    print("正在检测系统字体...")
    font_paths = get_font_paths()
    print(f"  找到字体: {list(font_paths.keys())}")

    # 创建目录
    train_input_dir = os.path.join(output_dir, 'train', 'input')
    train_target_dir = os.path.join(output_dir, 'train', 'target')
    val_input_dir = os.path.join(output_dir, 'val', 'input')
    val_target_dir = os.path.join(output_dir, 'val', 'target')

    for d in [train_input_dir, train_target_dir, val_input_dir, val_target_dir]:
        os.makedirs(d, exist_ok=True)

    # 10% 验证集
    val_count = max(1, num_pairs // 10)
    train_count = num_pairs - val_count

    print(f"生成训练数据: {train_count} 对")
    print(f"生成验证数据: {val_count} 对")
    print(f"图片尺寸: {img_size}x{img_size}")

    # 训练集
    for i in tqdm(range(train_count), desc="训练数据"):
        idx = i
        input_img, target_img = generate_one_pair(idx, img_size, font_paths)
        input_img.save(os.path.join(train_input_dir, f'input_{idx:05d}.jpg'), 'JPEG', quality=95)
        target_img.save(os.path.join(train_target_dir, f'target_{idx:05d}.jpg'), 'JPEG', quality=95)

    # 验证集
    for i in tqdm(range(val_count), desc="验证数据"):
        idx = i + 100000
        input_img, target_img = generate_one_pair(idx, img_size, font_paths)
        input_img.save(os.path.join(val_input_dir, f'input_{idx:05d}.jpg'), 'JPEG', quality=95)
        target_img.save(os.path.join(val_target_dir, f'target_{idx:05d}.jpg'), 'JPEG', quality=95)

    print(f"\n✓ 数据集生成完成!")
    print(f"  训练集: {train_input_dir} / {train_target_dir}")
    print(f"  验证集: {val_input_dir} / {val_target_dir}")
    print(f"  总计: {num_pairs} 对")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成手写擦除训练数据')
    parser.add_argument('--output_dir', type=str, default='./dataset',
                        help='输出目录')
    parser.add_argument('--num_pairs', type=int, default=2000,
                        help='生成训练数据对数')
    parser.add_argument('--img_size', type=int, default=512,
                        help='图片尺寸')
    args = parser.parse_args()

    generate_dataset(args.output_dir, args.num_pairs, args.img_size)
