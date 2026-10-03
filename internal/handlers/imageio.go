package handlers

import (
	"image"
	"image/jpeg"
	"image/png"
	"io"
)

// 解码器注册：image.Decode 依赖这些包在 init 时注册格式。
var _ = jpeg.Decode
var _ = png.Decode

// encodeJPEG 以 85 质量写出 JPEG。
func encodeJPEG(w io.Writer, img image.Image) error {
	return jpeg.Encode(w, img, &jpeg.Options{Quality: 85})
}