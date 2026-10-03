"""
推理脚本 — 手写擦除模型
输入一张带手写的图片，返回擦除后的图片

使用方式:
    # 命令行
    python inference.py --image input.jpg --model checkpoints/handwriting_removal.pth --output clean.jpg

    # Python API
    from inference import remove_handwriting
    clean_img = remove_handwriting('input.jpg', 'checkpoints/handwriting_removal.pth')
"""

import os
import argparse
import numpy as np
from PIL import Image

import torch
import torch.nn as nn
from torchvision import transforms

from model import LightUNet


def load_model(model_path: str, device: torch.device) -> nn.Module:
    """
    加载训练好的模型

    支持两种格式:
    1. 完整 checkpoint: {'model_state_dict': ...}
    2. 纯权重文件: state_dict
    """
    model = LightUNet()

    if not os.path.exists(model_path):
        raise FileNotFoundError(f"模型文件不存在: {model_path}")

    checkpoint = torch.load(model_path, map_location='cpu')

    if isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
        # 完整 checkpoint 格式
        model.load_state_dict(checkpoint['model_state_dict'])
        epoch = checkpoint.get('epoch', '未知')
        val_loss = checkpoint.get('val_loss', '未知')
        print(f"加载模型 (epoch={epoch}, val_loss={val_loss})")
    else:
        # 纯权重文件
        model.load_state_dict(checkpoint)
        print("加载模型 (纯权重)")

    model = model.to(device)
    model.eval()
    return model


def preprocess(image: Image.Image, img_size: int = 512) -> torch.Tensor:
    """
    预处理: 调整大小、转 Tensor 并归一化到 [0, 1]
    输出: [1, 3, H, W]
    """
    # 等比例缩放，短边对齐 img_size
    w, h = image.size
    if w > h:
        new_w = int(w * img_size / h)
        new_h = img_size
    else:
        new_w = img_size
        new_h = int(h * img_size / w)

    image = image.resize((new_w, new_h), Image.LANCZOS)

    # 中心裁剪
    left = (new_w - img_size) // 2
    top = (new_h - img_size) // 2
    image = image.crop((left, top, left + img_size, top + img_size))

    # 转换为 tensor [0, 1]
    to_tensor = transforms.ToTensor()
    tensor = to_tensor(image)  # [3, H, W]
    tensor = tensor.unsqueeze(0)  # [1, 3, H, W]
    return tensor


def postprocess(tensor: torch.Tensor) -> np.ndarray:
    """
    后处理: tensor → numpy image (H, W, 3), 范围 [0, 255], uint8
    """
    tensor = tensor.squeeze(0)  # [3, H, W]
    tensor = tensor.clamp(0, 1)
    tensor = tensor.detach().cpu()
    np_img = tensor.numpy().transpose(1, 2, 0)  # [H, W, 3]
    np_img = (np_img * 255).astype(np.uint8)
    return np_img


def remove_handwriting(
    image_path: str,
    model_path: str,
    output_path: str = None,
    img_size: int = 512,
    device: str = None,
) -> np.ndarray:
    """
    手写擦除主函数

    参数:
        image_path: 输入图片路径 (带手写)
        model_path: 模型权重路径
        output_path: 输出图片路径 (None 时不保存)
        img_size: 模型输入尺寸 (默认 512)
        device: 设备 ('cuda' 或 'cpu', None 自动选择)

    返回:
        clean_image: 擦除后的图片 numpy 数组 (H, W, 3), [0, 255], uint8
    """
    # 设备
    if device is None:
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    else:
        device = torch.device(device)

    print(f"推理设备: {device}")

    # 加载模型
    model = load_model(model_path, device)

    # 加载图片
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"输入图片不存在: {image_path}")
    image = Image.open(image_path).convert('RGB')
    original_size = image.size
    print(f"输入图片尺寸: {original_size}")

    # 预处理
    input_tensor = preprocess(image, img_size)
    input_tensor = input_tensor.to(device)

    # 推理
    print("正在推理...")
    with torch.no_grad():
        output_tensor = model(input_tensor)

    # 后处理
    clean_image = postprocess(output_tensor)
    print(f"输出图片尺寸: {clean_image.shape[:2]}")

    # 还原到原始尺寸
    clean_pil = Image.fromarray(clean_image)
    clean_pil = clean_pil.resize(original_size, Image.LANCZOS)
    clean_image = np.array(clean_pil)

    # 保存
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        Image.fromarray(clean_image).save(output_path, 'JPEG', quality=95)
        print(f"结果已保存: {output_path}")

    return clean_image


def batch_process(
    input_dir: str,
    model_path: str,
    output_dir: str,
    img_size: int = 512,
    device: str = None,
):
    """
    批量处理整个目录的图片

    参数:
        input_dir: 输入图片目录
        model_path: 模型权重路径
        output_dir: 输出目录
        img_size: 模型输入尺寸
        device: 设备
    """
    import glob
    from tqdm import tqdm

    os.makedirs(output_dir, exist_ok=True)
    image_exts = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff']
    image_paths = []
    for ext in image_exts:
        image_paths.extend(glob.glob(os.path.join(input_dir, ext)))  # ext 已包含 *
        image_paths.extend(glob.glob(os.path.join(input_dir, ext.upper())))

    if not image_paths:
        print(f"未找到图片: {input_dir}")
        return

    print(f"找到 {len(image_paths)} 张图片，开始批量处理...")

    for img_path in tqdm(image_paths, desc="处理中"):
        basename = os.path.basename(img_path)
        output_path = os.path.join(output_dir, basename)
        try:
            remove_handwriting(img_path, model_path, output_path, img_size, device)
        except Exception as e:
            print(f"  处理失败: {img_path} — {e}")

    print(f"批量处理完成! 结果保存在: {output_dir}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='手写擦除推理')
    parser.add_argument('--image', type=str, default=None,
                        help='输入图片路径')
    parser.add_argument('--model', type=str, default='checkpoints/handwriting_removal.pth',
                        help='模型权重路径')
    parser.add_argument('--output', type=str, default=None,
                        help='输出图片路径 (默认: 同目录添加 _clean 后缀)')
    parser.add_argument('--img_size', type=int, default=512,
                        help='模型输入尺寸')
    parser.add_argument('--device', type=str, default=None,
                        help='设备 (cuda/cpu)')
    parser.add_argument('--batch_input', type=str, default=None,
                        help='批量处理: 输入目录')
    parser.add_argument('--batch_output', type=str, default=None,
                        help='批量处理: 输出目录')

    args = parser.parse_args()

    if args.batch_input:
        # 批量模式
        output_dir = args.batch_output or (args.batch_input + '_clean')
        batch_process(args.batch_input, args.model, output_dir, args.img_size, args.device)
    elif args.image:
        # 单图模式
        if args.output is None:
            base, ext = os.path.splitext(args.image)
            args.output = f"{base}_clean{ext}"
        remove_handwriting(args.image, args.model, args.output, args.img_size, args.device)
    else:
        parser.print_help()
