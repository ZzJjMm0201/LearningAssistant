"""
轻量级 U-Net 模型 — 手写擦除
使用深度可分离卷积大幅减少参数量
总参数量 < 2M，模型文件 < 10MB
输入/输出: 3通道 RGB, 512x512
"""

import torch
import torch.nn as nn


class DepthwiseSeparableConv2d(nn.Module):
    """深度可分离卷积 (Depthwise + Pointwise)"""

    def __init__(self, in_channels, out_channels, kernel_size=3, padding=1, stride=1):
        super().__init__()
        self.depthwise = nn.Conv2d(
            in_channels, in_channels,
            kernel_size=kernel_size, padding=padding, stride=stride,
            groups=in_channels, bias=False
        )
        self.pointwise = nn.Conv2d(
            in_channels, out_channels,
            kernel_size=1, bias=False
        )

    def forward(self, x):
        x = self.depthwise(x)
        x = self.pointwise(x)
        return x


class DownBlock(nn.Module):
    """下采样块: 深度可分离卷积 + BN + ReLU + MaxPool"""

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.conv1 = DepthwiseSeparableConv2d(in_channels, out_channels)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.conv2 = DepthwiseSeparableConv2d(out_channels, out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(2)

    def forward(self, x):
        x = self.relu(self.bn1(self.conv1(x)))
        x = self.relu(self.bn2(self.conv2(x)))
        p = self.pool(x)
        return x, p


class UpBlock(nn.Module):
    """上采样块: ConvTranspose + Concat + 深度可分离卷积"""

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.up = nn.ConvTranspose2d(in_channels, out_channels, kernel_size=2, stride=2)
        self.conv1 = DepthwiseSeparableConv2d(out_channels * 2, out_channels)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.conv2 = DepthwiseSeparableConv2d(out_channels, out_channels)
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x, skip):
        x = self.up(x)
        # 如果尺寸不匹配，进行插值
        if x.shape[-2:] != skip.shape[-2:]:
            x = nn.functional.interpolate(x, size=skip.shape[-2:], mode='bilinear', align_corners=False)
        x = torch.cat([x, skip], dim=1)
        x = self.relu(self.bn1(self.conv1(x)))
        x = self.relu(self.bn2(self.conv2(x)))
        return x


class LightUNet(nn.Module):
    """
    轻量级 U-Net
    架构:
      Encoder: 4 层下采样 (64 -> 128 -> 256 -> 512)
      Bottleneck: 2 层卷积 (512)
      Decoder: 4 层上采样 (512 -> 256 -> 128 -> 64)
      输出层: 3 通道 RGB，Tanh 归一化
    """

    def __init__(self, in_channels=3, out_channels=3):
        super().__init__()

        # Encoder
        self.enc1 = DownBlock(in_channels, 64)
        self.enc2 = DownBlock(64, 128)
        self.enc3 = DownBlock(128, 256)
        self.enc4 = DownBlock(256, 512)

        # Bottleneck
        self.bottleneck_conv1 = DepthwiseSeparableConv2d(512, 512)
        self.bottleneck_bn1 = nn.BatchNorm2d(512)
        self.bottleneck_conv2 = DepthwiseSeparableConv2d(512, 512)
        self.bottleneck_bn2 = nn.BatchNorm2d(512)
        self.bottleneck_relu = nn.ReLU(inplace=True)

        # Decoder
        self.dec4 = UpBlock(512, 256)
        self.dec3 = UpBlock(256, 128)
        self.dec2 = UpBlock(128, 64)
        self.dec1 = UpBlock(64, 32)

        # Output
        self.out_conv = nn.Conv2d(32, out_channels, kernel_size=1)

    def forward(self, x):
        # Encoder
        s1, p1 = self.enc1(x)
        s2, p2 = self.enc2(p1)
        s3, p3 = self.enc3(p2)
        s4, p4 = self.enc4(p3)

        # Bottleneck
        b = self.bottleneck_relu(self.bottleneck_bn1(self.bottleneck_conv1(p4)))
        b = self.bottleneck_relu(self.bottleneck_bn2(self.bottleneck_conv2(b)))

        # Decoder
        d4 = self.dec4(b, s4)
        d3 = self.dec3(d4, s3)
        d2 = self.dec2(d3, s2)
        d1 = self.dec1(d2, s1)

        # Output: Tanh 归一化到 [-1, 1]，再映射到 [0, 1]
        out = self.out_conv(d1)
        out = torch.tanh(out)  # [-1, 1]
        out = (out + 1) / 2    # [0, 1]
        return out


def count_parameters(model):
    """统计模型参数量"""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


if __name__ == '__main__':
    model = LightUNet()
    x = torch.randn(1, 3, 512, 512)
    y = model(x)
    print(f"输入尺寸: {x.shape}")
    print(f"输出尺寸: {y.shape}")
    print(f"参数量: {count_parameters(model):,}")
    # 估算模型大小 (每个参数 4 字节 float32)
    model_size_mb = count_parameters(model) * 4 / (1024 * 1024)
    print(f"模型大小 (估算): {model_size_mb:.2f} MB")
