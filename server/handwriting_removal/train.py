"""
训练脚本 — 手写擦除模型
使用 L1 Loss + SSIM Loss 训练 LightUNet
适配 RTX 3050 4GB VRAM (batch_size=4)

使用方式:
    # 先生成数据
    python data_generator.py --output_dir ./dataset --num_pairs 2000
    # 再训练
    python train.py --data_dir ./dataset --epochs 100 --batch_size 4
"""

import os
import sys
import argparse
import time
import glob
import numpy as np
from PIL import Image

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from torchvision.transforms import functional as F

from model import LightUNet


# ─── 数据集 ───────────────────────────────────────────────────────────────

class PairedDataset(Dataset):
    """
    配对数据集: input (有手写) / target (干净)
    目录结构:
        data_dir/
            train/
                input/    (*.jpg)
                target/   (*.jpg)
            val/
                input/    (*.jpg)
                target/   (*.jpg)
    文件名对应: input_00001.jpg ↔ target_00001.jpg
    """

    def __init__(self, input_dir, target_dir, transform=None, img_size=512):
        self.input_dir = input_dir
        self.target_dir = target_dir
        self.transform = transform
        self.img_size = img_size

        # 获取所有输入图片路径
        self.input_paths = sorted(glob.glob(os.path.join(input_dir, '*.jpg')))
        if not self.input_paths:
            self.input_paths = sorted(glob.glob(os.path.join(input_dir, '*.png')))

        # 根据文件名匹配 target
        self.target_paths = []
        for inp_path in self.input_paths:
            basename = os.path.basename(inp_path)
            # 将 input_XXXXX.jpg 替换为 target_XXXXX.jpg
            target_name = basename.replace('input_', 'target_')
            target_path = os.path.join(target_dir, target_name)
            if os.path.exists(target_path):
                self.target_paths.append(target_path)
            else:
                # 也试试对应文件名
                self.target_paths.append(target_path)

        # 过滤掉 target 不存在的项
        valid_pairs = [(i, t) for i, t in zip(self.input_paths, self.target_paths) if os.path.exists(t)]
        if len(valid_pairs) < len(self.input_paths):
            print(f"  警告: {len(self.input_paths) - len(valid_pairs)} 个配对缺少 target 文件，已过滤")

        self.input_paths = [p[0] for p in valid_pairs]
        self.target_paths = [p[1] for p in valid_pairs]

        if len(self.input_paths) == 0:
            raise RuntimeError(f"未找到任何配对数据: {input_dir} / {target_dir}")

        print(f"  加载 {len(self.input_paths)} 对数据: {input_dir}")

    def __len__(self):
        return len(self.input_paths)

    def __getitem__(self, idx):
        # 加载图片
        input_img = Image.open(self.input_paths[idx]).convert('RGB')
        target_img = Image.open(self.target_paths[idx]).convert('RGB')

        # 调整到统一尺寸
        input_img = input_img.resize((self.img_size, self.img_size), Image.LANCZOS)
        target_img = target_img.resize((self.img_size, self.img_size), Image.LANCZOS)

        # 应用 transform (数据增强)
        if self.transform:
            # 由于需要同步处理 input 和 target，自定义 transform 逻辑
            input_img, target_img = self._apply_transform(input_img, target_img)

        # 转 Tensor
        to_tensor = transforms.ToTensor()
        input_tensor = to_tensor(input_img)
        target_tensor = to_tensor(target_img)

        return input_tensor, target_tensor

    def _apply_transform(self, input_img, target_img):
        """同步数据增强: input 和 target 应用相同的变换"""

        # 随机水平翻转
        if random.random() < 0.5:
            input_img = F.hflip(input_img)
            target_img = F.hflip(target_img)

        # 随机旋转 (±2°) — 保持 small 保持内容不变
        if random.random() < 0.3:
            angle = random.uniform(-2, 2)
            input_img = F.rotate(input_img, angle, fill=255)
            target_img = F.rotate(target_img, angle, fill=255)

        # 亮度变化 (仅 input)
        if random.random() < 0.3:
            brightness = random.uniform(0.9, 1.1)
            input_img = F.adjust_brightness(input_img, brightness)

        return input_img, target_img


# ─── SSIM Loss ────────────────────────────────────────────────────────────

def gaussian_kernel(size=11, sigma=1.5):
    """生成高斯核"""
    kernel = np.zeros((size, size))
    center = size // 2
    for i in range(size):
        for j in range(size):
            kernel[i, j] = np.exp(-((i - center) ** 2 + (j - center) ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    return kernel


class SSIMLoss(nn.Module):
    """简单的 SSIM Loss，用于图像质量评估"""

    def __init__(self, channel=3, size=11, sigma=1.5):
        super().__init__()
        # 准备高斯核 (1, 1, k, k)
        kernel = gaussian_kernel(size, sigma)
        kernel = torch.FloatTensor(kernel).unsqueeze(0).unsqueeze(0)
        self.register_buffer('kernel', kernel)
        self.channel = channel
        self.size = size
        self.C1 = 0.01 ** 2
        self.C2 = 0.03 ** 2

    def forward(self, img1, img2):
        # img1, img2: [B, C, H, W], 值范围 [0, 1]

        # 扩展 kernel 到 C 个通道
        kernel = self.kernel.expand(self.channel, 1, self.size, self.size)
        padding = self.size // 2

        # 计算均值
        mu1 = nn.functional.conv2d(img1, kernel, padding=padding, groups=self.channel)
        mu2 = nn.functional.conv2d(img2, kernel, padding=padding, groups=self.channel)

        mu1_sq = mu1 ** 2
        mu2_sq = mu2 ** 2
        mu1_mu2 = mu1 * mu2

        # 计算方差
        sigma1_sq = nn.functional.conv2d(img1 * img1, kernel, padding=padding, groups=self.channel) - mu1_sq
        sigma2_sq = nn.functional.conv2d(img2 * img2, kernel, padding=padding, groups=self.channel) - mu2_sq
        sigma12 = nn.functional.conv2d(img1 * img2, kernel, padding=padding, groups=self.channel) - mu1_mu2

        # SSIM 公式
        ssim_map = ((2 * mu1_mu2 + self.C1) * (2 * sigma12 + self.C2)) / \
                   ((mu1_sq + mu2_sq + self.C1) * (sigma1_sq + sigma2_sq + self.C2))

        return 1 - ssim_map.mean()


# ─── 训练 ─────────────────────────────────────────────────────────────────

def train(args):
    """主训练函数"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"使用设备: {device}")
    if torch.cuda.is_available():
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")

    # 创建模型
    model = LightUNet().to(device)
    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"模型参数量: {total_params:,}")
    model_size_mb = total_params * 4 / (1024 * 1024)
    print(f"模型大小 (估算): {model_size_mb:.2f} MB")

    # 损失函数
    l1_loss = nn.L1Loss()
    ssim_loss = SSIMLoss(channel=3).to(device) if args.use_ssim else None

    # 优化器
    optimizer = optim.Adam(model.parameters(), lr=args.lr)

    # 学习率调度
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=10, verbose=True
    )

    # 数据加载
    print("\n加载数据集...")
    train_input_dir = os.path.join(args.data_dir, 'train', 'input')
    train_target_dir = os.path.join(args.data_dir, 'train', 'target')
    val_input_dir = os.path.join(args.data_dir, 'val', 'input')
    val_target_dir = os.path.join(args.data_dir, 'val', 'target')

    train_dataset = PairedDataset(train_input_dir, train_target_dir, img_size=args.img_size)
    val_dataset = PairedDataset(val_input_dir, val_target_dir, img_size=args.img_size)

    train_loader = DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=True,
        num_workers=args.num_workers, pin_memory=True
    )
    val_loader = DataLoader(
        val_dataset, batch_size=args.batch_size, shuffle=False,
        num_workers=args.num_workers, pin_memory=True
    )

    # 检查点目录
    checkpoint_dir = args.checkpoint_dir
    os.makedirs(checkpoint_dir, exist_ok=True)

    # 训练循环
    best_val_loss = float('inf')
    start_time = time.time()

    print(f"\n开始训练 ({args.epochs} epochs)...")
    print(f"{'='*70}")

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        train_l1 = 0.0
        train_ssim = 0.0

        epoch_start = time.time()

        for batch_idx, (input_imgs, target_imgs) in enumerate(train_loader):
            input_imgs = input_imgs.to(device)
            target_imgs = target_imgs.to(device)

            # 前向
            output_imgs = model(input_imgs)

            # 计算损失
            loss_l1 = l1_loss(output_imgs, target_imgs)

            if args.use_ssim:
                loss_ssim = ssim_loss(output_imgs, target_imgs)
                loss = loss_l1 + args.ssim_weight * loss_ssim
            else:
                loss_ssim = torch.tensor(0.0)
                loss = loss_l1

            # 反向
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            train_l1 += loss_l1.item()
            if args.use_ssim:
                train_ssim += loss_ssim.item()

            # 打印进度
            if (batch_idx + 1) % args.log_interval == 0:
                print(f"  Epoch {epoch:3d}/{args.epochs} | Batch {batch_idx+1:4d}/{len(train_loader)} | "
                      f"Loss: {loss.item():.6f} | L1: {loss_l1.item():.6f}")

        # 每个 epoch 计算平均值
        avg_train_loss = train_loss / len(train_loader)
        avg_train_l1 = train_l1 / len(train_loader)
        avg_train_ssim = train_ssim / len(train_loader) if args.use_ssim else 0

        # 验证
        model.eval()
        val_loss = 0.0
        val_l1 = 0.0

        with torch.no_grad():
            for input_imgs, target_imgs in val_loader:
                input_imgs = input_imgs.to(device)
                target_imgs = target_imgs.to(device)

                output_imgs = model(input_imgs)
                loss_l1 = l1_loss(output_imgs, target_imgs)

                if args.use_ssim:
                    loss_ssim = ssim_loss(output_imgs, target_imgs)
                    loss = loss_l1 + args.ssim_weight * loss_ssim
                else:
                    loss = loss_l1

                val_loss += loss.item()
                val_l1 += loss_l1.item()

        avg_val_loss = val_loss / len(val_loader)
        avg_val_l1 = val_l1 / len(val_loader)

        # 学习率调度
        scheduler.step(avg_val_loss)

        epoch_time = time.time() - epoch_start

        # 打印 epoch 总结
        print(f"\n{'─'*70}")
        print(f"Epoch {epoch:3d}/{args.epochs} | "
              f"时间: {epoch_time:.1f}s | "
              f"LR: {optimizer.param_groups[0]['lr']:.2e}")
        print(f"  Train | Loss: {avg_train_loss:.6f} | L1: {avg_train_l1:.6f}" +
              (f" | SSIM: {avg_train_ssim:.6f}" if args.use_ssim else ""))
        print(f"  Val   | Loss: {avg_val_loss:.6f} | L1: {avg_val_l1:.6f}")
        print(f"{'─'*70}\n")

        # 保存最佳模型
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_path = os.path.join(checkpoint_dir, 'best_model.pth')
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_loss': avg_val_loss,
                'params': total_params,
            }, best_path)
            print(f"  ✓ 保存最佳模型: {best_path} (val_loss={avg_val_loss:.6f})\n")

        # 定期保存 checkpoint
        if epoch % args.save_interval == 0:
            ckpt_path = os.path.join(checkpoint_dir, f'checkpoint_epoch_{epoch:03d}.pth')
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_loss': avg_val_loss,
                'params': total_params,
            }, ckpt_path)
            print(f"  ✓ 保存 checkpoint: {ckpt_path}\n")

    total_time = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"训练完成! 总耗时: {total_time / 60:.1f} 分钟")
    print(f"最佳验证 loss: {best_val_loss:.6f}")
    print(f"最佳模型: {os.path.join(checkpoint_dir, 'best_model.pth')}")

    # 导出为纯权重文件 (便于推理)
    final_path = os.path.join(checkpoint_dir, 'best_model.pth')
    if os.path.exists(final_path):
        # 生成一个纯净的状态字典文件
        clean_path = os.path.join(checkpoint_dir, 'handwriting_removal.pth')
        checkpoint = torch.load(final_path, map_location='cpu')
        torch.save(checkpoint['model_state_dict'], clean_path)
        print(f"导出推理用模型: {clean_path}")
        print(f"  (仅包含模型权重，约 {total_params * 4 / 1024**2:.1f} MB)")


if __name__ == '__main__':
    import random  # 用于数据增强

    parser = argparse.ArgumentParser(description='训练手写擦除模型')
    parser.add_argument('--data_dir', type=str, default='./dataset',
                        help='数据集目录')
    parser.add_argument('--checkpoint_dir', type=str, default='./checkpoints',
                        help='检查点保存目录')
    parser.add_argument('--epochs', type=int, default=100,
                        help='训练轮数')
    parser.add_argument('--batch_size', type=int, default=4,
                        help='批次大小 (适配 4GB VRAM)')
    parser.add_argument('--lr', type=float, default=1e-3,
                        help='学习率')
    parser.add_argument('--img_size', type=int, default=512,
                        help='图片尺寸')
    parser.add_argument('--use_ssim', action='store_true', default=False,
                        help='是否使用 SSIM Loss (需额外计算)')
    parser.add_argument('--ssim_weight', type=float, default=0.2,
                        help='SSIM Loss 权重')
    parser.add_argument('--num_workers', type=int, default=0,
                        help='数据加载线程数 (Windows 建议 0)')
    parser.add_argument('--log_interval', type=int, default=50,
                        help='打印间隔 (batch)')
    parser.add_argument('--save_interval', type=int, default=10,
                        help='保存 checkpoint 间隔 (epoch)')

    args = parser.parse_args()

    # 打印配置
    print("=" * 70)
    print("手写擦除模型 — 训练配置")
    print("=" * 70)
    for key, value in vars(args).items():
        print(f"  {key}: {value}")
    print("=" * 70)

    train(args)
