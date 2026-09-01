package com.gyyz.assistant.gesture

import android.content.Context
import android.util.Log
import com.google.mediapipe.framework.image.MPImage
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import kotlin.math.abs
import kotlin.math.sqrt

class HandGestureRecognizer(
    context: Context,
    private val onGestureDetected: (fingerCount: Int) -> Unit,
    private val onFingerCountChanged: ((fingerCount: Int) -> Unit)? = null,
    private val onError: (Exception) -> Unit = {}
) {
    companion object {
        private const val TAG = "HandGesture"
        private const val GESTURE_HOLD_THRESHOLD_MS = 1200L  // ⑦ 保持1.2秒才触发，避免过快误触
        private const val SMOOTHING_WINDOW_SIZE = 8         // 滑动窗口大小
        private const val MIN_STABLE_COUNT = 6               // 窗口内至少出现次数才算稳定（提高准确率）
        private const val MAX_NO_HAND_FRAMES = 10            // 容忍短暂丢失
    }

    private var handLandmarker: HandLandmarker? = null

    // 手势保持检测
    private var stableFingerCount: Int = 0
    private var gestureHoldStart: Long = 0L

    // ===== 滑动窗口平滑 =====
    private val fingerHistory = IntArray(SMOOTHING_WINDOW_SIZE) { 0 }
    private var historyIndex = 0
    private var historyFilled = false

    // 调试计数
    private var frameCount = 0
    private var noHandFrameCount = 0
    private var lastLogTime = 0L

    init {
        setupHandLandmarker(context)
    }

    private fun setupHandLandmarker(context: Context) {
        try {
            context.assets.open("hand_landmarker.task").close()
            Log.d(TAG, "Model asset found: hand_landmarker.task")

            val baseOptions = BaseOptions.builder()
                .setModelAssetPath("hand_landmarker.task")
                .build()

            val options = HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(baseOptions)
                .setRunningMode(RunningMode.LIVE_STREAM)
                .setNumHands(1)
                .setMinHandDetectionConfidence(0.3f)
                .setMinTrackingConfidence(0.3f)
                .setResultListener { result, _ ->
                    onHandLandmarkerResult(result)
                }
                .setErrorListener { error ->
                    Log.e(TAG, "MediaPipe error: ${error.message}")
                    onError(Exception(error.message))
                }
                .build()

            handLandmarker = HandLandmarker.createFromOptions(context, options)
            Log.d(TAG, "HandLandmarker initialized successfully")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize HandLandmarker: ${e.message}", e)
            onError(e)
        }
    }

    fun processFrame(mpImage: MPImage, timestampMicros: Long) {
        try {
            handLandmarker?.detectAsync(mpImage, timestampMicros)
        } catch (e: Exception) {
            Log.e(TAG, "processFrame error: ${e.message}", e)
        }
    }

    /**
     * 从滑动窗口中获取稳定的手指计数
     * 返回窗口中频率最高的值，且必须达到 MIN_STABLE_COUNT 次
     */
    private fun getSmoothedFingerCount(rawCount: Int): Int {
        // 记录到历史
        fingerHistory[historyIndex] = rawCount
        historyIndex = (historyIndex + 1) % SMOOTHING_WINDOW_SIZE
        if (historyIndex == 0) historyFilled = true

        val windowSize = if (historyFilled) SMOOTHING_WINDOW_SIZE else historyIndex
        if (windowSize < 3) return rawCount  // 数据不足，直接返回

        // 统计窗口内每个值的出现次数
        val countMap = mutableMapOf<Int, Int>()
        for (i in 0 until windowSize) {
            countMap[fingerHistory[i]] = (countMap[fingerHistory[i]] ?: 0) + 1
        }

        // 找到出现次数最多的值
        val best = countMap.maxByOrNull { it.value } ?: return rawCount

        // 阈值：窗口的多数 (MIN_STABLE_COUNT)
        // 如果是全窗口，需要超过一半
        val threshold = minOf(MIN_STABLE_COUNT, windowSize / 2 + 1)
        return if (best.value >= threshold) best.key else rawCount
    }

    private fun onHandLandmarkerResult(result: HandLandmarkerResult) {
        frameCount++
        val now = System.currentTimeMillis()

        if (result.landmarks().isEmpty()) {
            noHandFrameCount++
            if (noHandFrameCount > MAX_NO_HAND_FRAMES) {
                // 确实没有手了
                if (stableFingerCount != 0) {
                    Log.d(TAG, "Hand lost after $MAX_NO_HAND_FRAMES frames")
                    stableFingerCount = 0
                    gestureHoldStart = 0L
                    historyFilled = false
                    historyIndex = 0
                    onFingerCountChanged?.invoke(0)
                }
            }
            return
        }

        // 重置无手计数器
        noHandFrameCount = 0

        val landmarks = result.landmarks().first()
        val rawCount = countExtendedFingers(landmarks)

        // ===== 滑动窗口平滑 =====
        val smoothedCount = getSmoothedFingerCount(rawCount)

        // 每2秒输出一次调试信息
        if (now - lastLogTime > 2000) {
            lastLogTime = now
            Log.d(TAG, "Frame #$frameCount: raw=$rawCount smoothed=$smoothedCount stable=$stableFingerCount")
            logHandGeometry(landmarks)
        }

        // ===== 实时通知手指数量变化（使用平滑后的值）=====
        if (smoothedCount != stableFingerCount) {
            onFingerCountChanged?.invoke(smoothedCount)
        }

        // ===== 手势保持检测 =====
        if (smoothedCount == stableFingerCount && smoothedCount > 0) {
            if (gestureHoldStart > 0L) {
                val heldMs = now - gestureHoldStart
                if (heldMs >= GESTURE_HOLD_THRESHOLD_MS) {
                    Log.d(TAG, "Gesture triggered: $smoothedCount fingers (held for ${heldMs}ms)")
                    onGestureDetected(smoothedCount)
                    gestureHoldStart = 0L  // 触发后重置，防止重复触发
                }
            } else {
                gestureHoldStart = now
                Log.d(TAG, "Started holding $smoothedCount fingers...")
            }
        } else {
            // 手指数量变化了
            if (smoothedCount > 0) {
                gestureHoldStart = now  // 从新计数开始计时
            } else {
                gestureHoldStart = 0L
            }
            stableFingerCount = smoothedCount
        }
    }

    private fun logHandGeometry(landmarks: List<com.google.mediapipe.tasks.components.containers.NormalizedLandmark>) {
        if (landmarks.size < 21) return

        val wrist = landmarks[0]
        val middleMcp = landmarks[9]
        val upLen = distance2D(wrist, middleMcp)

        val tips = listOf(4, 8, 12, 16, 20)
        val mcps = listOf(2, 5, 9, 13, 17)
        val names = listOf("Thumb", "Index", "Middle", "Ring", "Pinky")

        for (i in tips.indices) {
            val tip = landmarks[tips[i]]
            val mcp = landmarks[mcps[i]]
            val dist = distance2D(tip, mcp)
            val dx = tip.x() - mcp.x()
            val dy = tip.y() - mcp.y()

            val along = if (upLen > 0.01f) {
                (dx * (middleMcp.x() - wrist.x()) + dy * (middleMcp.y() - wrist.y())) / upLen
            } else 0f

            Log.d(TAG, "  ${names[i]}: dist=%.4f, along=%.4f".format(dist, along))
        }
    }

    /**
     * 判断手指是否伸展开来
     * 返回伸展开的手指数量（0-5）
     */
    private fun countExtendedFingers(
        landmarks: List<com.google.mediapipe.tasks.components.containers.NormalizedLandmark>
    ): Int {
        if (landmarks.size < 21) return 0

        // 关键参考点
        val wrist = landmarks[0]
        val middleMCP = landmarks[9]
        val middlePIP = landmarks[10]
        val middleTip = landmarks[12]

        // 计算手部方向（从手腕到中指根）
        val handUpX = middleMCP.x() - wrist.x()
        val handUpY = middleMCP.y() - wrist.y()
        val handUpLength = sqrt(handUpX * handUpX + handUpY * handUpY)
        if (handUpLength < 0.01f) return 0

        // 归一化方向向量
        val upX = handUpX / handUpLength
        val upY = handUpY / handUpLength

        // 右手/左手判断：右手时，拇指在左侧（x 负方向）
        // 手掌方向向量（从手腕到中指，z轴正方向指向观察者）
        val isRightHand = landmarks[17].x() < landmarks[5].x()  // pinky MCP < index MCP

        var count = 0

        for (fingerIndex in 0..4) {
            val tipIdx = listOf(4, 8, 12, 16, 20)[fingerIndex]
            val mcpIdx = listOf(2, 5, 9, 13, 17)[fingerIndex]
            val pipIdx = listOf(3, 6, 10, 14, 18)[fingerIndex]

            val tip = landmarks[tipIdx]
            val mcp = landmarks[mcpIdx]
            val pip = landmarks[pipIdx]

            // 指尖到指根（MCP）的向量
            val dx = tip.x() - mcp.x()
            val dy = tip.y() - mcp.y()

            // 沿手方向的投影（指尖在"朝上"方向上的偏移）
            val projectionAlong = dx * upX + dy * upY

            // 指尖到指根的距离
            val distance = sqrt(dx * dx + dy * dy)

            // PIP到MCP的距离（近端指节长度）
            val pipDist = distance2D(pip, mcp)

            if (fingerIndex == 0) {
                // ===== 拇指：使用多维度判断 =====

                // 方法1: 指尖到手腕距离 vs 指根到手腕距离的比值
                val tipToWrist = distance2D(tip, wrist)
                val mcpToWrist = distance2D(mcp, wrist)
                val ratio = tipToWrist / (mcpToWrist + 0.01f)

                // 方法2: 拇指指尖相对于 MCP 在手掌平面外侧
                // 右手：拇指在外侧（x 减小的方向）；左手：拇指在外侧（x 增加的方向）
                val outwardDir = if (isRightHand) -1f else 1f
                val thumbOutwardness = (tip.x() - mcp.x()) * outwardDir

                // 方法3: 拇指弯曲检测 - PIP到MCP的距离 vs 指尖到PIP的距离
                val tipToPip = distance2D(tip, pip)
                // 如果拇指弯曲，tip-to-pip 会明显小于 PIP-to-MCP 的某个比例
                val isThumbBent = tipToPip < pipDist * 0.7f

                // 综合判断
                val isExtended = if (isThumbBent) {
                    // 弯曲的拇指不算伸开
                    false
                } else {
                    // 伸直条件: 要么 tip-to-wrist 比值大，要么向外展开明显
                    ratio > 1.15f || (distance > 0.08f && thumbOutwardness > 0.02f)
                }

                if (isExtended) count++
            } else {
                // ===== 四指：基于投影 + 弯曲检测 =====

                // 弯曲检测：指尖到PIP的距离 与 PIP到MCP的距离做比较
                // 如果指尖到PIP < PIP到MCP * 0.6，说明明显弯曲
                val tipToPip = distance2D(tip, pip)
                val isBent = tipToPip < pipDist * 0.6f

                if (isBent) continue  // 弯曲的手指不算伸开

                // 对于其他四指，伸直时指尖应明显位于指根沿手方向的前方
                // 阈值：projectionAlong > 0.03 且距离 > 0.05（比之前略宽松）
                val isExtended = projectionAlong > 0.03f && distance > 0.05f

                if (isExtended) count++
            }
        }

        return count
    }

    // 二维点距离
    private fun distance2D(
        a: com.google.mediapipe.tasks.components.containers.NormalizedLandmark,
        b: com.google.mediapipe.tasks.components.containers.NormalizedLandmark
    ): Float {
        val dx = a.x() - b.x()
        val dy = a.y() - b.y()
        return sqrt(dx * dx + dy * dy)
    }

    fun close() {
        handLandmarker?.close()
        Log.d(TAG, "HandLandmarker closed")
    }
}
