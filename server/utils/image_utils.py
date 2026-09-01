"""
图片工具：上传图片压缩
将上传图片的最长边限制在指定像素以内，便于后续OCR识别与存储。
"""
import io
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE_PIXELS = 1000  # 最长边限制（像素）
JPEG_QUALITY = 90  # JPEG 保存质量


def save_uploaded_image(image_bytes: bytes, dest_path: Path, max_side: int = MAX_SIDE_PIXELS) -> None:
    """保存上传图片：最长边超过 max_side 时等比压缩，并应用EXIF方向。

    PIL 解析失败时回退为直接写原始字节，保证上传流程不受影响。
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)  # 纠正手机拍照方向

        width, height = img.size
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / longest
            new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)
            print(f"[图片] 压缩: {width}x{height} -> {new_size[0]}x{new_size[1]}")

        # 统一转为RGB保存JPEG；带透明通道的先铺白底，避免透明区域变黑
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(dest_path, format="JPEG", quality=JPEG_QUALITY)
    except Exception as e:
        print(f"[图片] 压缩失败({e})，按原始字节保存: {dest_path}")
        with open(dest_path, "wb") as f:
            f.write(image_bytes)
