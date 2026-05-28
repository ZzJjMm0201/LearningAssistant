package com.gyyz.assistant.camera

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.renderscript.*
import android.util.Log
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.gyyz.assistant.gesture.HandGestureRecognizer
import java.nio.ByteBuffer

class CameraAnalyzer(
    private val gestureRecognizer: HandGestureRecognizer,
    private val processIntervalMs: Long = 100
) : ImageAnalysis.Analyzer {

    private var lastProcessTime = 0L
    private var frameCount = 0
    private var nullCount = 0

    override fun analyze(imageProxy: ImageProxy) {
        val currentTime = System.currentTimeMillis()
        if (currentTime - lastProcessTime < processIntervalMs) {
            imageProxy.close()
            return
        }
        lastProcessTime = currentTime
        frameCount++

        try {
            val bitmap = when (imageProxy.format) {
                ImageFormat.YUV_420_888 -> {
                    // YUV 格式（format=35）
                    yuv420ToBitmap(imageProxy)
                }
                else -> {
                    // RGBA_8888 或其他：直接读取 planes[0] 的 buffer
                    // RGBA 格式（format=1）：planes=1, pixelStride=4
                    val buffer = imageProxy.planes[0].buffer
                    val bytes = ByteArray(buffer.remaining())
                    buffer.get(bytes)

                    // 创建 Bitmap
                    val width = imageProxy.width
                    val height = imageProxy.height
                    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
                    bitmap.copyPixelsFromBuffer(ByteBuffer.wrap(bytes))
                    bitmap
                }
            }

            if (bitmap != null) {
                if (frameCount % 30 == 0) {
                    Log.d("CameraAnalyzer", "Frame #$frameCount: bitmap=${bitmap.width}x${bitmap.height}, format=${imageProxy.format}")
                }
                val mpImage = BitmapImageBuilder(bitmap).build()
                val timestampMicros = System.nanoTime() / 1000
                gestureRecognizer.processFrame(mpImage, timestampMicros)
                bitmap.recycle()
            }
        } catch (e: Exception) {
            Log.e("CameraAnalyzer", "Frame #$frameCount 处理失败: ${e.message}", e)
        } finally {
            imageProxy.close()
        }
    }

    /**
     * YUV_420_888 → Bitmap（更稳健的方式）
     */
    private fun yuv420ToBitmap(image: ImageProxy): Bitmap? {
        try {
            val planes = image.planes
            if (planes.size < 3) return null

            val yPlane = planes[0]
            val uPlane = planes[1]
            val vPlane = planes[2]

            val width = image.width
            val height = image.height

            // 检查数据是否有效
            if (yPlane.buffer.remaining() < width * height) {
                Log.w("CameraAnalyzer", "Y plane too small: ${yPlane.buffer.remaining()} < ${width * height}")
                return null
            }

            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)

            val yBuffer = yPlane.buffer
            val uBuffer = uPlane.buffer
            val vBuffer = vPlane.buffer

            // 重置位置
            yBuffer.rewind()
            uBuffer.rewind()
            vBuffer.rewind()

            val yRowStride = yPlane.rowStride
            val yPixelStride = yPlane.pixelStride
            val uvRowStride = uPlane.rowStride
            val uvPixelStride = uPlane.pixelStride

            for (y in 0 until height) {
                for (x in 0 until width) {
                    // 读取 Y
                    val yIndex = y * yRowStride + x * yPixelStride
                    val Y = (yBuffer.get(yIndex).toInt() and 0xFF)

                    // 读取 U 和 V（4:2:0 采样，每4个Y共享1个UV）
                    val uvIndex = (y / 2) * uvRowStride + (x / 2) * uvPixelStride

                    // 检查索引是否越界
                    val U = if (uvIndex < uBuffer.remaining()) (uBuffer.get(uvIndex).toInt() and 0xFF) else 128
                    val V = if (uvIndex < vBuffer.remaining()) (vBuffer.get(uvIndex).toInt() and 0xFF) else 128

                    // YUV 转 RGB
                    val y1 = Y - 16
                    val u1 = U - 128
                    val v1 = V - 128

                    var r = (298 * y1 + 409 * v1 + 128) shr 8
                    var g = (298 * y1 - 100 * u1 - 208 * v1 + 128) shr 8
                    var b = (298 * y1 + 516 * u1 + 128) shr 8

                    r = r.coerceIn(0, 255)
                    g = g.coerceIn(0, 255)
                    b = b.coerceIn(0, 255)

                    bitmap.setPixel(x, y, (255 shl 24) or (r shl 16) or (g shl 8) or b)
                }
            }

            return bitmap
        } catch (e: Exception) {
            Log.e("CameraAnalyzer", "YUV转换失败: ${e.message}", e)
            return null
        }
    }

    private fun jpegToBitmap(image: ImageProxy): Bitmap? {
        val buffer = image.planes[0].buffer
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
    }
}