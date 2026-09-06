@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.gyyz.assistant
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.core.ImageAnalysis
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*

import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.gyyz.assistant.camera.CameraAnalyzer
import com.gyyz.assistant.gesture.HandGestureRecognizer
import com.gyyz.assistant.network.ApiService
import com.gyyz.assistant.network.ServerDiscoverer
import io.noties.markwon.Markwon
import io.noties.markwon.ext.latex.JLatexMathPlugin
import io.noties.markwon.ext.strikethrough.StrikethroughPlugin
import io.noties.markwon.image.ImagesPlugin
import io.noties.markwon.image.glide.GlideImagesPlugin
import io.noties.markwon.ext.tables.TablePlugin
import io.noties.markwon.inlineparser.MarkwonInlineParserPlugin
import java.io.BufferedReader
import java.io.InputStreamReader
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.*
import org.json.JSONArray
import org.json.JSONObject

@Suppress("OPT_IN_IS_NOT_ENABLED")
@OptIn(ExperimentalMaterial3Api::class)


sealed class AppState {
    data class Login(val isLoading: Boolean = false, val error: String = "") : AppState()

    object Tracking : AppState()

    data class Solving(
            val stage: SolveStage = SolveStage.UPLOADING,
            val requestId: String = "",
            val questionInfo: String = "",
            val subject: String = "",
            val solutionSteps: String = "",
            val fullSolution: String = "",
            val mindMap: String = "",
            val suggestedQuestions: List<String> = emptyList(),
            val suggestedQA: List<QAItem> = emptyList(),
            val qaList: List<QAItem> = emptyList(),
            val searchResults: List<Map<String, String>> = emptyList(),
            val ocrText: String = "",
            val pendingAnswer: String = "",
            val isAnswering: Boolean = false,
            val stepsStreaming: Boolean = false,
            val solutionStreaming: Boolean = false,
            val mindmapStreaming: Boolean = false,
    ) : AppState()

    data class Report(val reportText: String = "", val isLoading: Boolean = true, val streaming: Boolean = false) : AppState()

    object History : AppState()

    object Animation : AppState()

    data class Knowledge(
            val summary: String = "",
            val extension: String = "",
            val similarQuestions: List<QAItem> = emptyList(),
            val suggestedQuestions: List<QAItem> = emptyList(),
            val summaryStreaming: Boolean = false,
            val extensionStreaming: Boolean = false
    ) : AppState()
}

/**
 * 问答对：问题 + 答案（AI预判问题自带答案；用户追问后追加）
 */
data class QAItem(val question: String, val answer: String = "", val rendered: Boolean = false)

/** 解析服务端下发的 [{question,answer}] 或 ["q1","q2"] 为 QAItem 列表 */
private fun parseQAList(content: Any?): List<QAItem> {
    if (content == null) return emptyList()
    val arr = content as? JSONArray ?: return emptyList()
    val out = mutableListOf<QAItem>()
    for (i in 0 until arr.length()) {
        val obj = arr.optJSONObject(i)
        if (obj != null) {
            val q = obj.optString("question", "")
            val a = obj.optString("answer", "")
            if (q.isNotEmpty()) out.add(QAItem(q, a))
        } else {
            val s = arr.optString(i, "")
            if (s.isNotEmpty()) out.add(QAItem(s, ""))
        }
    }
    return out
}

enum class SolveStage {
    UPLOADING,
    ANALYZING,
    DISPLAY_STEPS,
    DISPLAY_FULL,
    DISPLAY_MINDMAP,
    INTERACTIVE,
    COMPLETED
}

enum class FocusState(val label: String) {
    WRITING("书写中"),
    THINKING("思考中"),
    PAGE_TURNING("翻页中"),
    SEEKING_HELP("求助中");

    companion object {
        fun fromString(s: String): FocusState {
            return when (s.lowercase()) {
                "writing" -> WRITING
                "thinking" -> THINKING
                "page_turning" -> PAGE_TURNING
                "seeking_help" -> SEEKING_HELP
                else -> WRITING
            }
        }
    }
}

// ==================== ViewModel ====================

class MainViewModel : ViewModel() {
    private val _appState = MutableStateFlow<AppState>(AppState.Tracking)
    val appState: StateFlow<AppState> = _appState.asStateFlow()

    private val _statusText = MutableStateFlow("跟踪学习中...")
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    private val _pomodoroTime = MutableStateFlow(25 * 60)
    val pomodoroTime: StateFlow<Int> = _pomodoroTime.asStateFlow()

    private val _pageCount = MutableStateFlow(0)
    val pageCount: StateFlow<Int> = _pageCount.asStateFlow()

    private val _thinkingTimer = MutableStateFlow(0)
    val thinkingTimer: StateFlow<Int> = _thinkingTimer.asStateFlow()

    private var thinkingJob: Job? = null
    private var pomodoroJob: Job? = null
    private val _animationUrl = MutableStateFlow("")
    val animationUrl: StateFlow<String> = _animationUrl.asStateFlow()

    val apiService = ApiService()

    private val _isLoggedIn = MutableStateFlow(false)
    val isLoggedIn: StateFlow<Boolean> = _isLoggedIn.asStateFlow()

    private val _loggedInUsername = MutableStateFlow("")
    val loggedInUsername: StateFlow<String> = _loggedInUsername.asStateFlow()

    // Feature 5: SSE取消标志
    var cancelCurrentSSE = false
    
    // Feature 4: 掌握程度
    val showMasteryDialog = MutableStateFlow(false)
    // 掌握程度按钮可见性（解答完成后显示在模块下方，不再自动弹窗）
    val masteryVisible = MutableStateFlow(false)
    val masterySaved = MutableStateFlow(false)
    val masteryLevel = MutableStateFlow("")
    // 解题阶段进度（右上角显示，如“阶段 3/6 · 完整解析”）
    val solveProgress = MutableStateFlow("")
    // GeoGebra生成中（需要锁屏等待AI回复）
    val isGeneratingGeoGebra = MutableStateFlow(false)
    // GeoGebra：生成该题的交互式数学图形
    val geoGebraUrl = MutableStateFlow("")
    private var lastPhotoFile: java.io.File? = null
    val currentSolvingRequestId = MutableStateFlow("")

    // Feature 9/10: OCR/手势确认弹窗
    val showOcrConfirmDialog = MutableStateFlow(false)
    val ocrConfirmText = MutableStateFlow("")
    val ocrConfirmTitle = MutableStateFlow("确认识别结果")

    // 待确认的解题 request_id（用于把确认结果回传给服务端，避免30秒超时等待）
    private val _pendingOcrRequestId = MutableStateFlow("")
    val pendingOcrRequestId: StateFlow<String> = _pendingOcrRequestId.asStateFlow()

    // Feature 3: 提问loading状态
    val isAskingQuestion = MutableStateFlow(false)

    // Feature 11/12: 番茄钟休息
    val isResting = MutableStateFlow(false)
    val restTimeRemaining = MutableStateFlow(0)
    val pomodoroWorkDuration = MutableStateFlow(25)
    val pomodoroRestDuration = MutableStateFlow(5)

    // 番茄钟增强：正计时模式 + 暂停/继续 + 设置弹窗
    val pomodoroMode = MutableStateFlow(false)   // false=倒计时, true=正计时
    val elapsedTime = MutableStateFlow(0)        // 正计时经过秒数
    val pomodoroRunning = MutableStateFlow(true) // 暂停/继续
    val showPomodoroSettings = MutableStateFlow(false)

    // Feature 20: GeoGebra
    val showGeoGebraScreen = MutableStateFlow(false)
    val geogebraUrl = MutableStateFlow("")

    init {
        viewModelScope.launch {
            delay(100)

            tomatoEnabled.collect { enabled ->
                if (enabled) {
                    startTomato()
                } else {
                    pomodoroJob?.cancel()
                }
            }
        }
    }

    fun initApiService(context: android.content.Context) {
        apiService.init(context)
        showWelcomeDialog.value = !apiService.isWelcomeDismissed()
        // 同步AI模型设置（从持久化读取）
        llmProvider.value = apiService.llmProvider
        llmModel.value = apiService.llmModel
        ocrMode.value = apiService.ocrMode
        visionModel.value = apiService.visionModel
        // ②④⑦十一 扩展设置同步
        answerStyle.value = apiService.answerStyle
        searchEnabled.value = apiService.searchEnabled
        thinkingEnabled.value = apiService.thinkingEnabled
        themeMode.value = apiService.themeMode
        dialect.value = apiService.dialect
        grade.value = apiService.grade
        if (apiService.isLoggedIn()) {
            _isLoggedIn.value = true
            _loggedInUsername.value = apiService.getUsername() ?: ""
            _appState.value = AppState.Tracking
            _statusText.value = "欢迎回来，${_loggedInUsername.value}"
        } else {
            _appState.value = AppState.Login()
        }
    }

    fun register(username: String, password: String) {
        _appState.value = AppState.Login(isLoading = true)
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val result = apiService.register(username, password)
                withContext(Dispatchers.Main) {
                    _isLoggedIn.value = true
                    _loggedInUsername.value = result.user.username
                    _appState.value = AppState.Tracking
                    _statusText.value = "注册成功，欢迎 ${result.user.username}"
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Login(error = e.message ?: "注册失败")
                }
            }
        }
    }

    fun login(username: String, password: String) {
        _appState.value = AppState.Login(isLoading = true)
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val result = apiService.login(username, password)
                withContext(Dispatchers.Main) {
                    _isLoggedIn.value = true
                    _loggedInUsername.value = result.user.username
                    _appState.value = AppState.Tracking
                    _statusText.value = "登录成功，欢迎 ${result.user.username}"
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Login(error = e.message ?: "登录失败")
                }
            }
        }
    }

    fun logout() {
        apiService.logout()
        _isLoggedIn.value = false
        _loggedInUsername.value = ""
        _appState.value = AppState.Login()
        _statusText.value = "已退出登录"
    }

    private fun startTomato() {
        pomodoroJob?.cancel()
        pomodoroJob = viewModelScope.launch {
            while (isActive && tomatoEnabled.value) {
                delay(1000)
                if (!pomodoroRunning.value) continue
                if (pomodoroMode.value) {
                    // 正计时模式：从0向上计时
                    elapsedTime.value += 1
                } else {
                    // 倒计时模式
                    if (_pomodoroTime.value > 0) {
                        _pomodoroTime.value -= 1
                    } else {
                        // Feature 11: 番茄钟完成 → 全屏休息
                        _statusText.value = "完成一个番茄钟！休息一下吧~"
                        isResting.value = true
                        restTimeRemaining.value = pomodoroRestDuration.value * 60
                        // 关闭手势、语音、摄像头（通过Tracking状态控制）
                        _appState.value = AppState.Tracking
                        // 休息倒计时
                        while (isActive && restTimeRemaining.value > 0) {
                            delay(1000)
                            restTimeRemaining.value -= 1
                        }
                        isResting.value = false
                        _statusText.value = "休息结束，继续学习！"
                        _pomodoroTime.value = pomodoroWorkDuration.value * 60
                    }
                }
            }
        }
    }

    /**
     * 修改工作时长（设置面板/番茄钟弹窗调用）：立即生效并重置当前倒计时
     */
    fun applyPomodoroWorkDuration(minutes: Int) {
        val m = minutes.coerceIn(1, 180)
        pomodoroWorkDuration.value = m
        if (!pomodoroMode.value && !isResting.value) {
            _pomodoroTime.value = m * 60
        }
    }

    /**
     * 修改休息时长
     */
    fun applyPomodoroRestDuration(minutes: Int) {
        pomodoroRestDuration.value = minutes.coerceIn(1, 60)
    }

    /**
     * 切换计时模式（倒计时 ↔ 正计时）
     */
    fun setPomodoroMode(stopwatch: Boolean) {
        pomodoroMode.value = stopwatch
        elapsedTime.value = 0
        _pomodoroTime.value = pomodoroWorkDuration.value * 60
        pomodoroRunning.value = true
    }

    /**
     * 应用番茄钟设置（模式 + 时长）
     */
    fun applyPomodoroSettings(workMin: Int, restMin: Int, stopwatch: Boolean) {
        pomodoroWorkDuration.value = workMin.coerceIn(1, 180)
        pomodoroRestDuration.value = restMin.coerceIn(1, 60)
        pomodoroMode.value = stopwatch
        elapsedTime.value = 0
        _pomodoroTime.value = pomodoroWorkDuration.value * 60
        pomodoroRunning.value = true
        isResting.value = false
    }

    /**
     * 暂停/继续计时
     */
    fun togglePomodoro() {
        pomodoroRunning.value = !pomodoroRunning.value
    }

    /**
     * 重置计时
     */
    fun resetPomodoro() {
        elapsedTime.value = 0
        _pomodoroTime.value = pomodoroWorkDuration.value * 60
        pomodoroRunning.value = true
    }

    private fun startTracking() {
        _appState.value = AppState.Tracking

        if (tomatoEnabled.value) {
            _pomodoroTime.value = pomodoroWorkDuration.value * 60
            startTomato()
        }
    }

    fun onFocusStateChanged(focusState: FocusState) {
        when (focusState) {
            FocusState.THINKING -> {
                thinkingJob?.cancel()
                thinkingJob =
                        viewModelScope.launch {
                            _thinkingTimer.value = 0
                            while (_thinkingTimer.value < 30) {
                                delay(1000)
                                _thinkingTimer.value += 1
                            }
                            _statusText.value = "需要帮助吗？正在准备解题..."
                            startSolving()
                            thinkingJob?.cancel()
                        }
            }
            FocusState.PAGE_TURNING -> {
                _pageCount.value += 1
                thinkingJob?.cancel()
                _thinkingTimer.value = 0
            }
            FocusState.WRITING -> {
                thinkingJob?.cancel()
                _thinkingTimer.value = 0
            }
            FocusState.SEEKING_HELP -> {
                startSolving()
            }
        }
    }

    fun onGestureDetected(fingerCount: Int) {
        when (fingerCount) {
            1 -> {
                _statusText.value = "更多功能..."
            }
            2 -> {
                // Feature 10: 手势确认弹窗
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "将生成AI学情报告"
                ocrConfirmTitle.value = "手势识别确认 - AI报告(2指)"
                _appState.value = AppState.Report(isLoading = true)
                loadAiReport()
            }
            3 -> {
                // Feature 10: 手势确认弹窗
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "将生成数据学情报告"
                ocrConfirmTitle.value = "手势识别确认 - 数据报告(3指)"
                _appState.value = AppState.Report(isLoading = true)
                loadDataReport()
            }
            4 -> {
                // Feature 10: 手势确认弹窗
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "将触发生成AI动画"
                ocrConfirmTitle.value = "手势识别确认 - AI动画(4指)"
                _appState.value = AppState.Animation
            }
        }
    }

    fun handleVoiceCommand(text: String) {
        when {
            text.contains("不会") || text.contains("拍照") || text.contains("解题") -> {
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "识别到语音指令：$text"
                ocrConfirmTitle.value = "语音识别确认"
                onButtonSolve()
            }
            text.contains("抽象") || text.contains("动画") -> {
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "识别到语音指令：$text"
                ocrConfirmTitle.value = "语音识别确认"
                onButtonAnimation()
            }
            text.contains("做了多少") || text.contains("数据") -> {
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "识别到语音指令：$text"
                ocrConfirmTitle.value = "语音识别确认"
                loadDataReport()
            }
            text.contains("掌握") || text.contains("了解") -> {
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "识别到语音指令：$text"
                ocrConfirmTitle.value = "语音识别确认"
                loadAiReport()
            }
            text.contains("还想了解") || text.contains("延伸") -> {
                showOcrConfirmDialog.value = true
                ocrConfirmText.value = "识别到语音指令：$text"
                ocrConfirmTitle.value = "语音识别确认"
                onButtonExtend()
            }
            else -> _statusText.value = "未识别到命令: $text"
        }
    }

    fun saveAiSettings() {
        apiService.saveAiSettings(llmProvider.value, llmModel.value, ocrMode.value, visionModel.value)
    }

    fun startSolving() {
        _appState.value = AppState.Solving(stage = SolveStage.UPLOADING)
        _statusText.value = "正在拍照..."
    }

    fun confirmOcr() {
        // 若当前弹窗是解题流程的OCR确认，则通知服务端继续，避免白白等待30秒
        val rid = _pendingOcrRequestId.value
        _pendingOcrRequestId.value = ""
        if (rid.isNotEmpty()) {
            viewModelScope.launch(Dispatchers.IO) {
                try {
                    apiService.confirmSolve(rid)
                } catch (e: Exception) {
                    Log.w("MainViewModel", "OCR确认回传失败: ${e.message}")
                }
            }
        }
    }

    fun onPhotoReady(photoFile: java.io.File) {
        Log.d("MainViewModel", "照片就绪，开始上传: ${photoFile.absolutePath}")
        cancelCurrentSSE = false  // Feature 5: 重置取消标志
        masteryVisible.value = false  // 新一次解题开始时隐藏掌握程度按钮
        masterySaved.value = false
        masteryLevel.value = ""
        solveSearchResults.value = emptyList()
        solveQuestionInfo.value = emptyMap()
        solveThinkingText.value = ""
        solvingThinkingVisible.value = false
        // ① 重置多题状态
        multiQuestionCount.value = 0
        multiQuestionTexts.value = emptyList()
        currentQuestionIndex.value = 0
        multiSolveStates.value = emptyList()
        lastPhotoFile = photoFile

        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Solving(stage = SolveStage.UPLOADING)
                    _statusText.value = "正在上传题目..."
                }

                val photoBytes = photoFile.readBytes()
                Log.d("MainViewModel", "调用 apiService.startSolve()")
                val requestId = apiService.startSolve(photoBytes)
                Log.d("MainViewModel", "上传成功，requestId=$requestId")
                currentSolvingRequestId.value = requestId

                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Solving(stage = SolveStage.ANALYZING)
                    _statusText.value = "正在分析题目..."
                }

                Log.d("MainViewModel", "开始连接SSE流...")
                connectAndReceiveSSE(requestId)
            } catch (e: Exception) {
                Log.e("MainViewModel", "解题失败: ${e.message}", e)
                withContext(Dispatchers.Main) {
                    _statusText.value = "解题失败: ${e.message}"
                    _appState.value = AppState.Tracking
                }
            }
        }
    }

    fun onPhotoError(error: String) {
        _statusText.value = "拍照失败: $error"
        _appState.value = AppState.Tracking
    }

    private suspend fun connectAndReceiveSSE(requestId: String) {
        val client =
                OkHttpClient.Builder()
                        .connectTimeout(30, TimeUnit.SECONDS)
                        .readTimeout(0, TimeUnit.MILLISECONDS)
                        .build()

        val request =
                Request.Builder()
                        .url("${apiService.getBaseUrl()}/solve/stream/$requestId")
                        .header("Accept", "text/event-stream")
                        .build()

        Log.d("MainViewModel", "SSE请求URL: ${request.url}")

        try {
            val response = withContext(Dispatchers.IO) { client.newCall(request).execute() }

            if (!response.isSuccessful) {
                Log.e("MainViewModel", "SSE连接失败: ${response.code}")
                withContext(Dispatchers.Main) { _statusText.value = "连接失败: ${response.code}" }
                return
            }

            val reader = BufferedReader(InputStreamReader(response.body?.byteStream()))
            var eventType = ""
            var dataBuffer = StringBuilder()

            // ① 多题：每题独立累积（按 qi 键控）
            val stepsMap = mutableMapOf<Int, String>()
            val fullMap = mutableMapOf<Int, String>()
            val mindMapMap = mutableMapOf<Int, String>()
            val suggQAMap = mutableMapOf<Int, List<QAItem>>()
            val suggMap = mutableMapOf<Int, List<String>>()
            val qaMap = mutableMapOf<Int, List<QAItem>>()
            val subjectMap = mutableMapOf<Int, String>()
            val ocrMap = mutableMapOf<Int, String>()
            // 流式显示节流：每100ms最多刷新一次UI
            var lastSolutionUpdate = 0L
            var lastStepsUpdate = 0L
            var lastMindmapUpdate = 0L

            fun cur(key: Int): Int = if (key >= 0) key else 0

            // 统一写入：多题→列表并刷新当前页；单题→_appState
            fun emit(qiKey: Int, s: AppState.Solving) {
                if (multiQuestionCount.value > 1 && qiKey >= 0) {
                    val list = multiSolveStates.value.toMutableList()
                    while (list.size <= qiKey) list.add(AppState.Solving(stage = SolveStage.ANALYZING, requestId = requestId, ocrText = ocrMap[qiKey] ?: ""))
                    list[qiKey] = s
                    multiSolveStates.value = list
                    currentQuestionIndex.value = qiKey
                    _appState.value = s
                } else {
                    _appState.value = s
                }
            }

            while (true) {
                if (cancelCurrentSSE) {
                    Log.d("MainViewModel", "SSE接收已取消")
                    reader.close()
                    response.close()
                    withContext(Dispatchers.Main) {
                        _statusText.value = "已取消"
                        _appState.value = AppState.Tracking
                    }
                    return
                }

                val line = withContext(Dispatchers.IO) { reader.readLine() } ?: break

                when {
                    line.startsWith("event:") -> {
                        eventType = line.substring(6).trim()
                    }
                    line.startsWith("data:") -> {
                        dataBuffer.append(line.substring(5).trim())
                    }
                    line.isEmpty() && dataBuffer.isNotEmpty() -> {
                        val data = dataBuffer.toString()
                        Log.d("MainViewModel", "SSE事件: $data")
                        dataBuffer = StringBuilder()

                        withContext(Dispatchers.Main) {
                            try {
                                val json = JSONObject(data)
                                val stage = json.optString("stage", "")
                                val qiKey = json.optInt("qi", -1)

                                when (stage) {
                                    "question_split" -> {
                                        val cnt = json.optJSONObject("content")?.optInt("count", 0) ?: 0
                                        val qs = mutableListOf<String>()
                                        json.optJSONObject("content")?.optJSONArray("questions")?.let { arr ->
                                            for (i in 0 until arr.length()) qs.add(arr.optString(i, ""))
                                        }
                                        multiQuestionCount.value = if (cnt > 1) cnt else (if (qs.size > 1) qs.size else 0)
                                        multiQuestionTexts.value = qs
                                        _statusText.value = "识别到 ${multiQuestionCount.value} 道题，正在逐题解答..."
                                    }
                                    "info" -> {
                                        _statusText.value = json.optString("content", "处理中...")
                                        val infoText = json.optString("content", "")
                                        if (infoText.contains("图解") || infoText.contains("渲染")) {
                                            solveProgress.value = "阶段 4/6 · LaTeX图解"
                                        }
                                    }
                                    "ocr_complete" -> {
                                        val ocrObj = json.optJSONObject("content")
                                        val ocrText0 = if (ocrObj != null) ocrObj.optString("text", "") else json.optString("content", "")
                                        ocrMap[cur(qiKey)] = ocrText0
                                        _statusText.value = "正在搜索题库..."
                                        _pendingOcrRequestId.value = requestId
                                        showOcrConfirmDialog.value = true
                                        ocrConfirmText.value = ocrText0
                                        ocrConfirmTitle.value = "确认识别结果"
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.ANALYZING,
                                                requestId = requestId,
                                                ocrText = ocrText0
                                        ))
                                    }
                                    "search_complete" -> {
                                        solveProgress.value = "阶段 1/6 · 搜索题库"
                                        _statusText.value = "AI正在分析..."
                                    }
                                    "search_results" -> {
                                        val arr = json.optJSONArray("content")
                                        val items = mutableListOf<Map<String, String>>()
                                        if (arr != null) {
                                            for (i in 0 until arr.length()) {
                                                val o = arr.optJSONObject(i)
                                                if (o != null) {
                                                    items.add(mapOf(
                                                            "question_md" to o.optString("question_md", ""),
                                                            "hint_md" to o.optString("hint_md", ""),
                                                            "answer_md" to o.optString("answer_md", ""),
                                                            "subject" to o.optString("subject", ""),
                                                            "grade" to o.optString("grade", ""),
                                                            "point_name" to o.optString("point_name", ""),
                                                    ))
                                                }
                                            }
                                        }
                                        solveSearchResults.value = items
                                        _statusText.value = "找到 ${items.size} 条相似题目"
                                    }
                                    "question_info" -> {
                                        _statusText.value = "正在生成解题思路..."
                                        solveProgress.value = "阶段 1/6 · AI分析题目"
                                        val qiObj = json.optJSONObject("content")
                                        var subject0 = ""
                                        if (qiObj != null) {
                                            subject0 = qiObj.optString("subject", "")
                                            val infoMap = mutableMapOf<String, String>()
                                            val gradeV = qiObj.optString("grade", "")
                                            val diffV = qiObj.optString("difficulty", "")
                                            val kps = qiObj.optJSONArray("knowledge_points")
                                            val ems = qiObj.optJSONArray("easy_mistakes")
                                            val dps = qiObj.optJSONArray("difficult_points")
                                            infoMap["grade"] = gradeV
                                            infoMap["subject"] = subject0
                                            infoMap["difficulty"] = diffV
                                            fun joinArr(arr: JSONArray?): String =
                                                    if (arr != null) (0 until arr.length()).map { arr.optString(it, "") }.filter { it.isNotEmpty() }.joinToString("、") else ""
                                            infoMap["knowledge_points"] = joinArr(kps)
                                            infoMap["easy_mistakes"] = joinArr(ems)
                                            infoMap["difficult_points"] = joinArr(dps)
                                            if (qiKey == -1 || currentQuestionIndex.value == qiKey) solveQuestionInfo.value = infoMap
                                        }
                                        subjectMap[cur(qiKey)] = subject0
                                    }
                                    "thinking_start" -> {
                                        solveThinkingText.value = ""
                                        solvingThinkingVisible.value = true
                                        solveProgress.value = "🧠 思考中..."
                                    }
                                    "thinking_chunk" -> {
                                        solvingThinkingVisible.value = true
                                        solveThinkingText.value = json.optString("content", "")
                                    }
                                    "solution_steps_chunk" -> {
                                        val chunk = json.optString("content", "")
                                        if (chunk.isNotEmpty()) {
                                            val now = System.currentTimeMillis()
                                            if (now - lastStepsUpdate >= 100) {
                                                lastStepsUpdate = now
                                                val ss = extractStepsText(chunk)
                                                stepsMap[cur(qiKey)] = ss
                                                emit(qiKey, AppState.Solving(
                                                        stage = SolveStage.DISPLAY_STEPS,
                                                        requestId = requestId,
                                                        ocrText = ocrMap[cur(qiKey)] ?: "",
                                                        solutionSteps = ss,
                                                        stepsStreaming = true
                                                ))
                                            }
                                        }
                                    }
                                    "solution_steps" -> {
                                        val ss = extractStepsText(json.optString("content", ""))
                                        stepsMap[cur(qiKey)] = ss
                                        solveProgress.value = "阶段 2/6 · 解题思路"
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.DISPLAY_STEPS,
                                                requestId = requestId,
                                                ocrText = ocrMap[cur(qiKey)] ?: "",
                                                solutionSteps = ss
                                        ))
                                    }
                                    "solution_chunk" -> {
                                        val chunk = json.optString("content", "")
                                        if (chunk.isNotEmpty()) {
                                            val now = System.currentTimeMillis()
                                            if (now - lastSolutionUpdate >= 100) {
                                                lastSolutionUpdate = now
                                                fullMap[cur(qiKey)] = chunk
                                                _statusText.value = "AI正在生成完整解析..."
                                                emit(qiKey, AppState.Solving(
                                                        stage = SolveStage.DISPLAY_FULL,
                                                        requestId = requestId,
                                                        ocrText = ocrMap[cur(qiKey)] ?: "",
                                                        solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                        fullSolution = chunk,
                                                        solutionStreaming = true
                                                ))
                                            }
                                        }
                                    }
                                    "solution", "solution_rendered" -> {
                                        val fs = json.optString("content", "")
                                        fullMap[cur(qiKey)] = fs
                                        solveProgress.value = if (stage == "solution_rendered") "阶段 4/6 · 渲染完成" else "阶段 3/6 · 完整解析"
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.DISPLAY_FULL,
                                                requestId = requestId,
                                                ocrText = ocrMap[cur(qiKey)] ?: "",
                                                solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                fullSolution = fs
                                        ))
                                    }
                                    "mindmap_chunk" -> {
                                        val chunk = json.optString("content", "")
                                        if (chunk.isNotEmpty()) {
                                            val now = System.currentTimeMillis()
                                            if (now - lastMindmapUpdate >= 100) {
                                                lastMindmapUpdate = now
                                                val mm = formatMindMap(chunk)
                                                mindMapMap[cur(qiKey)] = mm
                                                emit(qiKey, AppState.Solving(
                                                        stage = SolveStage.DISPLAY_MINDMAP,
                                                        requestId = requestId,
                                                        ocrText = ocrMap[cur(qiKey)] ?: "",
                                                        solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                        fullSolution = fullMap[cur(qiKey)] ?: "",
                                                        mindMap = mm,
                                                        mindmapStreaming = true
                                                ))
                                            }
                                        }
                                    }
                                    "mindmap" -> {
                                        val mm = formatMindMap(json.optString("content", ""))
                                        mindMapMap[cur(qiKey)] = mm
                                        solveProgress.value = "阶段 5/6 · 思维导图"
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.DISPLAY_MINDMAP,
                                                requestId = requestId,
                                                ocrText = ocrMap[cur(qiKey)] ?: "",
                                                solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                fullSolution = fullMap[cur(qiKey)] ?: "",
                                                mindMap = mm
                                        ))
                                    }
                                    "suggested_questions" -> {
                                        val content = json.opt("content")
                                        val qaItems = mutableListOf<QAItem>()
                                        val qStrings = mutableListOf<String>()
                                        if (content is JSONArray) {
                                            for (i in 0 until content.length()) {
                                                val obj = content.optJSONObject(i)
                                                if (obj != null) {
                                                    val q = obj.optString("question", "")
                                                    val a = obj.optString("answer", "")
                                                    if (q.isNotEmpty()) { qaItems.add(QAItem(q, a)); qStrings.add(q) }
                                                } else {
                                                    content.optString(i, "").takeIf { it.isNotEmpty() }?.let { qStrings.add(it) }
                                                }
                                            }
                                        }
                                        suggQAMap[cur(qiKey)] = qaItems
                                        suggMap[cur(qiKey)] = qStrings
                                        solveProgress.value = "阶段 6/6 · 预判问题"
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.INTERACTIVE,
                                                requestId = requestId,
                                                ocrText = ocrMap[cur(qiKey)] ?: "",
                                                solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                fullSolution = fullMap[cur(qiKey)] ?: "",
                                                mindMap = mindMapMap[cur(qiKey)] ?: "",
                                                suggestedQuestions = qStrings,
                                                suggestedQA = qaItems,
                                                qaList = qaMap[cur(qiKey)] ?: emptyList(),
                                                subject = subjectMap[cur(qiKey)] ?: ""
                                        ))
                                    }
                                    "complete" -> {
                                        solveProgress.value = ""
                                        emit(qiKey, AppState.Solving(
                                                stage = SolveStage.COMPLETED,
                                                requestId = requestId,
                                                ocrText = ocrMap[cur(qiKey)] ?: "",
                                                solutionSteps = stepsMap[cur(qiKey)] ?: "",
                                                fullSolution = fullMap[cur(qiKey)] ?: "",
                                                mindMap = mindMapMap[cur(qiKey)] ?: "",
                                                suggestedQuestions = suggMap[cur(qiKey)] ?: emptyList(),
                                                suggestedQA = suggQAMap[cur(qiKey)] ?: emptyList(),
                                                qaList = qaMap[cur(qiKey)] ?: emptyList(),
                                                subject = subjectMap[cur(qiKey)] ?: ""
                                        ))
                                        _statusText.value = if (multiQuestionCount.value > 1) "全部题目解答完成" else "解答完成"
                                        masteryVisible.value = true
                                    }
                                    "error" ->
                                            _statusText.value = "错误: ${json.optString("content")}"
                                    "blurred" -> {
                                        // ⑥ 图片模糊，提示重拍并回到主页
                                        _statusText.value = json.optString("content", "图片模糊，请重新拍摄")
                                        showOcrConfirmDialog.value = false
                                        _appState.value = AppState.Tracking
                                    }
                                }
                            } catch (e: Exception) {
                                Log.e("MainViewModel", "解析SSE事件失败: $data", e)
                            }
                        }
                    }
                }
            }
            reader.close()
            response.close()
        } catch (e: Exception) {
            Log.e("MainViewModel", "SSE连接失败: ${e.message}", e)
            withContext(Dispatchers.Main) { _statusText.value = "SSE失败: ${e.message}" }
        }
    }

    fun askQuestion(question: String) {
        val currentState = _appState.value as? AppState.Solving ?: return
        _statusText.value = "正在获取回答..."
        isAskingQuestion.value = true  // Feature 3: 显示loading

        viewModelScope.launch(Dispatchers.IO) {
            try {
                // 流式回答：与AI解题相同的SSE方式，回答逐字显示在气泡中
                val sb = StringBuilder()
                var lastAskUpdate = 0L
                var answerRendered = false
                apiService.askQuestionStream(
                        currentState.requestId,
                        question,
                        onChunk = { chunk ->
                            // 服务端可能下发增量或累积全文：仅追加新增部分，避免“重复多一个字”
                            appendStreamDelta(sb, chunk)
                            val now = System.currentTimeMillis()
                            if (now - lastAskUpdate >= 100) {
                                lastAskUpdate = now
                                val partial = sb.toString()
                                withContext(Dispatchers.Main) {
                                    _appState.value =
                                            currentState.copy(pendingAnswer = partial, isAnswering = true)
                                }
                            }
                        },
                        onRendered = { rendered ->
                            // ① 追问LaTeX已渲染为图片：用渲染后的Markdown替换气泡内容
                            answerRendered = true
                            sb.setLength(0)
                            sb.append(rendered)
                            withContext(Dispatchers.Main) {
                                _appState.value =
                                        currentState.copy(pendingAnswer = rendered, isAnswering = true)
                            }
                        }
                )

                withContext(Dispatchers.Main) {
                    // 问答追加到对话记录（气泡展示），不再混入完整解析
                    _appState.value =
                            currentState.copy(
                                    qaList = currentState.qaList + QAItem(question, sb.toString(), answerRendered),
                                    pendingAnswer = "",
                                    isAnswering = false
                            )
                    _statusText.value = "已回答"
                    isAskingQuestion.value = false
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _statusText.value = "提问失败: ${e.message}"
                    isAskingQuestion.value = false
                    _appState.value = currentState.copy(pendingAnswer = "", isAnswering = false)
                }
            }
        }
    }

    fun saveMastery(level: String) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                apiService.saveMastery(currentSolvingRequestId.value, level)
                withContext(Dispatchers.Main) {
                    masteryLevel.value = when (level) {
                        "completely_mastered" -> "完全掌握"
                        "partially_mastered" -> "部分掌握"
                        else -> "完全没掌握"
                    }
                    masterySaved.value = true
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { _statusText.value = "保存掌握程度失败: ${e.message}" }
            }
        }
    }

    fun generateGeoGebra() {
        val state = _appState.value as? AppState.Solving
        val ocrText = state?.ocrText
        if (ocrText.isNullOrBlank()) {
            _statusText.value = "没有可用的题目内容"
            return
        }
        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _statusText.value = "正在生成数学图形..."
                    isGeneratingGeoGebra.value = true
                }
                val resp = apiService.generateGeoGebra(ocrText)
                withContext(Dispatchers.Main) {
                    isGeneratingGeoGebra.value = false
                    if (resp.status == "ok" && resp.url.isNotEmpty()) {
                        geoGebraUrl.value = "${apiService.getBaseUrl()}${resp.url}"
                        showGeoGebraScreen.value = true
                    } else {
                        _statusText.value = "图形生成失败: ${resp.message}"
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    isGeneratingGeoGebra.value = false
                    _statusText.value = "图形生成失败: ${e.message}"
                }
            }
        }
    }

    fun requestAnimation(photoFile: java.io.File) {
        Log.d("MainViewModel", "请求AI动画，上传图片: ${photoFile.absolutePath}")
        cancelCurrentSSE = false

        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Animation  // Feature 6: loading状态
                    _statusText.value = "正在生成动画..."
                }

                val photoBytes = photoFile.readBytes()
                val response = apiService.requestAnimation(photoBytes)

                withContext(Dispatchers.Main) {
                    if (response.status == "ok") {
                        _statusText.value = "动画已生成"
                        _animationUrl.value = "${apiService.getBaseUrl()}${response.url}"
                    } else {
                        _statusText.value = "动画生成失败: ${response.message}"
                        _appState.value = AppState.Tracking
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _statusText.value = "动画请求失败: ${e.message}"
                    _appState.value = AppState.Tracking
                }
            }
        }
    }

    fun loadAiReport(days: Int? = null) {
        if (days == null) {
            // ② 无参数→先弹时间范围选择
            pendingReportType.value = "ai"
            showReportRangeDialog.value = true
            return
        }
        val reportDaysVal = days
        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Report(reportText = "", isLoading = true)  // Feature 6: loading状态
                    _statusText.value = "正在生成AI学情报告..."
                }

                // SSE流式接收，打字机效果（150ms节流；流式期间只显示纯文本，完成后Markdown渲染）
                val sb = StringBuilder()
                var lastReportUpdate = 0L
                apiService.getAiReportStream(reportDaysVal) { chunk ->
                    // 服务端可能下发增量或累积全文：仅追加新增部分，避免重复
                    appendStreamDelta(sb, chunk)
                    val now = System.currentTimeMillis()
                    if (now - lastReportUpdate >= 150) {
                        lastReportUpdate = now
                        val text = sb.toString()
                        withContext(Dispatchers.Main) {
                            _appState.value = AppState.Report(reportText = text, isLoading = false, streaming = true)
                        }
                    }
                }

                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Report(reportText = sb.toString(), isLoading = false, streaming = false)
                    _statusText.value = "AI报告已生成"
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _statusText.value = "报告请求失败: ${e.message}"
                    _appState.value = AppState.Tracking
                }
            }
        }
    }

    fun loadDataReport(days: Int? = null) {
        if (days == null) {
            // ② 无参数→先弹时间范围选择
            pendingReportType.value = "data"
            showReportRangeDialog.value = true
            return
        }
        val reportDaysVal = days
        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Report(isLoading = true)  // Feature 6: loading状态
                    _statusText.value = "正在生成数据学情报告..."
                }

                val response = apiService.getDataReport(reportDaysVal, theme = if (AppDarkTheme) "dark" else "light")

                withContext(Dispatchers.Main) {
                    if (response.status == "ok") {
                        val fullUrl = "${apiService.getBaseUrl()}${response.url}"
                        _appState.value =
                                AppState.Report(
                                        reportText = fullUrl,
                                        isLoading = false
                                )
                        _statusText.value = "数据报告已生成"
                    } else {
                        _statusText.value = "报告生成失败: ${response.message}"
                        _appState.value = AppState.Tracking
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _statusText.value = "报告请求失败: ${e.message}"
                    _appState.value = AppState.Tracking
                }
            }
        }
    }

    // 报告统计周期（0=全部历史）
    val reportDays = MutableStateFlow(7)

    fun requestKnowledgeExtension(photoFile: java.io.File) {
        Log.d("MainViewModel", "请求知识延伸: ${photoFile.absolutePath}")
        cancelCurrentSSE = false

        viewModelScope.launch(Dispatchers.IO) {
            try {
                withContext(Dispatchers.Main) {
                    _appState.value = AppState.Knowledge()  // Feature 6: loading状态
                    _statusText.value = "正在上传..."
                }

                val photoBytes = photoFile.readBytes()
                val requestId = apiService.startExtend(photoBytes)

                withContext(Dispatchers.Main) { _statusText.value = "正在分析..." }

                connectExtendSSE(requestId)
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    _statusText.value = "请求失败: ${e.message}"
                    _appState.value = AppState.Tracking
                }
            }
        }
    }

    private suspend fun connectExtendSSE(requestId: String) {
        val client =
                OkHttpClient.Builder()
                        .connectTimeout(30, TimeUnit.SECONDS)
                        .readTimeout(0, TimeUnit.MILLISECONDS)
                        .build()

        val request =
                Request.Builder()
                        .url("${apiService.getBaseUrl()}/extend/stream/$requestId")
                        .header("Accept", "text/event-stream")
                        .build()

        try {
            val response = withContext(Dispatchers.IO) { client.newCall(request).execute() }

            val reader = BufferedReader(InputStreamReader(response.body?.byteStream()))
            var dataBuffer = StringBuilder()
            var summary = ""
            var extension = ""
            var similar = emptyList<QAItem>()
            var questions = emptyList<QAItem>()
            var lastSummaryUpdate = 0L
            var lastExtensionUpdate = 0L

            while (true) {
                val line = withContext(Dispatchers.IO) { reader.readLine() } ?: break

                when {
                    line.startsWith("data:") -> {
                        dataBuffer.append(line.substring(5).trim())
                    }
                    line.isEmpty() && dataBuffer.isNotEmpty() -> {
                        val data = dataBuffer.toString()
                        dataBuffer = StringBuilder()

                        withContext(Dispatchers.Main) {
                            val json = JSONObject(data)
                            val stage = json.optString("stage")

                            when (stage) {
                                "info" -> _statusText.value = json.optString("content", "处理中...")
                                "ocr_complete" -> _statusText.value = "正在分析内容..."
                                "question_info" -> {
                                    _appState.value = AppState.Knowledge()
                                    _statusText.value = "正在总结知识点..."
                                }
                                "summary_chunk" -> {
                                    val chunk = json.optString("content", "")
                                    if (chunk.isNotEmpty()) {
                                        val now = System.currentTimeMillis()
                                        if (now - lastSummaryUpdate >= 100) {
                                            lastSummaryUpdate = now
                                            summary = chunk
                                            _appState.value = AppState.Knowledge(summary = summary, summaryStreaming = true)
                                            _statusText.value = "正在总结知识点..."
                                        }
                                    }
                                }
                                "summary" -> {
                                    summary = json.optString("content", "")
                                    _appState.value = AppState.Knowledge(summary = summary)
                                    _statusText.value = "知识点已总结"
                                }
                                "similar_questions" -> {
                                    similar = parseQAList(json.opt("content"))
                                    _appState.value = AppState.Knowledge(summary = summary, similarQuestions = similar)
                                    _statusText.value = "已推荐相似题"
                                }
                                "extension_chunk" -> {
                                    val chunk = json.optString("content", "")
                                    if (chunk.isNotEmpty()) {
                                        val now = System.currentTimeMillis()
                                        if (now - lastExtensionUpdate >= 100) {
                                            lastExtensionUpdate = now
                                            extension = chunk
                                            _appState.value = AppState.Knowledge(summary = summary, similarQuestions = similar, extension = extension, extensionStreaming = true)
                                            _statusText.value = "正在生成知识拓展..."
                                        }
                                    }
                                }
                                "extension" -> {
                                    extension = json.optString("content", "")
                                    _appState.value = AppState.Knowledge(summary = summary, similarQuestions = similar, extension = extension)
                                    _statusText.value = "知识拓展已生成"
                                }
                                "suggested_questions" -> {
                                    questions = parseQAList(json.opt("content"))
                                    _appState.value = AppState.Knowledge(summary = summary, similarQuestions = similar, extension = extension, suggestedQuestions = questions)
                                    _statusText.value = "延伸完成"
                                }
                                "complete" -> {
                                    _appState.value = AppState.Knowledge(summary = summary, similarQuestions = similar, extension = extension, suggestedQuestions = questions)
                                    _statusText.value = "延伸完成"
                                }
                                "error" -> _statusText.value = "错误: ${json.optString("content")}"
                            }
                        }
                    }
                }
            }
            reader.close()
            response.close()
        } catch (e: Exception) {
            withContext(Dispatchers.Main) { _statusText.value = "连接失败: ${e.message}" }
        }
    }

    fun backToTracking() {
        cancelCurrentSSE = true  // Feature 5: 取消当前SSE接收
        isAskingQuestion.value = false
        showMasteryDialog.value = false
        showOcrConfirmDialog.value = false
        _pendingOcrRequestId.value = ""
        _appState.value = AppState.Tracking
        _statusText.value = "跟踪学习中..."
    }

    // Feature 11: 跳过休息
    fun skipRest() {
        isResting.value = false
        restTimeRemaining.value = 0
        _pomodoroTime.value = pomodoroWorkDuration.value * 60
        _statusText.value = "休息结束，继续学习！"
    }

    private val _detectedFingerCount = MutableStateFlow(0)
    val detectedFingerCount: StateFlow<Int> = _detectedFingerCount.asStateFlow()

    fun setFingerCount(count: Int) {
        _detectedFingerCount.value = count
    }

    val showSettingsDialog = MutableStateFlow(false)
    val showWelcomeDialog = MutableStateFlow(true)
    val showHistoryScreen = MutableStateFlow(false)

    val gestureEnabled = MutableStateFlow(false)
    val voiceEnabled = MutableStateFlow(false)
    val tomatoEnabled = MutableStateFlow(false)
    val serverAddress = MutableStateFlow("10.100.55.231:8000")
    val themeColor = MutableStateFlow(Color(0xFF00D2FF))
    val fontSize = MutableStateFlow(16f)

    val showModules =
            MutableStateFlow(
                    mapOf(
                            "solution_steps" to true,
                            "full_solution" to true,
                            "mind_map" to true,
                            "suggested_questions" to true,
                            "extension" to true,
                    )
            )

    val historyStartDate = MutableStateFlow("")
    val historyEndDate = MutableStateFlow("")
    // ① 多题模式
    val multiQuestionCount = MutableStateFlow(0)                 // 分题后题目总数（>1 表示多题模式）
    val multiQuestionTexts = MutableStateFlow<List<String>>(emptyList())  // 各题OCR文本
    val currentQuestionIndex = MutableStateFlow(0)               // 当前查看的题号
    val multiSolveStates = MutableStateFlow<List<AppState.Solving>>(emptyList())  // 每题完整解题状态
    // ⑩ 主页面相机对准预览开关
    val cameraPreviewEnabled = MutableStateFlow(false)
    // ② 报告时间范围弹窗（生成报告前先询问）
    val showReportRangeDialog = MutableStateFlow(false)
    val pendingReportType = MutableStateFlow("")  // "data" / "ai"

    // ⑤ AI模型设置（大语言模型提供方/模型 + 视觉OCR模型）
    val llmProvider = MutableStateFlow("deepseek")
    val llmModel = MutableStateFlow("")
    val ocrMode = MutableStateFlow("paddle")
    val visionModel = MutableStateFlow("")
    // ④ 搜题结果（SSE search_results 阶段下发，客户端按钮+Markdown展示）
    val solveSearchResults = MutableStateFlow<List<Map<String, String>>>(emptyList())
    // ⑩ 第一轮JSON题目分析（年级/学科/难度/知识点/易错点/难点 → 顶部标签）
    val solveQuestionInfo = MutableStateFlow<Map<String, String>>(emptyMap())
    // 十一 思考模式：完整解析生成前的思维链文本（显示在完整解析上方）
    val solveThinkingText = MutableStateFlow("")
    val solvingThinkingVisible = MutableStateFlow(false)
    // ②④⑦十一 设置项（持久化到 ApiService）
    val answerStyle = MutableStateFlow("formal")
    val searchEnabled = MutableStateFlow(true)
    val thinkingEnabled = MutableStateFlow(false)
    val themeMode = MutableStateFlow("system")   // system / light / dark
    // ③ 方言 / ④ 年级
    val dialect = MutableStateFlow("四川话")
    val grade = MutableStateFlow("")

    fun saveExtraSettings() {
        apiService.saveExtraSettings(
            answerStyle.value, searchEnabled.value,
            thinkingEnabled.value, themeMode.value
        )
    }

    fun saveDialectGrade() {
        apiService.saveDialectGrade(dialect.value, grade.value)
    }

    // ① 多题切换：切换到指定题目页
    fun switchQuestion(index: Int) {
        val list = multiSolveStates.value
        if (index in list.indices) {
            currentQuestionIndex.value = index
            _appState.value = list[index]
        }
    }

    // ④ 导出 PDF/Word（服务端生成后返回本地路径，经回调交给UI分享）
    fun exportContent(title: String, content: String, format: String, onResult: (String?, String?) -> Unit) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val path = apiService.exportDocument(title, content, format)
                withContext(Dispatchers.Main) { onResult(path, null) }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { onResult(null, e.message ?: "导出失败") }
            }
        }
    }

    fun updateServerAddress(address: String) {
        serverAddress.value = address.removePrefix("http://")
        apiService.updateServerAddress(address)
        Log.d("MainViewModel", "服务器地址已更新: $address")
    }

    fun showSettings() {
        showSettingsDialog.value = true
    }
    fun showHistory() {
        showHistoryScreen.value = true
    }

    fun clearHistory() {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                apiService.clearHistory()
                withContext(Dispatchers.Main) { _statusText.value = "历史记录已清除" }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { _statusText.value = "清除失败: ${e.message}" }
            }
        }
    }

    var onButtonTakePhoto: (() -> Unit)? = null
    var onButtonAnimation: (() -> Unit)? = null
    var onButtonExtend: (() -> Unit)? = null

    fun onButtonSolve() {
        _statusText.value = "正在拍照..."
        onButtonTakePhoto?.invoke()
    }

    fun onButtonAnimation() {
        _statusText.value = "正在准备动画..."
        onButtonAnimation?.invoke()
    }

    fun onButtonExtend() {
        _statusText.value = "正在准备知识延伸..."
        onButtonExtend?.invoke()
    }

    override fun onCleared() {
        super.onCleared()
        thinkingJob?.cancel()
        pomodoroJob?.cancel()
    }
}


class MainActivity : ComponentActivity() {

    companion object {
        const val TAG = "LearningAssistant"
    }

    private lateinit var cameraExecutor: java.util.concurrent.ExecutorService

    private var imageCapture: ImageCapture? = null
    private val gestureRecognizerState = MutableStateFlow<HandGestureRecognizer?>(null)
    private val isCameraReady = MutableStateFlow(false)


    private val hasCameraPermission = MutableStateFlow(false)

    private lateinit var mainViewModel: MainViewModel

    private val cameraRebindTrigger = MutableStateFlow(0)

    var onTakePhoto: ((ImageCapture) -> Unit)? = null

    private val requestPermissionLauncher =
            registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
                    permissions ->
                val granted = permissions.values.all { it }
                if (granted) {
                    Log.d(TAG, "所有权限已授予")
                    hasCameraPermission.value = true
                } else {
                    Log.w(TAG, "权限被拒绝")
                    Toast.makeText(this, "需要相机权限才能正常使用", Toast.LENGTH_LONG).show()
                    hasCameraPermission.value = false
                }
            }

    private val serverDiscoverer = ServerDiscoverer()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        discoverAndConnect()

        cameraExecutor = Executors.newSingleThreadExecutor()

        initGestureRecognizer()

        val hasCamera =
            ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
        val hasInternet =
            ContextCompat.checkSelfPermission(this, Manifest.permission.INTERNET) ==
                    PackageManager.PERMISSION_GRANTED
        Log.d(TAG, "权限状态- 相机:$hasCamera, 网络:$hasInternet")

        if (hasCamera) {
            hasCameraPermission.value = true
            Log.d(TAG, "相机权限已授予，直接设置")
        } else {
            Log.d(TAG, "相机权限未授予，主动请求")
            requestPermissionLauncher.launch(
                arrayOf(
                    Manifest.permission.CAMERA,
                    Manifest.permission.INTERNET,
                    Manifest.permission.ACCESS_NETWORK_STATE,
                    Manifest.permission.RECORD_AUDIO,
                )
            )
        }

        setContent {
            // ⑦ 主题模式：跟随系统 / 亮色 / 暗色
            val themeVm = viewModel<MainViewModel>()
            mainViewModel = themeVm
            val themeModeState by themeVm.themeMode.collectAsState()
            val systemDark = isSystemInDarkTheme()
            val appDark = when (themeModeState) {
                "light" -> false
                "dark" -> true
                else -> systemDark
            }
            LearningAssistantTheme(dark = appDark) {
                val viewModel = viewModel<MainViewModel>()
                mainViewModel = viewModel

                LaunchedEffect(Unit) {
                    viewModel.initApiService(this@MainActivity)
                }

                fun requireCamera(action: String, takeAction: (ImageCapture) -> Unit) {
                    Log.d(TAG, "$action: imageCapture=${imageCapture}, isCameraReady=${isCameraReady.value}")
                    if (!isCameraReady.value) {
                        Toast.makeText(this@MainActivity, "相机未就绪，请稍后再试", Toast.LENGTH_SHORT).show()
                        return
                    }
                    imageCapture?.let { capture ->
                        takeAction(capture)
                    } ?: run {
                        Log.e(TAG, "imageCapture 为 null")
                        Toast.makeText(this@MainActivity, "相机未就绪，请稍后再试", Toast.LENGTH_SHORT).show()
                    }
                }

                viewModel.onButtonTakePhoto = {
                    requireCamera("onButtonTakePhoto") { capture ->
                        takePhotoAndUpload(capture, viewModel)
                    }
                }

                viewModel.onButtonAnimation = {
                    requireCamera("onButtonAnimation") { capture ->
                        takePhotoAndUploadAnimation(capture, viewModel)
                    }
                }

                viewModel.onButtonExtend = {
                    requireCamera("onButtonExtend") { capture ->
                        takePhotoAndUploadExtend(capture, viewModel)
                    }
                }

                // 相册选图（模拟器无可用相机时的替代方案）
                var pendingGalleryAction by remember { mutableStateOf<String?>(null) }
                val galleryLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.GetContent()
                ) { uri ->
                    val action = pendingGalleryAction
                    pendingGalleryAction = null
                    if (uri != null && action != null) {
                        handleGalleryImage(uri, action, viewModel)
                    }
                }

                val hasCameraPerm by hasCameraPermission.collectAsState()
                val gestureRecognizer by gestureRecognizerState.collectAsState()
                val cameraRebind by cameraRebindTrigger.collectAsState()
                val cameraReady by isCameraReady.collectAsState()

                MainScreen(
                    hasCameraPermission = hasCameraPerm,
                    gestureRecognizer = gestureRecognizer,
                    viewModel = viewModel,
                    onImageCaptureReady = { capture ->
                        imageCapture = capture
                        isCameraReady.value = true
                        Log.d(TAG, "ImageCapture 已就绪")
                    },
                    cameraRebindTrigger = cameraRebind,
                    onPickImage = { action ->
                        pendingGalleryAction = action
                        galleryLauncher.launch("image/*")
                    },
                    isCameraReady = cameraReady
                )
            }
        }
    }

    private fun discoverAndConnect() {
        lifecycleScope.launch {
            delay(500)
            val address = serverDiscoverer.discover()
            if (address != null) {
                Log.d(TAG, "自动发现成功: $address")
                mainViewModel.updateServerAddress(address)
            } else {
                Log.w(TAG, "未发现服务端，将使用默认地址")
            }
        }
    }

    private fun takePhotoAndUpload(capture: ImageCapture, viewModel: MainViewModel) {
        if (!isCameraReady.value) {
            viewModel.onPhotoError("相机未就绪")
            return
        }

        val photoFile = java.io.File(externalCacheDir, "solve_${System.currentTimeMillis()}.jpg")
        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        val mainHandler = Handler(Looper.getMainLooper())
        val timeoutRunnable = Runnable {
            Log.e(TAG, "拍照超时")
            viewModel.onPhotoError("拍照超时")
        }
        mainHandler.postDelayed(timeoutRunnable, 5000)

        capture.takePicture(
            outputOptions,
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    mainHandler.removeCallbacks(timeoutRunnable)
                    Log.d(TAG, "照片已保存: ${photoFile.absolutePath}")
                    viewModel.onPhotoReady(photoFile)
                }

                override fun onError(exception: ImageCaptureException) {
                    mainHandler.removeCallbacks(timeoutRunnable)
                    Log.e(TAG, "拍照失败: ${exception.message}")
                    if (exception.message?.contains("closed") == true) {
                        isCameraReady.value = false
                        imageCapture = null
                        cameraRebindTrigger.value += 1
                    }
                    viewModel.onPhotoError(exception.message ?: "拍照失败")
                }
            }
        )
    }

    private fun takePhotoAndUploadAnimation(capture: ImageCapture, viewModel: MainViewModel) {
        val photoFile = java.io.File(externalCacheDir, "anim_${System.currentTimeMillis()}.jpg")

        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        capture.takePicture(
                outputOptions,
                cameraExecutor,
                object : ImageCapture.OnImageSavedCallback {
                    override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                        Log.d(TAG, "动画照片已保存: ${photoFile.absolutePath}")
                        viewModel.requestAnimation(photoFile)
                    }

                    override fun onError(exception: ImageCaptureException) {
                        Log.e(TAG, "拍照失败: ${exception.message}")
                    }
                }
        )
    }

    private fun initGestureRecognizer() {
        lifecycleScope.launch(Dispatchers.IO) {
            val recognizer = HandGestureRecognizer(
                    context = this@MainActivity,
                    onGestureDetected = { fingerCount ->
                        Log.d(TAG, "手势识别: $fingerCount 根手指")
                            runOnUiThread {
                                when (fingerCount) {
                                    // ⑦ 统一拍照链路：手势与按钮走同一方法（避免相机状态不一致导致 “Camera is closed”）；
                                    // 不再手动调用 startSolving()（onPhotoReady 会负责状态切换），避免拍照前相机被解绑
                                    5 -> mainViewModel.onButtonSolve()
                                    4 -> mainViewModel.onButtonAnimation()
                                    3 -> mainViewModel.onGestureDetected(3)
                                    2 -> mainViewModel.onGestureDetected(2)
                                    1 -> mainViewModel.onButtonExtend()
                                }
                            }
                        },
                        onFingerCountChanged = { fingerCount ->
                            runOnUiThread { mainViewModel.setFingerCount(fingerCount) }
                        },
                        onError = { error ->
                            Log.e(TAG, "手势识别错误: ${error.message}")
                            runOnUiThread {
                                Toast.makeText(this@MainActivity, "手势识别失败: ${error.message}", Toast.LENGTH_SHORT)
                                        .show()
                            }
                        }
                )
            
            withContext(Dispatchers.Main) {
                gestureRecognizerState.value = recognizer
                Log.d(TAG, "手势识别器初始化完成")
            }
        }
    }

    private fun requestCameraPermissions() {
        val permissions =
                arrayOf(
                        Manifest.permission.CAMERA,
                        Manifest.permission.INTERNET,
                        Manifest.permission.ACCESS_NETWORK_STATE,
                )

        val needRequest =
                permissions.any {
                    val hasIt =
                            ContextCompat.checkSelfPermission(this, it) ==
                                    PackageManager.PERMISSION_GRANTED
                    Log.d(TAG, "权限 $it: ${if (hasIt) "已授予" else "未授予"}")
                    !hasIt
                }

        if (needRequest) {
            Log.d(TAG, "正在请求权限...")
            requestPermissionLauncher.launch(permissions)
        } else {
            Log.d(TAG, "所有权限已授予，无需请求")
            hasCameraPermission.value = true
        }
    }

    private fun handleGalleryImage(uri: android.net.Uri, action: String, viewModel: MainViewModel) {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val file = java.io.File(cacheDir, "gallery_${System.currentTimeMillis()}.jpg")
                contentResolver.openInputStream(uri)?.use { input ->
                    file.outputStream().use { output -> input.copyTo(output) }
                } ?: throw Exception("无法读取所选图片")
                Log.d(TAG, "相册图片已保存: ${file.absolutePath}")
                withContext(Dispatchers.Main) {
                    when (action) {
                        "solve" -> viewModel.onPhotoReady(file)
                        "animation" -> viewModel.requestAnimation(file)
                        "extend" -> viewModel.requestKnowledgeExtension(file)
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "读取相册图片失败: ${e.message}")
                withContext(Dispatchers.Main) {
                    viewModel.onPhotoError("读取图片失败: ${e.message}")
                }
            }
        }
    }

    private fun takePhotoAndUploadExtend(capture: ImageCapture, viewModel: MainViewModel) {
        val photoFile = java.io.File(externalCacheDir, "extend_${System.currentTimeMillis()}.jpg")

        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        capture.takePicture(
                outputOptions,
                cameraExecutor,
                object : ImageCapture.OnImageSavedCallback {
                    override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                        Log.d(TAG, "延伸照片已保存: ${photoFile.absolutePath}")
                        viewModel.requestKnowledgeExtension(photoFile)
                    }

                    override fun onError(exception: ImageCaptureException) {
                        Log.e(TAG, "拍照失败: ${exception.message}")
                    }
                }
        )
    }

    override fun onDestroy() {
        super.onDestroy()
        gestureRecognizerState.value?.close()
        cameraExecutor.shutdown()
    }
}

@Composable
fun MainScreen(
    hasCameraPermission: Boolean = false,
    gestureRecognizer: HandGestureRecognizer? = null,
    viewModel: MainViewModel,
    onImageCaptureReady: ((ImageCapture) -> Unit)? = null,
    cameraRebindTrigger: Int = 0,
    onPickImage: ((String) -> Unit)? = null,
    isCameraReady: Boolean = false,
) {
    val appState by viewModel.appState.collectAsState()
    val statusText by viewModel.statusText.collectAsState()
    val gestureEnabled by viewModel.gestureEnabled.collectAsState()
    val isMainScreen = appState is AppState.Tracking
    val showModules by viewModel.showModules.collectAsState()
    val isAskingQuestion by viewModel.isAskingQuestion.collectAsState()
    val animationUrl by viewModel.animationUrl.collectAsState()
    val showWelcome by viewModel.showWelcomeDialog.collectAsState()
    val showSettings by viewModel.showSettingsDialog.collectAsState()
    val showHistory by viewModel.showHistoryScreen.collectAsState()
    val showOcr by viewModel.showOcrConfirmDialog.collectAsState()
    val ocrConfirmTitle by viewModel.ocrConfirmTitle.collectAsState()
    val ocrConfirmText by viewModel.ocrConfirmText.collectAsState()
    val showGeoGebra by viewModel.showGeoGebraScreen.collectAsState()
    val geogebraUrl by viewModel.geoGebraUrl.collectAsState()
    val reportDays by viewModel.reportDays.collectAsState()

    Box(modifier = Modifier.fillMaxSize().background(tC(Color(0xFF0A0A1A), Color(0xFFF2F4F8)))) {
        // Feature 11: 番茄钟休息全屏界面
        val isResting by viewModel.isResting.collectAsState()
        if (isResting) {
            PomodoroRestScreen(viewModel = viewModel)
            return@Box
        }

        if (hasCameraPermission) {
            CameraPreviewView(
                modifier = if (isMainScreen) Modifier.fillMaxSize() else Modifier.fillMaxSize().alpha(0f),
                gestureRecognizer = if (gestureEnabled && isMainScreen) gestureRecognizer else null,
                onImageCaptureReady = onImageCaptureReady,
                onCameraReady = {
                    Log.d("MainScreen", "相机已就绪")
                },
                rebindKey = cameraRebindTrigger
            )
        }
        when (val state = appState) {
            is AppState.Login ->
                LoginScreen(
                    isLoading = state.isLoading,
                    error = state.error,
                    onLogin = { username, password -> viewModel.login(username, password) },
                    onRegister = { username, password -> viewModel.register(username, password) },
                )
            is AppState.Tracking ->
                MainMenuScreen(
                    viewModel = viewModel,
                    statusText = statusText,
                    gestureEnabled = gestureEnabled,
                    onPickImage = onPickImage,
                    isCameraReady = isCameraReady,
                )
            is AppState.Solving ->
                SolvingScreen(
                    solveState = state,
                    showModules = showModules,
                    onAskQuestion = { viewModel.askQuestion(it) },
                    onBack = { viewModel.backToTracking() },
                    isAskingQuestion = isAskingQuestion,
                    viewModel = viewModel,
                )
            is AppState.Report -> {
                val isDataReport = state.reportText.startsWith("http")
                if (state.isLoading) {
                    LoadingOverlay("正在生成报告...")
                } else {
                    ReportScreen(
                            report = state,
                            onBack = { viewModel.backToTracking() },
                            reportUrl = if (isDataReport) state.reportText else "",
                            reportText = if (!isDataReport) state.reportText else "",
                            reportDays = reportDays,
                            onSelectDays = { days ->
                                viewModel.reportDays.value = days
                                if (isDataReport) {
                                    viewModel.loadDataReport(days)
                                } else {
                                    viewModel.loadAiReport(days)
                                }
                            }
                    )
                }
            }
            is AppState.Animation -> {
                if (animationUrl.isEmpty()) {
                    LoadingOverlay("正在生成动画...")
                } else {
                    AnimationScreen(
                            onBack = { viewModel.backToTracking() },
                            animationUrl = animationUrl
                    )
                }
            }
            is AppState.History ->
                    HistoryScreen(
                            onBack = { viewModel.backToTracking() },
                    )
            is AppState.Knowledge ->
                    KnowledgeScreen(
                            state = state,
                            showModules = showModules,
                            onAskQuestion = { viewModel.askQuestion(it) },
                            onBack = { viewModel.backToTracking() },
                            viewModel = viewModel,
                    )
        }

        if (showWelcome) {
            WelcomeDialog(
                    onDismiss = { viewModel.showWelcomeDialog.value = false },
                    viewModel = viewModel
            )
        }

        if (showSettings) {
            SettingsDialog(
                    viewModel = viewModel,
                    onDismiss = { viewModel.showSettingsDialog.value = false }
            )
        }

        // 番茄钟设置弹窗（点击顶部 🍅 时间打开）
        val showPomodoroSettings by viewModel.showPomodoroSettings.collectAsState()
        if (showPomodoroSettings) {
            PomodoroSettingsDialog(
                    viewModel = viewModel,
                    onDismiss = { viewModel.showPomodoroSettings.value = false }
            )
        }
        if (showHistory) {
            HistoryViewScreen(
                viewModel = viewModel,
                onBack = { viewModel.showHistoryScreen.value = false }
            )
        }

        // Feature 9: OCR确认弹窗（确认后回传服务端，立即继续解题流程）
        if (showOcr) {
            CountdownConfirmDialog(
                title = ocrConfirmTitle,
                content = ocrConfirmText,
                onConfirm = {
                    viewModel.showOcrConfirmDialog.value = false
                    viewModel.confirmOcr()
                },
                onCancel = {
                    viewModel.showOcrConfirmDialog.value = false
                    viewModel.confirmOcr()
                    viewModel.backToTracking()
                }
            )
        }

        // Feature 20: GeoGebra图形页面（生成该题的交互式数学图形）
        if (showGeoGebra) {
            GeoGebraScreen(
                url = geogebraUrl,
                onBack = { viewModel.showGeoGebraScreen.value = false }
            )
        }

        // ② 报告时间范围弹窗（生成报告前先询问）
        val showReportRange by viewModel.showReportRangeDialog.collectAsState()
        if (showReportRange) {
            ReportRangeDialog(
                    onSelect = { days ->
                        viewModel.showReportRangeDialog.value = false
                        viewModel.reportDays.value = days
                        if (viewModel.pendingReportType.value == "data") viewModel.loadDataReport(days)
                        else viewModel.loadAiReport(days)
                    },
                    onDismiss = { viewModel.showReportRangeDialog.value = false }
            )
        }
    }
}


private fun resolveCameraSelector(provider: ProcessCameraProvider): CameraSelector? {
    // 优先后置摄像头；没有则回退前置（模拟器只有前置 Webcam 时也能用）；再不行选任意可用摄像头
    if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) {
        return CameraSelector.DEFAULT_BACK_CAMERA
    }
    if (provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)) {
        Log.w("CameraPreview", "未找到后置摄像头，回退到前置摄像头")
        return CameraSelector.DEFAULT_FRONT_CAMERA
    }
    val infos = provider.availableCameraInfos
    if (infos.isNotEmpty()) {
        Log.w("CameraPreview", "回退到任意可用摄像头，共${infos.size}个")
        // 不依赖镜头朝向：直接取第一个可用摄像头
        return CameraSelector.Builder()
            .addCameraFilter { candidates -> candidates.take(1) }
            .build()
    }
    return null
}

@Composable
fun CameraPreviewView(
    modifier: Modifier = Modifier,
    gestureRecognizer: HandGestureRecognizer? = null,
    onImageCaptureReady: ((ImageCapture) -> Unit)? = null,
    onCameraReady: (() -> Unit)? = null,
    rebindKey: Int = 0,
) {
    val context = LocalContext.current
    val lifecycleOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current

    val hasPermission =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED

    if (!hasPermission) {
        Box(modifier = modifier.background(Color.Black))
        return
    }

    val cameraState = remember {
        mutableStateOf<ProcessCameraProvider?>(null)
    }

    val imageCaptureRef = remember { mutableStateOf<ImageCapture?>(null) }
    val isBound = remember { mutableStateOf(false) }
    val previewView = remember { PreviewView(context) }

    LaunchedEffect(Unit) {
        var retryCount = 0
        val maxRetries = 2
        while (retryCount < maxRetries && cameraState.value == null) {
            try {
                val cameraProvider = withContext(Dispatchers.IO) {
                    val future = ProcessCameraProvider.getInstance(context)
                    try {
                        future.get(10, java.util.concurrent.TimeUnit.SECONDS)
                    } catch (e: java.util.concurrent.TimeoutException) {
                        Log.w("CameraPreview", "获取 CameraProvider 超时 (第${retryCount + 1}次)")
                        null
                    }
                }
                if (cameraProvider != null) {
                    cameraState.value = cameraProvider
                    Log.d("CameraPreview", "CameraProvider 获取成功")
                    // 诊断：打印可用摄像头的朝向信息
                    try {
                        val infos = cameraProvider.availableCameraInfos
                        Log.d("CameraPreview", "可用摄像头数: ${infos.size}")
                        infos.forEach { info ->
                            Log.d("CameraPreview", "  摄像头: lensFacing=${info.lensFacing}, 支持BACK=${cameraProvider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)}, 支持FRONT=${cameraProvider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)}")
                        }
                    } catch (e: Exception) {
                        Log.w("CameraPreview", "枚举摄像头信息失败: ${e.message}")
                    }
                } else {
                    retryCount++
                    if (retryCount < maxRetries) {
                        delay(2000)
                    }
                }
            } catch (e: Exception) {
                Log.e("CameraPreview", "获取 CameraProvider 失败 (第${retryCount + 1}次)", e)
                retryCount++
                if (retryCount < maxRetries) {
                    delay(2000)
                }
            }
        }
        if (cameraState.value == null) {
            Log.e("CameraPreview", "CameraProvider 获取失败，相机功能不可用（可改用相册选图）")
        }
    }

    LaunchedEffect(cameraState.value, lifecycleOwner, rebindKey) {
        val cameraProvider = cameraState.value ?: return@LaunchedEffect
        val currentLifecycle = lifecycleOwner

        cameraProvider.unbindAll()
        isBound.value = false

        val preview = Preview.Builder().build()
        preview.setSurfaceProvider(previewView.surfaceProvider)

        val imageCapture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .build()

        val useCases = mutableListOf<UseCase>(preview, imageCapture)

        if (gestureRecognizer != null) {
            val analyzer = CameraAnalyzer(gestureRecognizer, processIntervalMs = 60)
            val imageAnalysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
                .also {
                    it.setAnalyzer(ContextCompat.getMainExecutor(context), analyzer)
                }
            useCases.add(imageAnalysis)
        }

        try {
            val selector = resolveCameraSelector(cameraProvider)
            if (selector == null) {
                Log.e("CameraPreview", "没有可用摄像头")
                return@LaunchedEffect
            }
            withContext(Dispatchers.Main) {
                cameraProvider.bindToLifecycle(
                    currentLifecycle,
                    selector,
                    *useCases.toTypedArray()
                )
            }

            imageCaptureRef.value = imageCapture
            onImageCaptureReady?.invoke(imageCapture)
            isBound.value = true
            onCameraReady?.invoke()

            Log.d("CameraPreview", "相机绑定成功")
        } catch (e: Exception) {
            Log.e("CameraPreview", "相机绑定失败", e)
            // 绑定失败时触发重绑，并等待下一次机会
            isBound.value = false
        }
    }

    LaunchedEffect(gestureRecognizer) {
        val cameraProvider = cameraState.value ?: return@LaunchedEffect
        if (!isBound.value) return@LaunchedEffect
        
        cameraProvider.unbindAll()
        
        val preview = Preview.Builder().build()
        preview.setSurfaceProvider(previewView.surfaceProvider)
        val imageCapture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .build()
        
        val useCases = mutableListOf<UseCase>(preview, imageCapture)

        if (gestureRecognizer != null) {
            val analyzer = CameraAnalyzer(gestureRecognizer, processIntervalMs = 60)
            val imageAnalysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
                .also {
                    it.setAnalyzer(ContextCompat.getMainExecutor(context), analyzer)
                }
            useCases.add(imageAnalysis)
        }
        
        try {
            val selector = resolveCameraSelector(cameraProvider)
            if (selector == null) {
                Log.e("CameraPreview", "没有可用摄像头")
                return@LaunchedEffect
            }
            cameraProvider.bindToLifecycle(
                lifecycleOwner,
                selector,
                *useCases.toTypedArray()
            )
            imageCaptureRef.value = imageCapture
            onImageCaptureReady?.invoke(imageCapture)
            Log.d("CameraPreview", "Gesture: ${if (gestureRecognizer != null) "enabled" else "disabled"}")
        } catch (e: Exception) {
            Log.e("CameraPreview", "Failed to update gesture analyzer", e)
            isBound.value = false
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            try {
                cameraState.value?.unbindAll()
                Log.d("CameraPreview", "相机已解绑")
            } catch (e: Exception) {
                Log.e("CameraPreview", "解绑相机失败", e)
            }
        }
    }

    AndroidView(
            factory = { previewView },
            modifier = modifier
    )
}


@Composable
fun TrackingOverlay(
        statusText: String,
        pomodoroTime: Int,
        pageCount: Int,
        thinkingTimer: Int,
        fingerCount: Int = 0,
) {
    Box(modifier = Modifier.fillMaxSize()) {
        Column(
                modifier =
                        Modifier.align(Alignment.TopCenter)
                                .padding(16.dp)
                                .background(Color(0xAA000000))
                                .padding(12.dp)
        ) {
            Text(
                    text = statusText,
                    color = tC(Color.White, Color(0xFF16181D)),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(4.dp))

            Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(text = "${formatTime(pomodoroTime)}", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
                Text(text = "已做${pageCount}页", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
            }

            if (thinkingTimer > 0) {
                Text(
                        text = "思考中... ${thinkingTimer}s",
                        color = Color(0xFFFFD700),
                        fontSize = 14.sp,
                        modifier = Modifier.padding(top = 4.dp)
                )
            }
        }

        if (fingerCount > 0) {
            Text(
                    text = "$fingerCount 根手指",
                    color = Color(0xFF00FF00),
                    fontSize = 48.sp,
                    fontWeight = FontWeight.Bold,
                    modifier =
                            Modifier.align(Alignment.Center)
                                    .background(Color(0xAA000000))
                                    .padding(16.dp)
            )
        }

        if (fingerCount > 0) {
            Text(
                    text = "手势命令: 5→解题 | 4→动画 | 3→数据报告 | 2→AI报告 | 1→知识延伸",
                    color = tC(Color.White.copy(alpha = 0.7f), Color(0xFF16181D).copy(alpha = 0.7f)),
                    fontSize = 12.sp,
                    modifier =
                            Modifier.align(Alignment.BottomCenter)
                                    .padding(16.dp)
                                    .background(Color(0x55000000))
                                    .padding(8.dp)
            )
        }
    }
}


@Composable
fun LoginScreen(
    isLoading: Boolean = false,
    error: String = "",
    onLogin: (String, String) -> Unit,
    onRegister: (String, String) -> Unit,
) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var isRegisterMode by remember { mutableStateOf(false) }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(tC(Color(0xFF0A0A1A), Color(0xFFF2F4F8)))
            .padding(32.dp),
        contentAlignment = Alignment.Center
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
            shape = RoundedCornerShape(16.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "📎 学习助手",
                    color = Color(0xFF00D2FF),
                    fontSize = 28.sp,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = if (isRegisterMode) "创建新账号" else "登录",
                    color = tC(Color.Gray, Color(0xFF5C6470)),
                    fontSize = 14.sp
                )

                Spacer(modifier = Modifier.height(24.dp))

                OutlinedTextField(
                    value = username,
                    onValueChange = { username = it },
                    label = { Text("用户名", color = tC(Color.Gray, Color(0xFF5C6470))) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = !isLoading,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = tC(Color.White, Color(0xFF16181D)),
                        unfocusedTextColor = tC(Color.White, Color(0xFF16181D)),
                        focusedBorderColor = Color(0xFF00D2FF),
                        unfocusedBorderColor = tC(Color.Gray, Color(0xFF5C6470)),
                        cursorColor = Color(0xFF00D2FF),
                    )
                )

                Spacer(modifier = Modifier.height(12.dp))

                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("密码", color = tC(Color.Gray, Color(0xFF5C6470))) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = !isLoading,
                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = tC(Color.White, Color(0xFF16181D)),
                        unfocusedTextColor = tC(Color.White, Color(0xFF16181D)),
                        focusedBorderColor = Color(0xFF00D2FF),
                        unfocusedBorderColor = tC(Color.Gray, Color(0xFF5C6470)),
                        cursorColor = Color(0xFF00D2FF),
                    )
                )

                if (error.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = error,
                        color = Color(0xFFFF5722),
                        fontSize = 13.sp
                    )
                }

                Spacer(modifier = Modifier.height(20.dp))

                Button(
                    onClick = {
                        if (username.isNotBlank() && password.isNotBlank()) {
                            if (isRegisterMode) {
                                onRegister(username.trim(), password)
                            } else {
                                onLogin(username.trim(), password)
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    enabled = !isLoading && username.isNotBlank() && password.isNotBlank(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    if (isLoading) {
                        CircularProgressIndicator(
                            color = tC(Color.White, Color(0xFF16181D)),
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp
                        )
                    } else {
                        Text(
                            text = if (isRegisterMode) "注册" else "登录",
                            color = Color.Black,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }

                Spacer(modifier = Modifier.height(12.dp))

                TextButton(onClick = { isRegisterMode = !isRegisterMode }) {
                    Text(
                        text = if (isRegisterMode) {
                            "已有账号？点击登录"
                        } else {
                            "没有账号？点击注册"
                        },
                        color = Color(0xFF00D2FF),
                        fontSize = 14.sp
                    )
                }
            }
        }
    }
}


@Composable
fun SolvingScreen(
        solveState: AppState.Solving,
        showModules: Map<String, Boolean> = emptyMap(),
        onAskQuestion: (String) -> Unit,
        onBack: () -> Unit,
        isAskingQuestion: Boolean = false,
        viewModel: MainViewModel? = null,
) {
    Box(modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))) {
        val solvingFontSize by (viewModel?.fontSize ?: MutableStateFlow(18f)).collectAsState()
        val solveProgress by (viewModel?.solveProgress ?: MutableStateFlow("")).collectAsState()
        var questionInput by remember { mutableStateOf("") }
        Column(modifier = Modifier.fillMaxSize()) {
            // ③ 顶部栏固定在上方（返回/标题/阶段进度），不随内容滚动
            Row(
                    modifier = Modifier
                            .fillMaxWidth()
                            .background(tC(Color(0xFF16213E), Color(0xFFFFFFFF)))
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(onClick = onBack) { Text("← 返回") }
                Text(
                        text =
                                when (solveState.stage) {
                                    SolveStage.UPLOADING -> "上传中..."
                                    SolveStage.ANALYZING -> "分析中..."
                                    SolveStage.DISPLAY_STEPS -> "解题思路"
                                    SolveStage.DISPLAY_FULL -> "完整解析"
                                    SolveStage.DISPLAY_MINDMAP -> "思维导图"
                                    SolveStage.INTERACTIVE -> "互动问答"
                                    SolveStage.COMPLETED -> "解答完成"
                                },
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.weight(1f),
                        textAlign = TextAlign.Center
                )
                // ① 右上角阶段进度提示（LaTeX生成/渲染时用户能看到AI正在做什么）
                Text(
                        text = solveProgress,
                        color = Color(0xFF00D2FF),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold
                )
            }

            // ① 多题切换器（分题>1时显示）
            val multiCount by (viewModel?.multiQuestionCount ?: MutableStateFlow(0)).collectAsState()
            val multiStates by (viewModel?.multiSolveStates ?: MutableStateFlow(emptyList())).collectAsState()
            val curQIndex by (viewModel?.currentQuestionIndex ?: MutableStateFlow(0)).collectAsState()
            if (multiCount > 1) {
                Row(
                        modifier = Modifier
                                .fillMaxWidth()
                                .background(tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                                .horizontalScroll(rememberScrollState())
                                .padding(horizontal = 8.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    (0 until multiCount).forEach { idx ->
                        FilterChip(
                                selected = curQIndex == idx,
                                onClick = {
                                    viewModel?.switchQuestion(idx)
                                },
                                label = { Text("题目 ${idx + 1}", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = Color(0xFF00D2FF)
                                )
                        )
                    }
                }
            }

            // 可滚动内容区
            Column(
                    modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .verticalScroll(rememberScrollState())
                            .padding(16.dp)
            ) {
            Spacer(modifier = Modifier.height(16.dp))

            if (solveState.stage == SolveStage.UPLOADING || solveState.stage == SolveStage.ANALYZING) {
                Box(
                        modifier = Modifier.fillMaxWidth().height(100.dp),
                        contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = Color(0xFF00D2FF))
                    Text(
                            text = "正在分析题目...",
                            color = tC(Color.White, Color(0xFF16181D)),
                            modifier = Modifier.padding(top = 80.dp)
                    )
                }
            }

            // ⑩ 题目分析标签（年级/学科/难度/知识点/易错点/难点）显示在解题内容顶端
            val qInfo by (viewModel?.solveQuestionInfo ?: MutableStateFlow(emptyMap())).collectAsState()
            if (qInfo.isNotEmpty()) {
                QuestionInfoTags(qInfo)
            }

            if (solveState.solutionSteps.isNotEmpty()) {
                SolutionCard(
                        title = "💡 解题思路",
                        content = solveState.solutionSteps,
                        color = Color(0xFF00D2FF),
                        fontSize = solvingFontSize,
                        initiallyCollapsed = !showModules.getOrDefault("solution_steps", true),
                        streaming = solveState.stepsStreaming
                )
            }

            // 十一 思考模式：完整解析的思考过程显示在其上方
            val thinkingText by (viewModel?.solveThinkingText ?: MutableStateFlow("")).collectAsState()
            if (thinkingText.isNotEmpty() && solveState.fullSolution.isNotEmpty()) {
                var showThinking by remember { mutableStateOf(true) }
                Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        colors = CardDefaults.cardColors(
                                containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
                        )
                ) {
                    Column(
                            modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { showThinking = !showThinking }
                                    .padding(12.dp)
                    ) {
                        Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                    "🧠 思考过程",
                                    color = Color(0xFFFFB74D),
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Bold
                            )
                            Text(
                                    if (showThinking) "▲ 收起" else "▼ 展开",
                                    color = Color(0xFF00D2FF),
                                    fontSize = 12.sp
                            )
                        }
                        if (showThinking) {
                            Spacer(modifier = Modifier.height(6.dp))
                            ColorText(
                                    thinkingText,
                                    color = tC(Color(0xFFB0BEC5), Color(0xFF546E7A)),
                                    fontSize = 13.sp,
                                    lineHeight = 18.sp
                            )
                        }
                    }
                }
            }

            if (solveState.fullSolution.isNotEmpty()) {
                SolutionCard(
                        title = "📝 完整解析",
                        // ② LaTeX未渲染完成时显示“图形正在生成”占位
                        content = replaceLatexWithPlaceholder(solveState.fullSolution),
                        color = Color(0xFF7B2FBE),
                        fontSize = solvingFontSize,
                        initiallyCollapsed = !showModules.getOrDefault("full_solution", true),
                        streaming = solveState.solutionStreaming
                )
            }

            if (solveState.mindMap.isNotEmpty()) {
                SolutionCard(
                        title = "🗺️ 思维导图",
                        content = solveState.mindMap,
                        color = Color(0xFF00C853),
                        fontSize = solvingFontSize,
                        initiallyCollapsed = !showModules.getOrDefault("mind_map", true),
                        streaming = solveState.mindmapStreaming
                )
            }

            // ④ 搜题结果：按钮展开 + Markdown 展示
            val searchResults by (viewModel?.solveSearchResults ?: MutableStateFlow(emptyList())).collectAsState()
            if (searchResults.isNotEmpty()) {
                var showSearch by remember { mutableStateOf(false) }
                Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        colors = CardDefaults.cardColors(containerColor = Color(0xFF1B3A4B))
                ) {
                    Column(
                            modifier =
                                    Modifier.fillMaxWidth()
                                            .clickable { showSearch = !showSearch }
                                            .padding(12.dp)
                    ) {
                        Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                    "🔍 搜题结果（${searchResults.size} 条）",
                                    color = Color(0xFFFFB74D),
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.Bold
                            )
                            Text(
                                    if (showSearch) "▲ 收起" else "▼ 展开",
                                    color = Color(0xFF00D2FF),
                                    fontSize = 12.sp
                            )
                        }
                        if (showSearch) {
                            searchResults.forEachIndexed { idx, item ->
                                val md = buildString {
                                    append("### 题目 ${idx + 1}")
                                    item["subject"]?.takeIf { it.isNotEmpty() }?.let { append("　**学科**：$it") }
                                    item["grade"]?.takeIf { it.isNotEmpty() }?.let { append("　**学段**：$it") }
                                    item["point_name"]?.takeIf { it.isNotEmpty() }?.let { append("　**知识点**：$it") }
                                    item["question_md"]?.takeIf { it.isNotEmpty() }?.let { append("\n\n$it") }
                                    item["hint_md"]?.takeIf { it.isNotEmpty() }?.let { append("\n\n**解析**：\n$it") }
                                    item["answer_md"]?.takeIf { it.isNotEmpty() }?.let { append("\n\n**答案**：\n$it") }
                                }
                                MarkdownView(
                                        content = md,
                                        fontSize = 13f,
                                        modifier = Modifier.padding(vertical = 6.dp)
                                )
                                Divider(color = tC(Color.White.copy(alpha = 0.15f), Color(0xFF16181D).copy(alpha = 0.15f)))
                            }
                        }
                    }
                }
            }
            
            // 互动问答区：预测问题 + 对话记录 + 输入框（解题进入互动阶段后显示）
            if ((solveState.stage == SolveStage.INTERACTIVE || solveState.stage == SolveStage.COMPLETED) && showModules.getOrDefault("suggested_questions", true)) {
                if (solveState.suggestedQA.isNotEmpty() || solveState.suggestedQuestions.isNotEmpty()) {
                    Text(
                            text = "💬 您可能还想问：",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(top = 16.dp, bottom = 8.dp)
                    )

                    if (solveState.suggestedQA.isNotEmpty()) {
                        // 新版：AI预测问题自带答案，点击展开/收起
                        solveState.suggestedQA.forEach { item ->
                            var expanded by remember(item.question) { mutableStateOf(false) }
                            Card(
                                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                    colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                            ) {
                                Column(
                                        modifier =
                                                Modifier.fillMaxWidth()
                                                        .clickable { expanded = !expanded }
                                                        .padding(12.dp)
                                ) {
                                    Row(
                                            modifier = Modifier.fillMaxWidth(),
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                            verticalAlignment = Alignment.CenterVertically
                                    ) {
                                        Text(
                                                item.question,
                                                color = tC(Color.White, Color(0xFF16181D)),
                                                fontSize = 14.sp,
                                                modifier = Modifier.weight(1f)
                                        )
                                        Text(
                                                if (expanded) "▲" else "▼",
                                                color = Color(0xFF00D2FF),
                                                fontSize = 12.sp
                                        )
                                    }
                                    if (expanded && item.answer.isNotEmpty()) {
                                        Text(
                                                text = "📝 ${item.answer}",
                                                color = tC(Color(0xFFB0BEC5), Color(0xFF546E7A)),
                                                fontSize = 14.sp,
                                                modifier = Modifier.padding(top = 8.dp)
                                        )
                                        TextButton(
                                                onClick = { onAskQuestion(item.question) },
                                                enabled = !isAskingQuestion
                                        ) {
                                            Text("追问", color = Color(0xFF00D2FF), fontSize = 12.sp)
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        solveState.suggestedQuestions.forEach { question ->
                            Button(
                                    onClick = { onAskQuestion(question) },
                                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))),
                                    enabled = !isAskingQuestion
                            ) { Text(question, color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp) }
                        }
                    }
                }

                // 与AI的对话记录（你问 → AI答，气泡样式）
                if (solveState.qaList.isNotEmpty()) {
                    Text(
                            text = "💬 对话记录：",
                            color = tC(Color.White.copy(alpha = 0.7f), Color(0xFF16181D).copy(alpha = 0.7f)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(top = 12.dp, bottom = 6.dp)
                    )
                    solveState.qaList.forEach { item ->
                        // 用户问题（右对齐蓝色气泡）
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                            Surface(
                                    color = Color(0xFF1565C0),
                                    shape = RoundedCornerShape(12.dp, 12.dp, 4.dp, 12.dp),
                                    modifier = Modifier.fillMaxWidth(0.85f).padding(vertical = 4.dp)
                            ) {
                                Text(
                                        item.question,
                                        color = Color.White,
                                        fontSize = 14.sp,
                                        modifier = Modifier.padding(10.dp)
                                )
                            }
                        }
                        // AI回答（左对齐灰色气泡）；① 用Markdown渲染（含LaTeX/图片/格式），② 未渲染完的图形显示占位
                        if (item.answer.isNotEmpty()) {
                            Row(modifier = Modifier.fillMaxWidth()) {
                                Surface(
                                        color = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                        shape = RoundedCornerShape(12.dp, 12.dp, 12.dp, 4.dp),
                                        modifier = Modifier.fillMaxWidth(0.92f).padding(vertical = 4.dp)
                                ) {
                                    val ansContent =
                                            if (item.rendered) item.answer else replaceLatexWithPlaceholder(item.answer)
                                    if (item.rendered || shouldRenderMarkdown(ansContent)) {
                                        MarkdownView(
                                                content = ansContent,
                                                fontSize = 13f,
                                                modifier = Modifier.padding(10.dp)
                                        )
                                    } else {
                                        ColorText(
                                                ansContent,
                                                color = tC(Color(0xFFE0E0E0), Color(0xFF3A3F47)),
                                                fontSize = 13.sp,
                                                modifier = Modifier.padding(10.dp)
                                        )
                                    }
                                }
                            }
                        }
                    }
                }

                // AI正在回答的气泡（流式打字机；② 未渲染完的图形显示占位）
                if (solveState.isAnswering) {
                    Row(modifier = Modifier.fillMaxWidth()) {
                        Surface(
                                color = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                shape = RoundedCornerShape(12.dp, 12.dp, 12.dp, 4.dp),
                                modifier = Modifier.fillMaxWidth(0.92f).padding(vertical = 4.dp)
                        ) {
                            // ① 流式中只显示纯文本（Markdown等生成完再渲染，避免卡顿）
                            ColorText(
                                    if (solveState.pendingAnswer.isNotEmpty()) solveState.pendingAnswer else "正在思考...",
                                    color = tC(Color(0xFFE0E0E0), Color(0xFF3A3F47)),
                                    fontSize = 13.sp,
                                    modifier = Modifier.padding(10.dp)
                            )
                        }
                    }
                }

                // 输入框：与AI基于本题继续对话（替代原来的固定快捷按钮）
                Spacer(modifier = Modifier.height(12.dp))
                Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedTextField(
                            value = questionInput,
                            onValueChange = { questionInput = it },
                            modifier = Modifier.weight(1f).height(46.dp),
                            placeholder = { Text("输入你的问题，与AI继续对话...", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp) },
                            textStyle = TextStyle(color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp),
                            colors = OutlinedTextFieldDefaults.colors(
                                    focusedBorderColor = Color(0xFF00D2FF),
                                    unfocusedBorderColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                    cursorColor = Color(0xFF00D2FF)
                            ),
                            singleLine = true,
                            enabled = !isAskingQuestion
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Button(
                            onClick = {
                                val q = questionInput.trim()
                                if (q.isNotEmpty() && !isAskingQuestion) {
                                    questionInput = ""
                                    onAskQuestion(q)
                                }
                            },
                            enabled = !isAskingQuestion,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
                    ) { Text("发送", color = Color.Black, fontSize = 14.sp, fontWeight = FontWeight.Bold) }
                }

                // Feature 20: GeoGebra（仅数学题可用；点击生成该题的交互式数学图形）
                val isMath = solveState.subject.contains("数学") || solveState.subject.contains("几何") ||
                        solveState.subject.contains("代数") || solveState.subject.contains("函数")
                if (isMath) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                            onClick = { viewModel?.generateGeoGebra() },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE65100)),
                            enabled = !isAskingQuestion
                    ) { Text("📐 查看GeoGebra图形", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp) }
                }
            }

            // 掌握程度：解答完成后显示标题 + 三个选项横向排布；点击后保留区块并高亮所选，可重新选择
            val showMasteryBtn by (viewModel?.masteryVisible ?: MutableStateFlow(false)).collectAsState()
            val masterySaved by (viewModel?.masterySaved ?: MutableStateFlow(false)).collectAsState()
            val masteryLevel by (viewModel?.masteryLevel ?: MutableStateFlow("")).collectAsState()
            if (showMasteryBtn && solveState.stage == SolveStage.COMPLETED) {
                Text(
                        text = "📊 掌握程度",
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 16.dp, bottom = 8.dp)
                )
                if (masterySaved && masteryLevel.isNotEmpty()) {
                    Text(
                            text = "✅ 已记录：$masteryLevel（点击可重新选择）",
                            color = Color(0xFF4CAF50),
                            fontSize = 13.sp,
                            modifier = Modifier.padding(bottom = 6.dp)
                    )
                }
                Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Button(
                            onClick = { viewModel?.saveMastery("completely_mastered") },
                            modifier = Modifier.weight(1f).height(44.dp),
                            colors = ButtonDefaults.buttonColors(
                                    containerColor = if (masteryLevel == "完全掌握") Color(0xFF2E7D32) else Color(0xFF4CAF50)
                            )
                    ) { Text("✅ 完全掌握", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold) }
                    Button(
                            onClick = { viewModel?.saveMastery("partially_mastered") },
                            modifier = Modifier.weight(1f).height(44.dp),
                            colors = ButtonDefaults.buttonColors(
                                    containerColor = if (masteryLevel == "部分掌握") Color(0xFFE65100) else Color(0xFFFF9800)
                            )
                    ) { Text("⚠️ 部分掌握", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold) }
                    Button(
                            onClick = { viewModel?.saveMastery("not_mastered") },
                            modifier = Modifier.weight(1f).height(44.dp),
                            colors = ButtonDefaults.buttonColors(
                                    containerColor = if (masteryLevel == "完全没掌握") Color(0xFFC62828) else Color(0xFFF44336)
                            )
                    ) { Text("❌ 完全没掌握", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold) }
                }
            }

            // ④ 导出（截图/PDF/Word）— 导出解题思路+完整解析+思维导图
            val exportContent = buildString {
                append("# 学习助手解题结果\n\n")
                if (solveState.solutionSteps.isNotEmpty()) append("## 解题思路\n\n${solveState.solutionSteps}\n\n")
                if (solveState.fullSolution.isNotEmpty()) append("## 完整解析\n\n${solveState.fullSolution}\n\n")
                if (solveState.mindMap.isNotEmpty()) append("## 思维导图\n\n${solveState.mindMap}\n")
            }
            if (exportContent.isNotBlank() && (solveState.stage == SolveStage.INTERACTIVE || solveState.stage == SolveStage.COMPLETED)) {
                ExportActions(viewModel = viewModel, title = "学习助手解题结果", content = exportContent, allowPdf = true)
            }

            Spacer(modifier = Modifier.height(32.dp))
            }
        }

        // ③ GeoGebra生成时锁屏等待AI回复（② 追问已流式输出，不再锁屏）
        val isGeneratingGeoGebra by (viewModel?.isGeneratingGeoGebra ?: MutableStateFlow(false)).collectAsState()
        if (isGeneratingGeoGebra) {
            LoadingOverlay("正在生成数学图形...")
        }
    }
}

// ==================== ⑩ 题目分析标签（年级/学科/难度/知识点/易错点/难点） ====================
@Composable
private fun InfoTag(text: String, bg: Color) {
    Surface(
            color = bg,
            shape = RoundedCornerShape(6.dp),
            modifier = Modifier.padding(end = 2.dp)
    ) {
        Text(
                text,
                color = Color.White,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
        )
    }
}

@Composable
fun QuestionInfoTags(qInfo: Map<String, String>) {
    val gradeV = qInfo["grade"].orEmpty().trim()
    val subjectV = qInfo["subject"].orEmpty().trim()
    val diffV = qInfo["difficulty"].orEmpty().trim()
    val kpV = qInfo["knowledge_points"].orEmpty().trim()
    val emV = qInfo["easy_mistakes"].orEmpty().trim()
    val dpV = qInfo["difficult_points"].orEmpty().trim()
    if (gradeV.isEmpty() && subjectV.isEmpty() && diffV.isEmpty() &&
            kpV.isEmpty() && emV.isEmpty() && dpV.isEmpty()
    ) return

    val diffColor =
            when (diffV) {
                "易", "较易" -> Color(0xFF4CAF50)
                "中" -> Color(0xFFFF9800)
                "较难", "难" -> Color(0xFFF44336)
                else -> Color(0xFF607D8B)
            }

    Card(
            modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
            colors = CardDefaults.cardColors(
                    containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
            ),
            shape = RoundedCornerShape(10.dp)
    ) {
        Column(
                modifier = Modifier.fillMaxWidth().padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp)
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                if (gradeV.isNotEmpty()) InfoTag("🎓 $gradeV", Color(0xFF7B2FBE))
                if (subjectV.isNotEmpty()) InfoTag("📖 $subjectV", Color(0xFF2196F3))
                if (diffV.isNotEmpty()) InfoTag("📊 难度：$diffV", diffColor)
            }
            if (kpV.isNotEmpty()) {
                Text(
                        text = "🔖 知识点：$kpV",
                        color = Color(0xFF00D2FF),
                        fontSize = 12.sp
                )
            }
            if (emV.isNotEmpty()) {
                Text(
                        text = "⚠️ 易错点：$emV",
                        color = Color(0xFFE65100),
                        fontSize = 12.sp
                )
            }
            if (dpV.isNotEmpty()) {
                Text(
                        text = "🚧 难点：$dpV",
                        color = Color(0xFFC62828),
                        fontSize = 12.sp
                )
            }
        }
    }
}

@Composable
fun SolutionCard(
    title: String,
    content: String,
    color: Color,
    fontSize: Float = 18f,
    initiallyCollapsed: Boolean = false,
    streaming: Boolean = false,
) {
    var expanded by remember(initiallyCollapsed) { mutableStateOf(!initiallyCollapsed) }
    Card(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF)))
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                    modifier = Modifier.fillMaxWidth().clickable { expanded = !expanded },
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                        text = title,
                        color = color,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                )
                Text(
                        text = if (expanded) "▲" else "▼",
                        color = color.copy(alpha = 0.7f),
                        fontSize = 14.sp
                )
            }
            if (expanded) {
                Spacer(modifier = Modifier.height(8.dp))
                if (streaming) {
                    // 流式输出中：先显示纯文本（打字机效果），全部完成后再做Markdown渲染，避免卡顿
                    ColorText(
                            text = content,
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = fontSize.sp,
                            lineHeight = (fontSize * 1.4f).sp
                    )
                } else {
                    MarkdownView(content = content, modifier = Modifier.fillMaxWidth(), fontSize = fontSize)
                }
            }
        }
    }
}

@Composable
fun ReportScreen(
        report: AppState.Report,
        onBack: () -> Unit,
        reportUrl: String = "",
        reportText: String = "",
        reportDays: Int = 7,
        onSelectDays: ((Int) -> Unit)? = null,
) {
    Column(modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))) {
        // Feature 14: 固定返回按钮在顶部
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(onClick = onBack) { Text("← 返回") }
                Text("学情报告", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(modifier = Modifier.width(60.dp))  // 平衡布局
            }
        }

        // 统计周期选择
        if (onSelectDays != null) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                listOf(7 to "近7天", 30 to "近30天", 90 to "近90天", 0 to "全部").forEach { (days, label) ->
                    FilterChip(
                            selected = reportDays == days,
                            onClick = { onSelectDays(days) },
                            label = { Text(label, fontSize = 12.sp) },
                            colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = Color(0xFF00D2FF),
                                    selectedLabelColor = Color.Black
                            )
                    )
                }
            }
        }

        if (reportUrl.isNotEmpty()) {
            AndroidView(
                    factory = { ctx ->
                        android.webkit.WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            // 禁止缩放但允许滚动（修复报告页面划不动的问题）
                            settings.builtInZoomControls = false
                            settings.displayZoomControls = false
                            settings.setSupportZoom(false)
                            setInitialScale(50)
                            loadUrl(reportUrl)
                        }
                    },
                    modifier = Modifier.fillMaxSize()
            )
        } else if (reportText.isNotEmpty()) {
            if (report.streaming) {
                // 流式期间只显示纯文本（打字机效果），完成后才做Markdown渲染，避免卡顿
                Column(
                        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)
                ) {
                    Text(
                            text = reportText,
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 16.sp,
                            lineHeight = 24.sp
                    )
                }
            } else {
                MarkdownView(content = reportText, modifier = Modifier.fillMaxSize().padding(16.dp))
            }
        } else if (report.isLoading) {
            LoadingOverlay("正在生成报告...")
        }
    }
}

@Composable
fun KnowledgeScreen(
        state: AppState.Knowledge,
        showModules: Map<String, Boolean> = emptyMap(),
        onAskQuestion: (String) -> Unit,
        onBack: () -> Unit,
        viewModel: MainViewModel? = null,
) {
    Column(
            modifier =
                    Modifier.fillMaxSize()
                            .background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))
    ) {
        val knowledgeFontSize by (viewModel?.fontSize ?: MutableStateFlow(18f)).collectAsState()
        // Feature 14: 固定返回按钮在顶部
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(onClick = onBack) { Text("← 返回") }
                Text("📎 知识延伸", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Spacer(modifier = Modifier.width(60.dp))
            }
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
        ) {
            if (state.summary.isEmpty() && state.extension.isEmpty()) {
                LoadingOverlay("正在分析内容...")
            }

            if (state.summary.isNotEmpty()) {
                SolutionCard(
                        title = "📝 知识点总结",
                        content = state.summary,
                        color = Color(0xFF00D2FF),
                        fontSize = knowledgeFontSize,
                        initiallyCollapsed = !showModules.getOrDefault("solution_steps", true),
                        streaming = state.summaryStreaming
                )
            }

            if (state.extension.isNotEmpty()) {
                SolutionCard(
                        title = "🚀 知识拓展",
                        content = state.extension,
                        color = Color(0xFF7B2FBE),
                        fontSize = knowledgeFontSize,
                        initiallyCollapsed = !showModules.getOrDefault("extension", true),
                        streaming = state.extensionStreaming
                )
            }

            // ② 相似题推荐（3个，折叠展示题目+答案）
            if (state.similarQuestions.isNotEmpty()) {
                Text(
                        text = "🔗 相似题推荐：",
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 16.dp, bottom = 8.dp)
                )
                state.similarQuestions.forEach { item ->
                    var expanded by remember(item.question) { mutableStateOf(false) }
                    Card(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                    ) {
                        Column(
                                modifier = Modifier.fillMaxWidth().clickable { expanded = !expanded }.padding(12.dp)
                        ) {
                            Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(item.question, color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp, modifier = Modifier.weight(1f))
                                Text(if (expanded) "▲" else "▼", color = Color(0xFF00D2FF), fontSize = 12.sp)
                            }
                            if (expanded && item.answer.isNotEmpty()) {
                                Spacer(modifier = Modifier.height(6.dp))
                                Text("📝 ${item.answer}", color = tC(Color(0xFFB0BEC5), Color(0xFF546E7A)), fontSize = 13.sp)
                            }
                        }
                    }
                }
            }

            // ② 延伸思考（折叠块，带答案 + 问追问，同AI解答样式）
            if (state.suggestedQuestions.isNotEmpty() && showModules.getOrDefault("suggested_questions", true)) {
                Text(
                        text = "💬 延伸思考：",
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 16.dp, bottom = 8.dp)
                )
                state.suggestedQuestions.forEach { item ->
                    var expanded by remember(item.question) { mutableStateOf(false) }
                    Card(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF)))
                    ) {
                        Column(
                                modifier = Modifier.fillMaxWidth().clickable { expanded = !expanded }.padding(12.dp)
                        ) {
                            Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("❓ ${item.question}", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp, modifier = Modifier.weight(1f))
                                Text(if (expanded) "▲" else "▼", color = Color(0xFF00D2FF), fontSize = 12.sp)
                            }
                            if (expanded) {
                                if (item.answer.isNotEmpty()) {
                                    Spacer(modifier = Modifier.height(6.dp))
                                    Text("💡 ${item.answer}", color = tC(Color(0xFFE0E0E0), Color(0xFF3A3F47)), fontSize = 13.sp)
                                }
                                TextButton(
                                        onClick = { onAskQuestion(item.question) }
                                ) {
                                    Text("追问", color = Color(0xFF00D2FF), fontSize = 12.sp)
                                }
                            }
                        }
                    }
                }
            }
            
            // ④ 导出（截图/PDF/Word）— 知识延伸内容
            val exportContent = buildString {
                if (state.summary.isNotEmpty()) append("## 知识点总结\n\n${state.summary}\n\n")
                if (state.extension.isNotEmpty()) append("## 知识拓展\n\n${state.extension}\n\n")
                if (state.similarQuestions.isNotEmpty()) {
                    append("## 相似题推荐\n\n")
                    state.similarQuestions.forEach { append("- ${it.question}\n") }
                }
            }
            if (exportContent.isNotBlank()) {
                ExportActions(viewModel = viewModel, title = "知识延伸", content = exportContent, allowPdf = true)
            }

            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}

@Composable
fun AnimationScreen(onBack: () -> Unit, animationUrl: String = "") {
    val context = LocalContext.current

    if (animationUrl.isEmpty()) {
        Box(
                modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8))),
                contentAlignment = Alignment.Center
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("🎬 AI动画", color = tC(Color.White, Color(0xFF16181D)), fontSize = 24.sp)
                Spacer(modifier = Modifier.height(16.dp))
                Text("正在加载动画...", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 16.sp)
                Spacer(modifier = Modifier.height(16.dp))
                Button(onClick = onBack) { Text("返回") }
            }
        }
        return
    }

    Column(modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))) {
        // Feature 14: 固定返回按钮
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(onClick = onBack) { Text("← 返回") }
                Text("🎬 AI动画", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Spacer(modifier = Modifier.width(60.dp))
            }
        }

        // WebView引用，供缩放按钮控制（JS缩放，避免页面user-scalable=no导致zoomIn/zoomOut无效）
        var webViewRef by remember { mutableStateOf<android.webkit.WebView?>(null) }
        var zoomScale by remember { mutableStateOf(1f) }

        Box(modifier = Modifier.weight(1f)) {
            AndroidView(
                    factory = { ctx ->
                        android.webkit.WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.allowFileAccess = true
                            settings.domStorageEnabled = true
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            settings.builtInZoomControls = false
                            settings.displayZoomControls = false
                            settings.setSupportZoom(false)
                            loadUrl(animationUrl)
                            webViewRef = this
                        }
                    },
                    modifier = Modifier.fillMaxSize()
            )
        }

        // 缩放控制条（页面过大看不到边缘时使用）
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(
                        onClick = {
                            zoomScale = (zoomScale * 0.8f).coerceAtLeast(0.5f)
                            webViewRef?.evaluateJavascript("document.body.style.zoom = '${zoomScale}';", null)
                        },
                        modifier = Modifier.size(width = 64.dp, height = 40.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("−", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp) }
                Text("缩放", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp))
                Button(
                        onClick = {
                            zoomScale = (zoomScale * 1.25f).coerceAtMost(5f)
                            webViewRef?.evaluateJavascript("document.body.style.zoom = '${zoomScale}';", null)
                        },
                        modifier = Modifier.size(width = 64.dp, height = 40.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("+", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp) }
                Spacer(modifier = Modifier.width(12.dp))
                TextButton(onClick = {
                    zoomScale = 1f
                    webViewRef?.evaluateJavascript("document.body.style.zoom = '1';", null)
                    webViewRef?.reload()
                }) {
                    Text("🔄 适应", color = Color(0xFF00D2FF), fontSize = 13.sp)
                }
            }
        }
    }
}

@Composable
fun HistoryScreen(onBack: () -> Unit) {
    Box(
            modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8))),
            contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("📎 历史记录", color = Color.White, fontSize = 24.sp)
            Spacer(modifier = Modifier.height(16.dp))
            Text("请在设置中打开历史记录", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 14.sp)
            Spacer(modifier = Modifier.height(16.dp))
            Button(onClick = onBack) { Text("返回") }
        }
    }
}

fun formatTime(seconds: Int): String {
    val mins = seconds / 60
    val secs = seconds % 60
    return "%02d:%02d".format(mins, secs)
}

fun formatStopwatch(seconds: Int): String {
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    val s = seconds % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%02d:%02d".format(m, s)
}

/**
 * 清洗OCR/历史文本中的HTML标签（<div>、<img>等），保留可读内容
 */
private fun cleanHtmlText(text: String): String {
    if (text.isEmpty()) return text
    var t = text.replace(Regex("<[^>]*>"), " ")
    // 压缩空白
    t = t.replace(Regex("[ \\t]+"), " ")
    t = t.replace(Regex("\\n{3,}"), "\\n\\n")
    return t.trim()
}

/**
 * 生成纯文本预览：去除HTML标签与Markdown标记，截断到指定长度
 */
private fun plainPreview(text: String, max: Int = 30): String {
    var t = cleanHtmlText(text)
    // 去掉Markdown标记：标题、粗体/斜体、行内代码、公式分隔符
    t = t.replace(Regex("^#{1,6}\\s*", RegexOption.MULTILINE), "")
    t = t.replace("**", "").replace("`", "")
    t = t.replace(Regex("\\$\\$?" ), "")
    t = t.replace(Regex("\\s+"), " ").trim()
    return if (t.length > max) t.take(max) + "…" else t
}

/**
 * 追加流式chunk到StringBuilder（兼容累积与增量两种下发方式）。
 * 服务端旧版本会下发累积全文（每个chunk都是从开头到当前的完整文本），
 * 直接append会产生“重复一遍多一个字”的错乱；这里按前缀比对只追加新增部分。
 */
private fun appendStreamDelta(sb: StringBuilder, chunk: String) {
    if (chunk.isEmpty()) return
    val existing = sb.toString()
    when {
        existing.isEmpty() -> sb.append(chunk)
        chunk.startsWith(existing) -> {
            if (chunk.length > existing.length) {
                sb.append(chunk.substring(existing.length))
            }
        }
        else -> sb.append(chunk)
    }
}

/**
 * ② LaTeX图形未渲染完成时，把 ```latex ... ``` 代码块替换为“图形正在生成”占位提示。
 * 服务端渲染完成后（solution_rendered / answer_rendered）内容中不再包含 ```latex，自动恢复正常显示。
 */
private fun replaceLatexWithPlaceholder(text: String): String {
    if (!text.contains("```")) return text
    return text.replace(Regex("```[\\s\\S]*?```"), "（图形正在生成...）")
}

/**
 * ① 判断回答是否需要用Markdown渲染（含图片/LaTeX/格式标记）
 */
private fun shouldRenderMarkdown(text: String): Boolean {
    return text.contains("```") || text.contains("![") || text.contains("**") ||
            text.contains("##") || text.contains("\n-") || text.contains("$$") ||
            text.contains("\\\\(") || text.contains("\n1.")
}

@Composable
fun MarkdownView(
    content: String,
    modifier: Modifier = Modifier,
    fontSize: Float = 18f,
    onRendered: (() -> Unit)? = null
) {
    val context = LocalContext.current
    // ⑦ 主题自适应：暗色背景白字 / 亮色背景深字
    val dark = AppDarkTheme
    // ④ 底色与卡片一致：深色卡片 #16213E，浅色卡片 #FFFFFF
    val viewBg = if (dark) "#16213E" else "#FFFFFF"
    val viewText = if (dark) android.graphics.Color.WHITE else 0xFF16181D.toInt()
    val viewDivider = if (dark) "#2D2D44" else "#E0E0E0"

    // ⑥ 颜色标记 [[#RRGGBB]…[[#RRGGBB] → 哨兵字符 + 渲染后自建 SpannableStringBuilder 回填颜色
    val (cleanMarked, markColors) = remember(content) { extractColorSegments(content) }
    val processedContent = remember(cleanMarked, dark) {
        var t = prepareMarkdownContent(cleanMarked)
        // ⑧ 深色模式：LaTeX 图改用反色版（diagram_xxx.png → diagram_xxx_dark.png）
        if (dark) t = rewriteLatexImageForDark(t)
        t
    }

    val markwon = remember(dark, fontSize) {
        Markwon.builder(context)
                .usePlugin(MarkwonInlineParserPlugin.create())
                .usePlugin(StrikethroughPlugin.create())
                .usePlugin(TablePlugin.create(context))
                .usePlugin(
                        ImagesPlugin.create { plugin ->
                            plugin.errorHandler { _, _ ->
                                android.graphics.drawable.ColorDrawable(android.graphics.Color.RED)
                            }
                            plugin.placeholderProvider {
                                android.graphics.drawable.ColorDrawable(
                                        if (dark) android.graphics.Color.DKGRAY else android.graphics.Color.LTGRAY
                                )
                            }
                        }
                )
                .usePlugin(GlideImagesPlugin.create(context))
                .usePlugin(
                        JLatexMathPlugin.create(
                                50f,
                                JLatexMathPlugin.BuilderConfigure { builder ->
                                    builder.inlinesEnabled(true)
                                    builder.blocksEnabled(true)
                                    builder.theme().apply {
                                        textColor(viewText)
                                        backgroundProvider {
                                            android.graphics.drawable.ColorDrawable(
                                                    android.graphics.Color.TRANSPARENT
                                            )
                                        }
                                    }
                                }
                        )
                )
                .build()
    }

    AndroidView(
            factory = { ctx ->
                TextView(ctx).apply {
                    setTextColor(viewText)
                    textSize = fontSize
                    setPadding(30, 20, 30, 20)
                    // 行间距随字体大小缩放，避免放大文字时行距变窄
                    setLineSpacing(fontSize * 0.2f, 1.2f)
                    setTextIsSelectable(true)
                    setBackgroundColor(android.graphics.Color.parseColor(viewBg))
                    // 允许长内容在固定高度容器内滚动（弹窗/详情页）
                    movementMethod = android.text.method.ScrollingMovementMethod()
                }
            },
            update = { textView ->
                if (processedContent.isNotEmpty()) {
                    try {
                        textView.setTextColor(viewText)
                        markwon.setMarkdown(textView, processedContent)
                        // ⑥ 回填颜色标记（自建 builder，不依赖 textView.text 类型，不会残留哨兵）
                        if (markColors.isNotEmpty()) {
                            applyColorMarkers(textView, markColors)
                        }
                        onRendered?.invoke()
                    } catch (e: Exception) {
                        Log.e("MarkdownView", "渲染失败: ${e.message}")
                        textView.text = processedContent
                        textView.setTextColor(viewText)
                    }
                } else {
                    textView.text = ""
                }
            },
            modifier = modifier
    )
}

// ==================== ⑥ 颜色标记 [[#RRGGBB]文字[[#RRGGBB] 解析与渲染 ====================
private const val COLOR_MARK_OPEN = '\uE000'
private const val COLOR_MARK_CLOSE = '\uE001'
private val colorMarkerRegex =
        Regex("""\[\[#([0-9A-Fa-f]{6})\]\](.*?)\[\[#[0-9A-Fa-f]{6}\]\]""", RegexOption.DOT_MATCHES_ALL)

private fun parseHexColor(hex: String): Int =
        try { android.graphics.Color.parseColor("#$hex") } catch (e: Exception) { -1 }

/** 把颜色标记替换为哨兵字符；返回(清理后文本, 颜色列表-按出现顺序) */
private fun extractColorSegments(text: String): Pair<String, List<Int>> {
    if (!text.contains("[[")) return text to emptyList()
    val colors = mutableListOf<Int>()
    val sb = StringBuilder()
    var last = 0
    var any = false
    for (mm in colorMarkerRegex.findAll(text)) {
        any = true
        colors.add(parseHexColor(mm.groupValues[1]))
        sb.append(text, last, mm.range.first)
        sb.append(COLOR_MARK_OPEN)
        sb.append(mm.groupValues[2])
        sb.append(COLOR_MARK_CLOSE)
        last = mm.range.last + 1
    }
    if (!any) return text to emptyList()
    sb.append(text, last, text.length)
    return sb.toString() to colors
}

/** 渲染后：自建 SpannableStringBuilder，跳过哨兵字符并给其间文字上色（无残留字形、不依赖 textView.text 类型） */
private fun applyColorMarkers(textView: TextView, colors: List<Int>) {
    try {
        val src = textView.text
        if (src == null || src.isEmpty()) return
        val rebuilt = android.text.SpannableStringBuilder()
        var openRebuilt = -1
        var segIdx = 0
        var i = 0
        val n = src.length
        while (i < n) {
            val ch = src[i]
            when {
                ch == COLOR_MARK_OPEN -> {
                    openRebuilt = rebuilt.length
                }
                ch == COLOR_MARK_CLOSE && openRebuilt >= 0 && segIdx < colors.size -> {
                    val col = colors[segIdx]
                    if (col != -1 && rebuilt.length > openRebuilt) {
                        rebuilt.setSpan(
                                android.text.style.ForegroundColorSpan(col),
                                openRebuilt, rebuilt.length,
                                android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE
                        )
                        // 复制区间内的样式（粗体/斜体等）——ForegroundColorSpan 已覆盖颜色，无需额外
                    }
                    segIdx++
                    openRebuilt = -1
                }
                else -> {
                    rebuilt.append(ch)
                }
            }
            i++
        }
        // 若还有未闭合的开标记忽略
        textView.text = rebuilt
    } catch (e: Exception) {
        Log.e("MarkdownView", "颜色标记渲染失败: ${e.message}")
    }
}

/**
 * ⑥ 纯文本路径的颜色标记渲染：把 [[#RRGGBB]文字[[#RRGGBB] 转换为带颜色Span的AnnotatedString
 */
@Composable
fun ColorText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontWeight: FontWeight? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    maxLines: Int = Int.MAX_VALUE,
    textAlign: TextAlign? = null,
) {
    val styled = remember(text) {
        buildAnnotatedString {
            if (!text.contains("[[")) {
                append(text)
            } else {
                var last = 0
                for (mm in colorMarkerRegex.findAll(text)) {
                    append(text, last, mm.range.first)
                    val col = parseHexColor(mm.groupValues[1])
                    if (col != -1) {
                        withStyle(SpanStyle(color = Color(col))) { append(mm.groupValues[2]) }
                    } else {
                        append(mm.groupValues[2])
                    }
                    last = mm.range.last + 1
                }
                append(text, last, text.length)
            }
        }
    }
    Text(
            text = styled,
            modifier = modifier,
            color = color,
            fontSize = fontSize,
            fontWeight = fontWeight,
            lineHeight = lineHeight,
            maxLines = maxLines,
            textAlign = textAlign,
    )
}

/**
 * 解题思路 JSON 兑底：AI 偶尔会把解题思路输出成
 * {"solution_steps":[...],"key_breakthrough":...} 结构，提取为纯文本
 */
private fun extractStepsText(text: String): String {
    val trimmed = text.trim()
    if (trimmed.startsWith("{") && trimmed.contains("solution_steps")) {
        return try {
            val obj = JSONObject(trimmed)
            val steps = obj.optJSONArray("solution_steps")
            val sb = StringBuilder()
            if (steps != null) {
                for (i in 0 until steps.length()) {
                    sb.append("${i + 1}. ").append(steps.optString(i, "")).append("\n")
                }
            }
            val kb = obj.optString("key_breakthrough", "")
            if (kb.isNotEmpty()) sb.append("\n💡 关键突破口：").append(kb)
            sb.toString().ifBlank { text }
        } catch (e: Exception) {
            text
        }
    }
    return text
}

/**
 * 思维导图围栏规范化：AI输出已含```代码块则原样使用，否则包裹（避免双重围栏格式错乱）。
 * 与服务端 _normalize_mindmap 逻辑一致，幂等。
 */
private fun formatMindMap(text: String): String {
    if (text.isBlank()) return text
    val fenced = text.trimStart().startsWith("```") || (text.split("```").size - 1) >= 2
    return if (fenced) text else "```\n$text\n```"
}

/** ⑧ 深色模式：把 LaTeX 图 URL 重写为反色版（diagram_xxx.png → diagram_xxx_dark.png；忽略已是 _dark 的） */
private fun rewriteLatexImageForDark(content: String): String {
    if (content.isEmpty()) return content
    return content.replace(
        Regex("""(diagram_)([0-9a-fA-F]+)\.png(?!_dark)""")
    ) { m ->
        "${m.groupValues[1]}${m.groupValues[2]}_dark.png"
    }
}

private fun prepareMarkdownContent(content: String): String {
    if (content.isEmpty()) return ""
    var processed = content
    // 1. Preserve existing $$...$$ blocks (don't touch them)
    // 2. Convert \(...\) to $...$ (inline LaTeX)
    processed = processed.replace(Regex("""\\\((.*?)\\\)""", RegexOption.DOT_MATCHES_ALL)) { match ->
        "\$${match.groupValues[1].trim()}\$"
    }
    // 3. Convert \[...\] to $$...$$ (display LaTeX)
    processed = processed.replace(Regex("""\\\[(.*?)\\\]""", RegexOption.DOT_MATCHES_ALL)) { match ->
        "\n\$\$\n${match.groupValues[1].trim()}\n\$\$\n"
    }
    // 4. 行内公式：单个$包裹的转换为$$（JLatexMathPlugin需要$$分隔符）
    //    避免误伤已有的$$和换行内的内容
    processed = processed.replace(
        Regex("(?<!\\$)\\$(?!\\$)([^$\\n]+?)(?<!\\$)\\$(?!\\$)")
    ) { match ->
        "\$\$${match.groupValues[1].trim()}\$\$"
    }
    // 5. Fix orphan newlines before headers/lists (ensure blank line before block elements)
    processed = processed.replace(Regex("([^\n])\n(#{1,6}\\s|>\\s|\\*\\s|\\d+\\.\\s)"), "$1\n\n$2")

    return processed
}

// ==================== ⑦ 主题（亮/暗/跟随系统） ====================
// 全局暗色标记：所有自绘颜色根据它切换（在 LearningAssistantTheme 组合时更新）
var AppDarkTheme: Boolean by mutableStateOf(true)

/** 主题色选择器：深色模式返回 dark，亮色模式返回 light（组合期调用，可响应切换） */
private fun tC(dark: Color, light: Color): Color = if (AppDarkTheme) dark else light

@Composable
fun LearningAssistantTheme(dark: Boolean = true, content: @Composable () -> Unit) {
    AppDarkTheme = dark
    MaterialTheme(
            colorScheme =
                    if (dark)
                        darkColorScheme(
                                primary = Color(0xFF00D2FF),
                                secondary = Color(0xFF7B2FBE),
                                background = tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)),
                                surface = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
                                onPrimary = Color.White,
                                onSecondary = Color.White,
                                onBackground = Color.White,
                                onSurface = Color.White,
                        )
                    else
                        lightColorScheme(
                                primary = Color(0xFF0086B3),
                                secondary = Color(0xFF7B2FBE),
                                background = Color(0xFFF2F4F8),
                                surface = Color(0xFFFFFFFF),
                                onPrimary = Color.White,
                                onSecondary = Color.White,
                                onBackground = Color(0xFF16181D),
                                onSurface = Color(0xFF16181D),
                        ),
            typography = Typography(),
            content = content
    )
}



@Composable
fun VoiceFloatingActionButton(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    var isListening by remember { mutableStateOf(false) }
    var hasAudioPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED
        )
    }

    val audioPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasAudioPermission = granted
    }

    val speechRecognizer = remember {
        SpeechRecognizer.createSpeechRecognizer(context)
    }

    val recognitionListener = remember {
        object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                isListening = false
            }
            override fun onError(error: Int) {
                isListening = false
                val errorMsg = when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH -> "未听清，请重试"
                    SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "未检测到语音"
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缺少录音权限"
                    SpeechRecognizer.ERROR_CLIENT -> "语音识别服务不可用（未安装语音引擎）"
                    else -> "语音识别错误: $error"
                }
                Toast.makeText(context, errorMsg, Toast.LENGTH_SHORT).show()
            }
            override fun onResults(results: Bundle?) {
                isListening = false
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                if (!matches.isNullOrEmpty()) {
                    val spokenText = matches[0]
                    viewModel.handleVoiceCommand(spokenText)
                }
            }
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            speechRecognizer.destroy()
        }
    }

    FloatingActionButton(
        onClick = {
            // ⑨ 语音识别暂未开放
            Toast.makeText(context, "语音识别暂未开放，敬请期待", Toast.LENGTH_SHORT).show()
            return@FloatingActionButton
            if (!hasAudioPermission) {
                audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                return@FloatingActionButton
            }
            // ⑦ 语音识别需要系统语音服务（Google/讯飞等），无服务时给出明确提示
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                Toast.makeText(
                        context,
                        "设备未安装语音识别服务（无 Google/讯飞等语音引擎），无法使用语音",
                        Toast.LENGTH_LONG
                ).show()
                return@FloatingActionButton
            }
            if (!isListening) {
                isListening = true
                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                    putExtra(RecognizerIntent.EXTRA_PROMPT, "请说出你的需求...")
                }
                speechRecognizer.setRecognitionListener(recognitionListener)
                speechRecognizer.startListening(intent)
            } else {
                speechRecognizer.stopListening()
                isListening = false
            }
        },
        modifier = modifier,
        containerColor = if (isListening) Color(0xFF00D2FF) else Color(0xFF2196F3)
    ) {
        Text(if (isListening) "listening" else "mic", fontSize = 24.sp)
    }
}


@Composable
fun AskDialog(
    suggestedQuestions: List<String>,
    onAsk: (String) -> Unit,
    onDismiss: () -> Unit
) {
    var inputText by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("💬 您可能还想问", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                if (suggestedQuestions.isNotEmpty()) {
                    Text("推荐问题：", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                    suggestedQuestions.forEach { question ->
                        TextButton(
                            onClick = { onAsk(question) },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                question,
                                color = Color(0xFF00D2FF),
                                fontSize = 14.sp,
                                textAlign = TextAlign.Start,
                                modifier = Modifier.fillMaxWidth()
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedTextField(
                        value = inputText,
                        onValueChange = { inputText = it },
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("输入你的问题...", color = tC(Color.Gray, Color(0xFF5C6470))) },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = tC(Color.White, Color(0xFF16181D)),
                            unfocusedTextColor = tC(Color.White, Color(0xFF16181D)),
                            focusedBorderColor = Color(0xFF00D2FF),
                            unfocusedBorderColor = tC(Color.Gray, Color(0xFF5C6470)),
                        )
                    )
                    Button(
                        onClick = {
                            if (inputText.isNotBlank()) {
                                onAsk(inputText.trim())
                                inputText = ""
                            }
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
                    ) {
                        Text("发送", color = Color.White)
                    }
                }
            }
        },
        confirmButton = {},
        containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

data class FunctionButtonData(
    val title: String,
    val subtitle: String,
    val color: Color,
    val onClick: () -> Unit
)

@Composable
fun MainMenuScreen(
    viewModel: MainViewModel,
    statusText: String,
    gestureEnabled: Boolean = false,
    onPickImage: ((String) -> Unit)? = null,
    isCameraReady: Boolean = true,
) {
    val voiceEnabled by viewModel.voiceEnabled.collectAsState()
    val showWelcome by viewModel.showWelcomeDialog.collectAsState()
    // ⑩ 主页面相机对准预览
    val cameraPreviewEnabled by viewModel.cameraPreviewEnabled.collectAsState()
    var showPickDialog by remember { mutableStateOf(false) }
    
    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(
                        // ⑩ 开启对准预览时，背景半透明让相机画面透出，便于对准题目
                        if (cameraPreviewEnabled) tC(Color(0x660A0A1A), Color(0x88F2F4F8))
                        else tC(Color(0xFF0A0A1A), Color(0xFFF2F4F8))
                )
        ) {
        TopStatusBar(statusText = statusText, viewModel = viewModel, showSettingsButton = true, showHelpButton = true)

        // ⑩ 相机对准预览开关
        Row(
                modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
        ) {
            Text("📷 相机对准", color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Switch(
                    checked = cameraPreviewEnabled,
                    onCheckedChange = { viewModel.cameraPreviewEnabled.value = it },
                    colors = SwitchDefaults.colors(checkedThumbColor = Color(0xFF00D2FF))
            )
        }

        // ③ 相机预览取景框（开启对准时显示，用于对准题目）
        if (cameraPreviewEnabled) {
            Box(
                    modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 4.dp)
                            .height(150.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(Color(0x00000000))
                            .border(2.dp, Color(0xFF00D2FF), RoundedCornerShape(12.dp)),
                    contentAlignment = Alignment.Center
            ) {
                Text(
                        "✨ 将题目对准此框\n（相机画面）",
                        color = tC(Color.White.copy(alpha = 0.7f), Color(0xFF16181D).copy(alpha = 0.7f)),
                        fontSize = 12.sp,
                        textAlign = TextAlign.Center
                )
            }
        }

        // 相机不可用时：醒目提示改用相册选图
        if (!isCameraReady) {
            Surface(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                color = Color(0x66F44336),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text(
                    "⚠️ 相机不可用（模拟器/无摄像头环境），请使用下方“🖼️ 相册选图”功能",
                    color = tC(Color.White, Color(0xFF16181D)),
                    fontSize = 12.sp,
                    modifier = Modifier.padding(10.dp)
                )
            }
        }

        val fingerCount by viewModel.detectedFingerCount.collectAsState()
        if (gestureEnabled && fingerCount > 0) {
            FingerCountHint(fingerCount = fingerCount)
        }

        Spacer(modifier = Modifier.height(8.dp))

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .alpha(if (cameraPreviewEnabled) 0.55f else 1f),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            val buttons = listOf(
                FunctionButtonData("🤔 AI解题", "伸出5根手指", Color(0xFF2196F3), { viewModel.onButtonSolve() }),
                FunctionButtonData("🎬 AI动画", "伸出4根手指", Color(0xFF9C27B0), { viewModel.onButtonAnimation() }),
                FunctionButtonData("📊 数据报告", "伸出3根手指", Color(0xFFFF9800), { viewModel.loadDataReport() }),
                FunctionButtonData("🤖 AI报告", "伸出2根手指", Color(0xFF4CAF50), { viewModel.loadAiReport() }),
                FunctionButtonData("📎 知识延伸", "伸出1根手指", Color(0xFF00BCD4), { viewModel.onButtonExtend() }),
                FunctionButtonData("📋 历史记录", "点击查看", Color(0xFF795548), { viewModel.showHistory() }),
            )

            buttons.chunked(2).forEach { row ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    row.forEach { data ->
                        FunctionButton(
                            title = data.title,
                            subtitle = data.subtitle,
                            color = data.color,
                            onClick = data.onClick,
                            modifier = Modifier.weight(1f)
                        )
                    }
                    if (row.size == 1) {
                        Spacer(modifier = Modifier.weight(1f))
                    }
                }
            }

            // 相册选图：无可用相机（如虚拟机/模拟器）时也能体验完整功能
            if (onPickImage != null) {
                Button(
                    onClick = { showPickDialog = true },
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isCameraReady) tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)) else Color(0xFF00D2FF)
                    ),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text(
                        "🖼️ 从相册选图${if (isCameraReady) "（相机不可用时使用）" else "（当前推荐）"}",
                        color = if (isCameraReady) tC(Color.White, Color(0xFF16181D)) else Color.Black,
                        fontSize = 14.sp
                    )
                }
            }

            Spacer(modifier = Modifier.height(32.dp))
        }
    }

    if (voiceEnabled) {
        VoiceFloatingActionButton(
            viewModel = viewModel,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(16.dp)
        )
    }
}

    if (showPickDialog) {
        AlertDialog(
            onDismissRequest = { showPickDialog = false },
            title = { Text("🖼️ 从相册选图", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("选择图片后将用于：", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                    listOf(
                        "🤔 AI解题" to "solve",
                        "🎬 AI动画" to "animation",
                        "📎 知识延伸" to "extend",
                    ).forEach { (label, action) ->
                        Button(
                            onClick = {
                                showPickDialog = false
                                onPickImage?.invoke(action)
                            },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                        ) { Text(label, color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp) }
                    }
                }
            },
            confirmButton = {},
            containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
        )
    }
}

@Composable
fun TopStatusBar(statusText: String, viewModel: MainViewModel, showSettingsButton: Boolean = false, showHelpButton: Boolean = false) {
    val pomodoroTime by viewModel.pomodoroTime.collectAsState()
    val pomodoroMode by viewModel.pomodoroMode.collectAsState()
    val elapsedTime by viewModel.elapsedTime.collectAsState()
    Row(
            modifier =
                    Modifier.fillMaxWidth()
                            .background(tC(Color(0xFF16213E), Color(0xFFFFFFFF)), RoundedCornerShape(12.dp))
                            .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(statusText, color = tC(Color.White, Color(0xFF16181D)), fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
        // Bug 19: 操作说明按钮
        if (showHelpButton) {
            TextButton(onClick = { viewModel.showWelcomeDialog.value = true }) {
                Text("❓", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp)
            }
        }
        // 番茄钟：点击打开设置（支持倒计时/正计时）
        TextButton(onClick = { viewModel.showPomodoroSettings.value = true }) {
            Text(
                    if (pomodoroMode) "⏱ ${formatStopwatch(elapsedTime)}" else "🍅 ${formatTime(pomodoroTime)}",
                    color = tC(Color.White, Color(0xFF16181D)),
                    fontSize = 14.sp
            )
        }
        // Bug 20: 设置按钮移动到右上角（emoji图标）
        if (showSettingsButton) {
            TextButton(onClick = { viewModel.showSettings() }) {
                Text("⚙️", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp)
            }
        }
    }
}

@Composable
fun FingerCountHint(fingerCount: Int) {
    val hintText =
            when (fingerCount) {
                5 -> "✅ 即将AI解题，请将手移开..."
                4 -> "🎬 即将生成动画，请将手移开..."
                1 -> "☝️ 即将知识延伸，请将手移开..."
                else -> "$fingerCount 根手指"
            }

    Text(
            text = hintText,
            color = Color(0xFF00FF00),
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            modifier =
                    Modifier.fillMaxWidth()
                            .background(Color(0xAA000000), RoundedCornerShape(8.dp))
                            .padding(16.dp),
            textAlign = TextAlign.Center
    )
}

@Composable
fun FunctionGrid(viewModel: MainViewModel) {
    val buttons = listOf(
        FunctionButtonData("🤔 AI解题", "伸出5根手指", Color(0xFF2196F3)) { viewModel.onButtonSolve() },
        FunctionButtonData("🎬 AI动画", "伸出4根手指", Color(0xFF9C27B0)) { viewModel.onButtonAnimation() },
        FunctionButtonData("📊 数据报告", "伸出3根手指", Color(0xFFFF9800)) { viewModel.loadDataReport() },
        FunctionButtonData("🤖 AI报告", "伸出2根手指", Color(0xFF4CAF50)) { viewModel.loadAiReport() },
        FunctionButtonData("📎 知识延伸", "伸出1根手指", Color(0xFF00BCD4)) { viewModel.onButtonExtend() },
        FunctionButtonData("📋 历史记录", "点击查看", Color(0xFF795548)) { viewModel.showHistory() },
        FunctionButtonData("⚙️ 设置", "个性化配置", Color(0xFF607D8B)) { viewModel.showSettings() },
    )

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        buttons.chunked(2).forEach { row ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                row.forEach { data ->
                    FunctionButton(
                        title = data.title,
                        subtitle = data.subtitle,
                        color = data.color,
                        onClick = data.onClick,
                        modifier = Modifier.weight(1f)
                    )
                }
                if (row.size == 1) {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
fun FunctionButton(
        title: String,
        subtitle: String,
        color: Color,
        onClick: () -> Unit,
        modifier: Modifier = Modifier
) {
    Button(
            onClick = onClick,
            modifier = modifier.height(80.dp),
            colors = ButtonDefaults.buttonColors(containerColor = color.copy(alpha = 0.8f)),
            shape = RoundedCornerShape(12.dp)
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(title, color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.height(4.dp))
            Text(subtitle, color = tC(Color.White.copy(alpha = 0.7f), Color(0xFF16181D).copy(alpha = 0.7f)), fontSize = 11.sp)
        }
    }
}

@Composable
fun LoadingOverlay(message: String = "处理中...") {
    Box(
            modifier = Modifier.fillMaxSize().background(Color(0xCC0A0A1A)),
            contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = Color(0xFF00D2FF))
            Spacer(modifier = Modifier.height(16.dp))
            Text(message, color = Color.White, fontSize = 16.sp)
        }
    }
}

@Composable
fun WelcomeDialog(onDismiss: () -> Unit, viewModel: MainViewModel) {
    var dontShowAgain by remember { mutableStateOf(false) }
    AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("👋 欢迎使用学习助手 2.1", color = tC(Color.White, Color(0xFF16181D))) },
            text = {
                Column {
                    Text(
                            """📗 使用说明：

📷 拍题解题流程：
  1. 将题目放入取景框（手离开摄像头）拍照
  2. 自动OCR识别 → 确认/修改识别结果
  3. AI 分步解题（思路/解析/图解/导图）
  4. 可追问、查看GeoGebra图形、记录掌握程度

🖐️ 手势操作（主页伸出对应手指保持1秒）：
  · 5指 → AI解题    · 4指 → AI动画
  · 3指 → 数据报告  · 2指 → AI报告
  · 1指 → 知识延伸

📋 也可直接点击按钮触发功能

📚 历史记录：查看原题图片/完整解析（含LaTeX图）、
  按学科筛选、删除（服务端移入回收站）

⚙️ 更多设置（AI模型/回答风格/主题/字体）在右上角
⚠️ 拍照前请将手移开摄像头""",
                            color = tC(Color.White.copy(alpha = 0.9f), Color(0xFF16181D).copy(alpha = 0.9f)),
                            fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(
                            modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { dontShowAgain = !dontShowAgain },
                            verticalAlignment = Alignment.CenterVertically
                    ) {
                        Checkbox(
                                checked = dontShowAgain,
                                onCheckedChange = { dontShowAgain = it },
                                colors = CheckboxDefaults.colors(checkedColor = Color(0xFF00D2FF))
                        )
                        Text("不再提醒", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
                    }
                }
            },
            confirmButton = {
                Button(onClick = {
                    if (dontShowAgain) {
                        viewModel.apiService.setWelcomeDismissed(true)
                    }
                    onDismiss()
                }) { Text("开始使用") }
            },
            containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

@Composable
fun HistoryViewScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val historyStartDate by viewModel.historyStartDate.collectAsState()
    val historyEndDate by viewModel.historyEndDate.collectAsState()
    val serverAddress by viewModel.serverAddress.collectAsState()
    
    var records by remember { mutableStateOf<List<ApiService.HistoryRecord>>(emptyList()) }
    var isLoading by remember { mutableStateOf(true) }
    var errorMessage by remember { mutableStateOf("") }
    var selectedRecord by remember { mutableStateOf<ApiService.HistoryRecord?>(null) }
    // 服务端返回的真实总数/学科数（不受50条截断影响）
    var totalCount by remember { mutableStateOf(0) }
    var subjectCount by remember { mutableStateOf(0) }
    
    var isSelectMode by remember { mutableStateOf(false) }
    var selectedIds by remember { mutableStateOf<Set<Int>>(emptySet()) }
    var isDeleting by remember { mutableStateOf(false) }
    var dateError by remember { mutableStateOf("") }
    // 清除全部确认框
    var showClearConfirm by remember { mutableStateOf(false) }
    // ③④ 筛选器（多选，空=全部）
    var subjectFilter by remember { mutableStateOf<Set<String>>(emptySet()) }
    var gradeFilter by remember { mutableStateOf<Set<String>>(emptySet()) }
    var difficultyFilter by remember { mutableStateOf<Set<String>>(emptySet()) }
    var masteryFilter by remember { mutableStateOf<Set<String>>(emptySet()) }
    var showFilterDialog by remember { mutableStateOf(false) }
    
    fun loadHistory() {
        // Feature 16: 日期格式验证
        val dateRegex = Regex("^\\d{4}-\\d{2}-\\d{2}$")
        if (historyStartDate.isNotEmpty() && !dateRegex.matches(historyStartDate)) {
            dateError = "开始日期格式错误，请使用格式: 2026-01-01"
            return
        }
        if (historyEndDate.isNotEmpty() && !dateRegex.matches(historyEndDate)) {
            dateError = "截止日期格式错误，请使用格式: 2026-12-31"
            return
        }
        dateError = ""
        
        isLoading = true
        errorMessage = ""
        viewModel.viewModelScope.launch(Dispatchers.IO) {
            try {
                // 使用ViewModel的ApiService（带token与服务器地址），否则登录用户看不到自己的记录
                val result = viewModel.apiService.getHistory(
                    startDate = historyStartDate,
                    endDate = historyEndDate,
                    subject = subjectFilter.joinToString(","),
                    grade = gradeFilter.joinToString(","),
                    difficulty = difficultyFilter.joinToString(","),
                    mastery = masteryFilter.joinToString(","),
                )
                withContext(Dispatchers.Main) {
                    records = result.records
                    totalCount = result.totalCount
                    subjectCount = result.subjectCount
                    isLoading = false
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    errorMessage = "加载失败: ${e.message}"
                    isLoading = false
                }
            }
        }
    }
    
    fun batchDelete() {
        if (selectedIds.isEmpty()) return
        isDeleting = true
        val deletingCount = selectedIds.size
        viewModel.viewModelScope.launch(Dispatchers.IO) {
            try {
                viewModel.apiService.batchDeleteHistory(selectedIds.toList())
                withContext(Dispatchers.Main) {
                    isDeleting = false
                    isSelectMode = false
                    selectedIds = emptySet()
                    loadHistory()
                    Toast.makeText(context, "已删除 $deletingCount 条记录", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    isDeleting = false
                    Toast.makeText(context, "删除失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
    
    LaunchedEffect(Unit) {
        loadHistory()
    }
    
    LaunchedEffect(historyStartDate, historyEndDate) {
        loadHistory()
    }
    
    if (selectedRecord != null) {
        HistoryDetailScreen(
            record = selectedRecord!!,
            onBack = { selectedRecord = null },
            viewModel = viewModel,
            serverAddress = serverAddress
        )
        return
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (isSelectMode) {
                Button(
                    onClick = {
                        isSelectMode = false
                        selectedIds = emptySet()
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("取消", color = Color.White) }
                
                Text("已选 ${selectedIds.size} 项", color = Color(0xFF00D2FF), fontSize = 16.sp, fontWeight = FontWeight.Bold)
                
                Button(
                    onClick = { batchDelete() },
                    enabled = selectedIds.isNotEmpty() && !isDeleting,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF44336))
                ) {
                    if (isDeleting) {
                        CircularProgressIndicator(color = tC(Color.White, Color(0xFF16181D)), modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                    } else {
                        Text("🗑️ 删除", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
                    }
                }
            } else {
                Button(
                    onClick = onBack,
                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("返回", color = tC(Color.White, Color(0xFF16181D))) }
                
                Text("📋 历史记录", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                
                IconButton(onClick = { loadHistory() }) {
                    Text("🔄", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp)
                }
            }
        }
        
        // ③ 筛选按钮行（日期/学科/年级/难度统一在筛选面板中）
        Row(
                modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
        ) {
            Button(
                    onClick = { showFilterDialog = true },
                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
            ) { Text("🔍 筛选", color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp) }
            val activeFilters =
                    listOf(
                            subjectFilter.joinToString("/"),
                            gradeFilter.joinToString("/"),
                            difficultyFilter.joinToString("/"),
                            masteryFilter.joinToString("/")
                    ).filter { it.isNotEmpty() }
            if (activeFilters.isNotEmpty()) {
                Text(
                        "已选：${activeFilters.joinToString(" / ")}",
                        color = Color(0xFF00D2FF),
                        fontSize = 12.sp,
                        modifier = Modifier.weight(1f)
                )
                TextButton(
                        onClick = {
                            subjectFilter = emptySet()
                            gradeFilter = emptySet()
                            difficultyFilter = emptySet()
                            masteryFilter = emptySet()
                            loadHistory()
                        }
                ) { Text("重置", color = Color(0xFFF44336), fontSize = 12.sp) }
            } else {
                Text(
                        "可按日期/学科/年级/难度筛选",
                        color = tC(Color.Gray, Color(0xFF5C6470)),
                        fontSize = 12.sp,
                        modifier = Modifier.weight(1f)
                )
            }
        }
        
        // Feature 16: 日期格式错误提示
        if (dateError.isNotEmpty()) {
            Text(
                dateError,
                color = Color(0xFFF44336),
                fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 12.dp)
            )
        }
        
        Spacer(modifier = Modifier.height(8.dp))
        
        when {
            isLoading -> {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(color = Color(0xFF00D2FF))
                        Spacer(modifier = Modifier.height(12.dp))
                        Text("加载中...", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 14.sp)
                    }
                }
            }
            errorMessage.isNotEmpty() -> {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("⚠️", fontSize = 32.sp)
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(errorMessage, color = Color(0xFFFF5722), fontSize = 14.sp)
                        Spacer(modifier = Modifier.height(12.dp))
                        Button(onClick = { loadHistory() }) {
                            Text("重试")
                        }
                    }
                }
            }
            records.isEmpty() -> {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📥", fontSize = 48.sp)
                        Spacer(modifier = Modifier.height(8.dp))
                        Text("暂无历史记录", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 16.sp)
                        Text("开始学习后将自动记录", color = tC(Color.Gray, Color(0xFF5C6470)).copy(alpha = 0.6f), fontSize = 13.sp)
                    }
                }
            }
            else -> {
                val filteredRecords =
                        if (subjectFilter.isEmpty()) records else records.filter { it.subject in subjectFilter }
                if (filteredRecords.isEmpty()) {
                    Box(
                            modifier = Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("🔍", fontSize = 48.sp)
                            Spacer(modifier = Modifier.height(8.dp))
                            Text("暂无符合条件的记录", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 16.sp)
                            Spacer(modifier = Modifier.height(12.dp))
                            Button(onClick = {
                                subjectFilter = emptySet()
                                gradeFilter = emptySet()
                                difficultyFilter = emptySet()
                                masteryFilter = emptySet()
                                loadHistory()
                            }) {
                                Text("查看全部")
                            }
                        }
                    }
                } else {
                val listState = rememberLazyListState()
                LazyColumn(
                    state = listState,
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    itemsIndexed(filteredRecords, key = { _, it -> it.id }) { index, record ->
                        HistoryRecordCard(
                            record = record,
                            serverAddress = serverAddress,
                            displayIndex = index + 1,
                            isSelectMode = isSelectMode,
                            isSelected = selectedIds.contains(record.id),
                            onClick = {
                                if (isSelectMode) {
                                    selectedIds = if (selectedIds.contains(record.id)) {
                                        selectedIds - record.id
                                    } else {
                                        selectedIds + record.id
                                    }
                                } else {
                                    selectedRecord = record
                                }
                            },
                            onLongClick = {
                                if (!isSelectMode) {
                                    isSelectMode = true
                                    selectedIds = setOf(record.id)
                                }
                            }
                        )
                    }
                }
                
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    // 选择模式下左侧只显示已选数量，避免长文本压缩右侧按钮（⑧）
                    if (isSelectMode) {
                        Text(
                                "已选 ${selectedIds.size} 项",
                                color = Color(0xFF00D2FF),
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier
                                        .weight(1f)
                                        .padding(end = 8.dp)
                        )
                    } else {
                    // 使用服务端返回的真实总数/学科数
                    val displayTotal = if (totalCount > 0) totalCount else records.size
                    val displaySubjects = if (subjectCount > 0) subjectCount
                    else records.map { it.subject }.filter { it.isNotEmpty() }.distinct().size
                    val filterNote =
                            if (subjectFilter.isNotEmpty()) " | 当前筛选：${subjectFilter.joinToString("/")} ${filteredRecords.size} 条" else ""
                    Text(
                        "共${displayTotal} 条记录 \n ${displaySubjects} 门学科$filterNote",
                        color = tC(Color.Gray, Color(0xFF5C6470)),
                        fontSize = 12.sp,
                        modifier = Modifier
                                .weight(1f)
                                .padding(end = 8.dp)
                    )
                    }
                    
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = {
                                isSelectMode = !isSelectMode
                                if (!isSelectMode) selectedIds = emptySet()
                            },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (isSelectMode) Color(0xFF00D2FF) else tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                            )
                        ) {
                            Text(
                                if (isSelectMode) "☑️ 完成" else "☑️ 选择",
                                color = tC(Color.White, Color(0xFF16181D)),
                                fontSize = 12.sp
                            )
                        }
                        
                        Button(
                            onClick = { showClearConfirm = true },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF44336))
                        ) {
                            Text("🗑️ 清除全部", color = tC(Color.White, Color(0xFF16181D)), fontSize = 12.sp)
                        }
                    }
                }
                }
            }
        }
        
        // ⑧ 清除全部确认框
        if (showClearConfirm) {
            AlertDialog(
                    onDismissRequest = { showClearConfirm = false },
                    title = { Text("确认清除全部？", color = tC(Color.White, Color(0xFF16181D))) },
                    text = {
                        Text(
                                "将删除当前日期范围内的所有历史记录，\n服务端文件会移入回收站，此操作不可恢复。",
                                color = tC(Color.White.copy(alpha = 0.85f), Color(0xFF16181D).copy(alpha = 0.85f)),
                                fontSize = 14.sp
                        )
                    },
                    confirmButton = {
                        Button(
                                onClick = {
                                    showClearConfirm = false
                                    android.widget.Toast.makeText(context, "正在清除...", android.widget.Toast.LENGTH_SHORT).show()
                                    viewModel.clearHistory()
                                    Handler(Looper.getMainLooper()).postDelayed({
                                        loadHistory()
                                    }, 500)
                                },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF44336))
                        ) { Text("确认清除", color = tC(Color.White, Color(0xFF16181D))) }
                    },
                    dismissButton = {
                        TextButton(onClick = { showClearConfirm = false }) { Text("取消", color = tC(Color.Gray, Color(0xFF5C6470))) }
                    },
                    containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
            )
        }
        
        // ③ 筛选面板（日期/学科/年级/难度，类似“设置”框）
        if (showFilterDialog) {
            val allSubjects =
                    records.map { it.subject }.filter { it.isNotEmpty() }.distinct().sorted()
            val allGrades =
                    records.map { it.grade }.filter { it.isNotEmpty() }.distinct().sorted()
            val difficultyOptions = listOf("易", "较易", "中", "较难", "难")
            AlertDialog(
                    onDismissRequest = { showFilterDialog = false },
                    title = { Text("🔍 筛选历史记录", color = tC(Color.White, Color(0xFF16181D))) },
                    text = {
                        Column(
                                modifier = Modifier.verticalScroll(rememberScrollState()),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Text("📅 日期范围", color = Color(0xFF00D2FF), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedTextField(
                                        value = historyStartDate,
                                        onValueChange = { viewModel.historyStartDate.value = it; dateError = "" },
                                        modifier = Modifier.weight(1f).height(44.dp),
                                        placeholder = { Text("开始 2026-01-01", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp) },
                                        singleLine = true,
                                        isError = dateError.isNotEmpty(),
                                        colors = darkTextFieldColors(),
                                        textStyle = LocalTextStyle.current.copy(fontSize = 12.sp)
                                )
                                Text("至", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp)
                                OutlinedTextField(
                                        value = historyEndDate,
                                        onValueChange = { viewModel.historyEndDate.value = it; dateError = "" },
                                        modifier = Modifier.weight(1f).height(44.dp),
                                        placeholder = { Text("截止 2026-12-31", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp) },
                                        singleLine = true,
                                        isError = dateError.isNotEmpty(),
                                        colors = darkTextFieldColors(),
                                        textStyle = LocalTextStyle.current.copy(fontSize = 12.sp)
                                )
                            }
                            if (dateError.isNotEmpty()) {
                                Text(dateError, color = Color(0xFFF44336), fontSize = 12.sp)
                            }
                            
                            Text("📚 学科", color = Color(0xFF00D2FF), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Row(
                                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                FilterChip(
                                        selected = subjectFilter.isEmpty(),
                                        onClick = { subjectFilter = emptySet() },
                                        label = { Text("全部", fontSize = 12.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFF00D2FF),
                                                selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                labelColor = tC(Color.White, Color(0xFF16181D)),
                                                containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                        )
                                )
                                allSubjects.forEach { subj ->
                                    FilterChip(
                                            selected = subj in subjectFilter,
                                            onClick = {
                                                subjectFilter =
                                                        if (subj in subjectFilter) subjectFilter - subj else subjectFilter + subj
                                            },
                                            label = { Text(subj, fontSize = 12.sp) },
                                            colors = FilterChipDefaults.filterChipColors(
                                                    selectedContainerColor = Color(0xFF00D2FF),
                                                    selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                    labelColor = tC(Color.White, Color(0xFF16181D)),
                                                    containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                            )
                                    )
                                }
                            }
                            
                            Text("🎓 年级", color = Color(0xFF00D2FF), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Row(
                                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                FilterChip(
                                        selected = gradeFilter.isEmpty(),
                                        onClick = { gradeFilter = emptySet() },
                                        label = { Text("全部", fontSize = 12.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFF7B2FBE),
                                                selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                labelColor = tC(Color.White, Color(0xFF16181D)),
                                                containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                        )
                                )
                                allGrades.forEach { g ->
                                    FilterChip(
                                            selected = g in gradeFilter,
                                            onClick = {
                                                gradeFilter =
                                                        if (g in gradeFilter) gradeFilter - g else gradeFilter + g
                                            },
                                            label = { Text(g, fontSize = 12.sp) },
                                            colors = FilterChipDefaults.filterChipColors(
                                                    selectedContainerColor = Color(0xFF7B2FBE),
                                                    selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                    labelColor = tC(Color.White, Color(0xFF16181D)),
                                                    containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                            )
                                    )
                                }
                            }
                            
                            Text("📊 难度", color = Color(0xFF00D2FF), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Row(
                                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                FilterChip(
                                        selected = difficultyFilter.isEmpty(),
                                        onClick = { difficultyFilter = emptySet() },
                                        label = { Text("全部", fontSize = 12.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFF4CAF50),
                                                selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                labelColor = tC(Color.White, Color(0xFF16181D)),
                                                containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                        )
                                )
                                difficultyOptions.forEach { d ->
                                    FilterChip(
                                            selected = d in difficultyFilter,
                                            onClick = {
                                                difficultyFilter =
                                                        if (d in difficultyFilter) difficultyFilter - d else difficultyFilter + d
                                            },
                                            label = { Text(d, fontSize = 12.sp) },
                                            colors = FilterChipDefaults.filterChipColors(
                                                    selectedContainerColor = Color(0xFF4CAF50),
                                                    selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                    labelColor = tC(Color.White, Color(0xFF16181D)),
                                                    containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                            )
                                    )
                                }
                            }

                            Text("📊 掌握程度", color = Color(0xFF00D2FF), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Row(
                                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                val masteryOptions = listOf("完全掌握", "部分掌握", "完全没掌握", "未记录")
                                FilterChip(
                                        selected = masteryFilter.isEmpty(),
                                        onClick = { masteryFilter = emptySet() },
                                        label = { Text("全部", fontSize = 12.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFFFF9800),
                                                selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                labelColor = tC(Color.White, Color(0xFF16181D)),
                                                containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                        )
                                )
                                masteryOptions.forEach { mv ->
                                    FilterChip(
                                            selected = mv in masteryFilter,
                                            onClick = {
                                                masteryFilter =
                                                        if (mv in masteryFilter) masteryFilter - mv else masteryFilter + mv
                                            },
                                            label = { Text(mv, fontSize = 12.sp) },
                                            colors = FilterChipDefaults.filterChipColors(
                                                    selectedContainerColor = Color(0xFFFF9800),
                                                    selectedLabelColor = tC(Color.White, Color(0xFF16181D)),
                                                    labelColor = tC(Color.White, Color(0xFF16181D)),
                                                    containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4))
                                            )
                                    )
                                }
                            }
                        }
                    },
                    confirmButton = {
                        Button(
                                onClick = {
                                    showFilterDialog = false
                                    loadHistory()
                                },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
                        ) { Text("应用", color = Color.Black, fontWeight = FontWeight.Bold) }
                    },
                    dismissButton = {
                        TextButton(onClick = { showFilterDialog = false }) { Text("取消", color = tC(Color.Gray, Color(0xFF5C6470))) }
                    },
                    containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
            )
        }
    }
}

@Composable
fun HistoryRecordCard(
    record: ApiService.HistoryRecord,
    serverAddress: String = "10.100.55.231:8000",
    displayIndex: Int = 0,
    isSelectMode: Boolean = false,
    isSelected: Boolean = false,
    onClick: () -> Unit,
    onLongClick: () -> Unit = {}
) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (isSelected) Color(0xFF1A3A5C) else tC(Color(0xFF16213E), Color(0xFFFFFFFF))
        ),
        shape = RoundedCornerShape(12.dp)
    ) {
        Row(modifier = Modifier.padding(12.dp)) {
            if (isSelectMode) {
                Checkbox(
                    checked = isSelected,
                    onCheckedChange = { onClick() },
                    colors = CheckboxDefaults.colors(
                        checkedColor = Color(0xFF00D2FF),
                        uncheckedColor = tC(Color.Gray, Color(0xFF5C6470))
                    ),
                    modifier = Modifier.padding(end = 8.dp)
                )
            }
            
            Column(modifier = Modifier.weight(1f)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val displayTime = try {
                        if (record.timestamp.isNotEmpty()) {
                            record.timestamp.substring(0, 19).replace("T", " ")
                        } else ""
                    } catch (e: Exception) { record.timestamp }
                    
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(displayTime, color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 11.sp)
                        Spacer(modifier = Modifier.width(8.dp))
                        if (record.grade.isNotEmpty()) {
                            Surface(
                                color = Color(0xFF7B2FBE).copy(alpha = 0.7f),
                                shape = RoundedCornerShape(4.dp)
                            ) {
                                Text(
                                    record.grade,
                                    color = tC(Color.White, Color(0xFF16181D)),
                                    fontSize = 9.sp,
                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                )
                            }
                        }
                        if (record.subject.isNotEmpty()) {
                            Spacer(modifier = Modifier.width(4.dp))
                            Surface(
                                color = Color(0xFF2196F3).copy(alpha = 0.7f),
                                shape = RoundedCornerShape(4.dp)
                            ) {
                                Text(
                                    record.subject,
                                    color = tC(Color.White, Color(0xFF16181D)),
                                    fontSize = 9.sp,
                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                )
                            }
                        }
                        if (record.difficulty.isNotEmpty()) {
                            Spacer(modifier = Modifier.width(4.dp))
                            val diffColor = when (record.difficulty) {
                                "易", "较易" -> Color(0xFF4CAF50)
                                "中" -> Color(0xFFFF9800)
                                "较难", "难" -> Color(0xFFF44336)
                                else -> tC(Color.Gray, Color(0xFF5C6470))
                            }
                            Surface(
                                color = diffColor.copy(alpha = 0.7f),
                                shape = RoundedCornerShape(4.dp)
                            ) {
                                Text(
                                    record.difficulty,
                                    color = tC(Color.White, Color(0xFF16181D)),
                                    fontSize = 9.sp,
                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                )
                            }
                        }
                        // ⑤ 掌握程度标签（与学科/难度同款）
                        if (record.masteryLevel.isNotEmpty()) {
                            Spacer(modifier = Modifier.width(4.dp))
                            val masteryColor = when (record.masteryLevel) {
                                "完全掌握" -> Color(0xFF4CAF50)
                                "部分掌握" -> Color(0xFFFF9800)
                                "完全没掌握" -> Color(0xFFF44336)
                                else -> tC(Color.Gray, Color(0xFF5C6470))
                            }
                            Surface(
                                color = masteryColor.copy(alpha = 0.7f),
                                shape = RoundedCornerShape(4.dp)
                            ) {
                                Text(
                                    record.masteryLevel,
                                    color = tC(Color.White, Color(0xFF16181D)),
                                    fontSize = 9.sp,
                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                )
                            }
                        }
                    }
                    Text("#$displayIndex", color = Color(0xFF00D2FF).copy(alpha = 0.5f), fontSize = 11.sp)
                }
                
                Spacer(modifier = Modifier.height(6.dp))
                
                if (record.knowledgePoints.isNotEmpty()) {
                    // 知识点像标签一样一个个接着排列，超宽自动换行，避免被压缩成竖条
                    FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.fillMaxWidth()
                    ) {
                        record.knowledgePoints.take(3).forEach { kp ->
                            Surface(
                                color = Color(0xFF00D2FF).copy(alpha = 0.15f),
                                shape = RoundedCornerShape(4.dp)
                            ) {
                                Text(
                                    kp,
                                    color = Color(0xFF00D2FF).copy(alpha = 0.8f),
                                    fontSize = 9.sp,
                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                )
                            }
                        }
                        if (record.knowledgePoints.size > 3) {
                            Text(
                                "+${record.knowledgePoints.size - 3}",
                                color = tC(Color.Gray, Color(0xFF5C6470)),
                                fontSize = 9.sp,
                                modifier = Modifier.align(Alignment.CenterVertically)
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                }
                
                // 题目以原图展示（服务端保存了用户拍摄/上传的图片），OCR文本不再作为题目
                if (record.imageUrl.isNotEmpty()) {
                    val fullImageUrl = "http://$serverAddress${record.imageUrl}"
                    AndroidView(
                            factory = { ctx ->
                                android.widget.ImageView(ctx).apply {
                                    scaleType = android.widget.ImageView.ScaleType.FIT_CENTER
                                    setBackgroundColor(android.graphics.Color.parseColor(if (AppDarkTheme) "#12122A" else "#FFFFFF"))
                                    com.bumptech.glide.Glide.with(ctx)
                                            .load(fullImageUrl)
                                            .placeholder(android.graphics.drawable.ColorDrawable(android.graphics.Color.parseColor(if (AppDarkTheme) "#2D2D44" else "#E9EDF4")))
                                            .into(this)
                                }
                            },
                            // 固定高度：保证图片加载前/后都有可见区域，避免高度为0导致图片不显示
                            modifier = Modifier.fillMaxWidth().height(180.dp)
                    )
                } else if (record.ocrText.isNotEmpty()) {
                    Text(
                        text = cleanHtmlText(record.ocrText),
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = 13.sp,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                
                if (record.solutionSteps.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = plainPreview(record.solutionSteps, 30),
                        color = Color(0xFF00D2FF).copy(alpha = 0.8f),
                        fontSize = 12.sp,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    if (isSelectMode) "点击切换选择" else "点击查看详情 ▶",
                    color = Color(0xFF00D2FF).copy(alpha = 0.6f),
                    fontSize = 11.sp
                )
            }
        }
    }
}

@Composable
fun HistoryDetailScreen(
    record: ApiService.HistoryRecord,
    onBack: () -> Unit,
    viewModel: MainViewModel? = null,
    serverAddress: String = "10.100.55.231:8000",
) {
    val displayTime = try {
        if (record.timestamp.isNotEmpty()) {
            record.timestamp.substring(0, 19).replace("T", " ")
        } else ""
    } catch (e: Exception) { record.timestamp }
    
    // Feature 17: 展开/收起状态
    var showSolutionSteps by remember { mutableStateOf(true) }
    var showFullSolution by remember { mutableStateOf(false) }
    // ⑦ 识别文本折叠
    var showOcrText by remember { mutableStateOf(false) }
    // ⑥ 完整解析（首次打开时按需把LaTeX代码块渲染成图片）
    var detailFullSolution by remember { mutableStateOf(record.fullSolution) }
    var renderingLatex by remember { mutableStateOf(false) }
    // ⑥ 掌握程度（读取当时AI解答记录的选项，可修改）
    var detailMastery by remember { mutableStateOf(record.masteryLevel) }
    var savingMastery by remember { mutableStateOf(false) }
    // ⑥ 历史详情追问（流式）
    var historyQa by remember { mutableStateOf<List<QAItem>>(emptyList()) }
    var askInput by remember { mutableStateOf("") }
    var pendingAsk by remember { mutableStateOf("") }
    var isAsking by remember { mutableStateOf(false) }
    val context = LocalContext.current
    
    // 打开详情时：若完整解析含LaTeX代码块，请求服务端渲染为图片（本地编译并缓存）
    LaunchedEffect(record.id) {
        if (record.fullSolution.contains("```")) {
            renderingLatex = true
            val rendered = viewModel?.apiService?.renderHistoryRecord(record.id) ?: ""
            if (rendered.isNotEmpty()) detailFullSolution = rendered
            renderingLatex = false
        }
    }
    
    // ⑥ 详情页追问：流式回答直接显示在气泡中
    fun sendHistoryAsk() {
        val q = askInput.trim()
        val vm = viewModel
        if (q.isEmpty() || isAsking || vm == null || record.sessionId.isEmpty()) return
        askInput = ""
        isAsking = true
        pendingAsk = ""
        val sb = StringBuilder()
        var answerRendered = false
        vm.viewModelScope.launch(Dispatchers.IO) {
            try {
                vm.apiService.askQuestionStream(
                        record.sessionId,
                        q,
                        onChunk = { chunk ->
                            appendStreamDelta(sb, chunk)
                            pendingAsk = sb.toString()
                        },
                        onRendered = { rendered ->
                            // ① 追问LaTeX已渲染：用渲染后的Markdown替换
                            answerRendered = true
                            sb.setLength(0)
                            sb.append(rendered)
                            pendingAsk = rendered
                        }
                )
                withContext(Dispatchers.Main) {
                    historyQa = historyQa + QAItem(q, sb.toString(), answerRendered)
                    pendingAsk = ""
                    isAsking = false
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    pendingAsk = ""
                    isAsking = false
                    Toast.makeText(context, "提问失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
    
    // ⑥ 详情页修改掌握程度
    fun saveDetailMastery(level: String) {
        val vm = viewModel
        if (savingMastery || vm == null || record.sessionId.isEmpty()) return
        savingMastery = true
        vm.viewModelScope.launch(Dispatchers.IO) {
            try {
                vm.apiService.saveMastery(record.sessionId, level)
                withContext(Dispatchers.Main) {
                    detailMastery = when (level) {
                        "completely_mastered" -> "完全掌握"
                        "partially_mastered" -> "部分掌握"
                        else -> "完全没掌握"
                    }
                    savingMastery = false
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    savingMastery = false
                    Toast.makeText(context, "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
    
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))
    ) {
        // Feature 14: fixed return bar
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Button(
                    onClick = onBack,
                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("← 返回列表", color = tC(Color.White, Color(0xFF16181D))) }
                
                Text(
                    "📝 记录详情",
                    color = tC(Color.White, Color(0xFF16181D)),
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold
                )
                
                Text("#${record.id}", color = Color(0xFF00D2FF).copy(alpha = 0.5f), fontSize = 12.sp)
            }
        }
        
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Card(
                colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                shape = RoundedCornerShape(8.dp)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("🕔 时间", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                        Text(displayTime, color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                    }
                    
                    if (record.grade.isNotEmpty() || record.subject.isNotEmpty() || record.difficulty.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (record.grade.isNotEmpty()) {
                                Text("📎 年级:", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                                Text(record.grade, color = Color(0xFF7B2FBE), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            }
                            if (record.subject.isNotEmpty()) {
                                Text("📉 学科:", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                                Text(record.subject, color = Color(0xFF2196F3), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            }
                            val diffColor = when (record.difficulty) {
                                "易", "较易" -> Color(0xFF4CAF50)
                                "中" -> Color(0xFFFF9800)
                                "较难", "难" -> Color(0xFFF44336)
                                else -> tC(Color.Gray, Color(0xFF5C6470))
                            }
                            Text(record.difficulty, color = diffColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                    
                    if (record.knowledgePoints.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text("🔖 知识点", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                        Spacer(modifier = Modifier.height(4.dp))
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            record.knowledgePoints.forEach { kp ->
                                Surface(
                                    color = Color(0xFF00D2FF).copy(alpha = 0.15f),
                                    shape = RoundedCornerShape(4.dp)
                                ) {
                                    Text(
                                        kp,
                                        color = Color(0xFF00D2FF),
                                        fontSize = 12.sp,
                                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                                    )
                                }
                            }
                        }
                    }
                }
            }
            
            if (record.ocrText.isNotEmpty()) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("📥 原题图片", color = Color(0xFF00D2FF), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(modifier = Modifier.height(6.dp))
                        // 显示原始题目图片（如可用）
                        if (record.imageUrl.isNotEmpty()) {
                            val fullImageUrl = "http://$serverAddress${record.imageUrl}"
                            AndroidView(
                                factory = { ctx ->
                                    android.widget.ImageView(ctx).apply {
                                        scaleType = android.widget.ImageView.ScaleType.FIT_CENTER
                                        setBackgroundColor(android.graphics.Color.parseColor(if (AppDarkTheme) "#1A1A2E" else "#FFFFFF"))
                                        com.bumptech.glide.Glide.with(ctx)
                                            .load(fullImageUrl)
                                            .placeholder(android.graphics.drawable.ColorDrawable(android.graphics.Color.parseColor(if (AppDarkTheme) "#2D2D44" else "#E9EDF4")))
                                            .into(this)
                                    }
                                },
                                // 固定高度：保证图片加载前/后都有可见区域
                                modifier = Modifier.fillMaxWidth().height(280.dp)
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                        }
                        // ⑦ 识别文本可折叠（与“解题思路/完整解析”一致）
                        Row(
                                modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable { showOcrText = !showOcrText },
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text("📝 识别文本", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                            Text(
                                    if (showOcrText) "▲ 收起" else "▼ 展开",
                                    color = Color(0xFF00D2FF),
                                    fontSize = 12.sp
                            )
                        }
                        if (showOcrText) {
                            Spacer(modifier = Modifier.height(4.dp))
                            // ⑧ 识别文本用Markdown渲染（斜体/公式/换行可正常显示）
                            if (record.ocrText.contains("$") || record.ocrText.contains("*") || record.ocrText.contains("\n")) {
                                MarkdownView(content = record.ocrText, fontSize = 13f)
                            } else {
                                Text(cleanHtmlText(record.ocrText), color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                            }
                        }
                    }
                }
            }
            
            if (record.solutionSteps.isNotEmpty()) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("💡 解题思路", color = Color(0xFF00D2FF), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(modifier = Modifier.height(6.dp))
                        
                        if (showSolutionSteps) {
                            MarkdownView(content = record.solutionSteps, fontSize = 14f)
                        } else {
                            Text(plainPreview(record.solutionSteps, 30), color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                        }
                        
                        TextButton(
                            onClick = { showSolutionSteps = !showSolutionSteps },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                if (showSolutionSteps) "▲ 收起解题思路" else "▼ 展开解题思路",
                                color = Color(0xFF00D2FF),
                                fontSize = 13.sp
                            )
                        }
                    }
                }
            }
            
            if (record.fullSolution.isNotEmpty()) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("📝 完整解析", color = Color(0xFF7B2FBE), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(modifier = Modifier.height(6.dp))
                        
                        if (showFullSolution) {
                            if (renderingLatex) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    CircularProgressIndicator(
                                            color = Color(0xFF7B2FBE),
                                            modifier = Modifier.size(16.dp),
                                            strokeWidth = 2.dp
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text("正在渲染LaTeX图片...", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp)
                                }
                            } else {
                                MarkdownView(content = detailFullSolution, fontSize = 14f)
                            }
                        } else {
                            Text(plainPreview(detailFullSolution, 30), color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                        }
                        
                        TextButton(
                            onClick = { showFullSolution = !showFullSolution },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                if (showFullSolution) "▲ 收起完整解析" else "▼ 展开完整解析",
                                color = Color(0xFF7B2FBE),
                                fontSize = 13.sp
                            )
                        }
                    }
                }
            }
            
            // ⑥ 掌握程度（读取当时AI解答时的选项，可点击修改）
            if (record.sessionId.isNotEmpty()) {
                Card(
                        colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                        shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("📊 掌握程度", color = Color(0xFF4CAF50), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        Spacer(modifier = Modifier.height(6.dp))
                        if (detailMastery.isNotEmpty()) {
                            Text(
                                    "✅ 已记录：$detailMastery（点击可修改）",
                                    color = Color(0xFF4CAF50),
                                    fontSize = 13.sp,
                                    modifier = Modifier.padding(bottom = 6.dp)
                            )
                        }
                        Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Button(
                                    onClick = { saveDetailMastery("completely_mastered") },
                                    enabled = !savingMastery,
                                    modifier = Modifier.weight(1f).height(40.dp),
                                    colors = ButtonDefaults.buttonColors(
                                            containerColor = if (detailMastery == "完全掌握") Color(0xFF2E7D32) else Color(0xFF4CAF50)
                                    )
                            ) { Text("✅ 完全掌握", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold) }
                            Button(
                                    onClick = { saveDetailMastery("partially_mastered") },
                                    enabled = !savingMastery,
                                    modifier = Modifier.weight(1f).height(40.dp),
                                    colors = ButtonDefaults.buttonColors(
                                            containerColor = if (detailMastery == "部分掌握") Color(0xFFE65100) else Color(0xFFFF9800)
                                    )
                            ) { Text("⚠️ 部分掌握", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold) }
                            Button(
                                    onClick = { saveDetailMastery("not_mastered") },
                                    enabled = !savingMastery,
                                    modifier = Modifier.weight(1f).height(40.dp),
                                    colors = ButtonDefaults.buttonColors(
                                            containerColor = if (detailMastery == "完全没掌握") Color(0xFFC62828) else Color(0xFFF44336)
                                    )
                            ) { Text("❌ 没掌握", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold) }
                        }
                    }
                }
            }
            
            // ⑥ 历史详情追问（与AI解答同款流式气泡）
            if (record.sessionId.isNotEmpty()) {
                Card(
                        colors = CardDefaults.cardColors(containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))),
                        shape = RoundedCornerShape(8.dp)
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text("💬 追问本题", color = Color(0xFF00D2FF), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        
                        historyQa.forEach { item ->
                            Spacer(modifier = Modifier.height(8.dp))
                            // 用户问题（右对齐）
                            Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
                                Surface(
                                        color = Color(0xFF1E3A5F),
                                        shape = RoundedCornerShape(10.dp),
                                        modifier = Modifier.widthIn(max = 280.dp)
                                ) {
                                    Text(
                                            item.question,
                                            color = Color.White,
                                            fontSize = 13.sp,
                                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                    )
                                }
                            }
                            Spacer(modifier = Modifier.height(6.dp))
                            // AI回答（左对齐）；① Markdown渲染，② 未渲染图形占位
                            Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.Start) {
                                Surface(
                                        color = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                        shape = RoundedCornerShape(10.dp),
                                        modifier = Modifier.widthIn(max = 320.dp)
                                ) {
                                    val ansContent =
                                            if (item.rendered) item.answer else replaceLatexWithPlaceholder(item.answer)
                                    if (item.rendered || shouldRenderMarkdown(ansContent)) {
                                        MarkdownView(
                                                content = ansContent,
                                                fontSize = 13f,
                                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                        )
                                    } else {
                                        ColorText(
                                                ansContent,
                                                color = tC(Color(0xFFE0E0E0), Color(0xFF3A3F47)),
                                                fontSize = 13.sp,
                                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                        )
                                    }
                                }
                            }
                        }
                        
                        if (pendingAsk.isNotEmpty()) {
                            Spacer(modifier = Modifier.height(6.dp))
                            Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.Start) {
                                Surface(
                                        color = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                        shape = RoundedCornerShape(10.dp),
                                        modifier = Modifier.widthIn(max = 320.dp)
                                ) {
                                    // ① 流式中只显示纯文本（等完整回答后再Markdown渲染）
                                    ColorText(
                                            pendingAsk,
                                            color = tC(Color(0xFFE0E0E0), Color(0xFF3A3F47)),
                                            fontSize = 13.sp,
                                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                                    )
                                }
                            }
                        }
                        
                        Spacer(modifier = Modifier.height(10.dp))
                        Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically
                        ) {
                            OutlinedTextField(
                                    value = askInput,
                                    onValueChange = { askInput = it },
                                    modifier = Modifier.weight(1f).height(46.dp),
                                    placeholder = { Text("输入你的问题...", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 13.sp) },
                                    textStyle = TextStyle(color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp),
                                    colors = OutlinedTextFieldDefaults.colors(
                                            focusedBorderColor = Color(0xFF00D2FF),
                                            unfocusedBorderColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)),
                                            cursorColor = Color(0xFF00D2FF)
                                    ),
                                    singleLine = true,
                                    enabled = !isAsking
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Button(
                                    onClick = { sendHistoryAsk() },
                                    enabled = askInput.isNotBlank() && !isAsking,
                                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
                            ) { Text("发送", color = Color.Black, fontSize = 13.sp, fontWeight = FontWeight.Bold) }
                        }
                    }
                }
            }
            
            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}

@Composable
fun SettingsDialog(viewModel: MainViewModel, onDismiss: () -> Unit) {
    val gestureEnabled by viewModel.gestureEnabled.collectAsState()
    val voiceEnabled by viewModel.voiceEnabled.collectAsState()
    val serverAddress by viewModel.serverAddress.collectAsState()
    val fontSize by viewModel.fontSize.collectAsState()
    val showModules by viewModel.showModules.collectAsState()
    val historyStartDate by viewModel.historyStartDate.collectAsState()
    val historyEndDate by viewModel.historyEndDate.collectAsState()
    val loggedInUsername by viewModel.loggedInUsername.collectAsState()
    // ③ 用collectAsState观察模型设置，点击后立即变色生效
    val llmProvider by viewModel.llmProvider.collectAsState()
    val llmModel by viewModel.llmModel.collectAsState()
    val ocrMode by viewModel.ocrMode.collectAsState()
    val visionModel by viewModel.visionModel.collectAsState()
    // ②④十一⑦ 扩展设置
    val answerStyle by viewModel.answerStyle.collectAsState()
    val searchEnabled by viewModel.searchEnabled.collectAsState()
    val thinkingEnabled by viewModel.thinkingEnabled.collectAsState()
    val themeMode by viewModel.themeMode.collectAsState()
    val dialect by viewModel.dialect.collectAsState()
    val grade by viewModel.grade.collectAsState()

    AlertDialog(
            onDismissRequest = onDismiss,
            title = {
                Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("⚙️ 设置", color = tC(Color.White, Color(0xFF16181D)))
                    TextButton(onClick = onDismiss) { Text("关闭", color = Color(0xFF00D2FF)) }
                }
            },
            text = {
                Column(
                        modifier = Modifier.fillMaxWidth().verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    SettingSwitch(
                            title = "🖐️ 手势识别",
                            subtitle = "开启后可在主页通过手势触发功能",
                            checked = gestureEnabled,
                            onCheckedChange = { viewModel.gestureEnabled.value = it }
                    )

                    SettingSwitch(
                            title = "🎤 语音识别",
                            subtitle = "暂未开放，敬请期待",
                            checked = false,
                            onCheckedChange = {},
                            enabled = false
                    )

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    // ② 回答风格（各风格对应不同temperature）
                    Text(
                            "🎨 回答风格",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(
                            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf(
                                "formal" to "严谨规范",
                                "plain" to "通俗易懂",
                                "concise" to "简洁精炼",
                                "lively" to "活泼有趣",
                                "dialect" to "方言",
                        ).forEach { (id, label) ->
                            FilterChip(
                                    selected = answerStyle == id,
                                    onClick = {
                                        viewModel.answerStyle.value = id
                                        viewModel.saveExtraSettings()
                                    },
                                    label = { Text(label, fontSize = 12.sp) },
                                    colors = FilterChipDefaults.filterChipColors(
                                            selectedContainerColor = Color(0xFF7B2FBE)
                                    )
                            )
                        }
                    }

                    // ③ 方言二级选择（风格选“方言”时显示）
                    if (answerStyle == "dialect") {
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                                "🗣️ 方言名称",
                                color = tC(Color.White, Color(0xFF16181D)),
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Bold
                        )
                        Row(
                                modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            listOf("四川话", "东北话", "粤语", "上海话", "天津话", "陕西话", "河南话", "湖南话").forEach { d ->
                                FilterChip(
                                        selected = dialect == d,
                                        onClick = {
                                            viewModel.dialect.value = d
                                            viewModel.saveDialectGrade()
                                        },
                                        label = { Text(d, fontSize = 12.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFF00ACC1)
                                        )
                                )
                            }
                        }
                    }

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    // ④ 年级设置（投给AI + 应用于AI报告）
                    Text(
                            "🏫 年级设置",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(
                            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf("" to "不限", "小学" to "小学", "初中" to "初中", "高中" to "高中", "考研" to "考研").forEach { (id, label) ->
                            FilterChip(
                                    selected = grade == id,
                                    onClick = {
                                        viewModel.grade.value = id
                                        viewModel.saveDialectGrade()
                                    },
                                    label = { Text(label, fontSize = 12.sp) },
                                    colors = FilterChipDefaults.filterChipColors(
                                            selectedContainerColor = Color(0xFF4CAF50)
                                    )
                            )
                        }
                    }

                    // ④ 搜题开关
                    SettingSwitch(
                            title = "🔍 题库搜索",
                            subtitle = "解题时自动在题库中搜索相似题目（耗时会增加）",
                            checked = searchEnabled,
                            onCheckedChange = {
                                viewModel.searchEnabled.value = it
                                viewModel.saveExtraSettings()
                            }
                    )

                    // 十一 思考模式开关（仅作用于完整解析，DeepSeek链路）
                    SettingSwitch(
                            title = "🧠 思考模式",
                            subtitle = "生成完整解析前先输出思考过程（显示在完整解析上方）",
                            checked = thinkingEnabled,
                            onCheckedChange = {
                                viewModel.thinkingEnabled.value = it
                                viewModel.saveExtraSettings()
                            }
                    )

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    // ⑦ 主题模式（跟随系统/亮色/暗色）
                    Text(
                            "🌓 主题模式",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("system" to "跟随系统", "light" to "亮色", "dark" to "暗色").forEach { (id, label) ->
                            FilterChip(
                                    selected = themeMode == id,
                                    onClick = {
                                        viewModel.themeMode.value = id
                                        viewModel.saveExtraSettings()
                                    },
                                    label = { Text(label, fontSize = 12.sp) },
                                    colors = FilterChipDefaults.filterChipColors(
                                            selectedContainerColor = Color(0xFF2196F3)
                                    )
                            )
                        }
                    }

                    Text(
                            "📝 字体大小: ${fontSize.toInt()}sp",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("小", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp)
                        Slider(
                                value = fontSize,
                                onValueChange = { viewModel.fontSize.value = it },
                                valueRange = 12f..28f,
                                modifier = Modifier.weight(1f),
                                colors = SliderDefaults.colors(thumbColor = Color(0xFF00D2FF))
                        )
                        Text("大", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 28.sp)
                    }
                    // 字体预览
                    Text(
                        "预览文字 ABC 123 学习助手",
                        color = tC(Color.White, Color(0xFF16181D)),
                        fontSize = fontSize.sp,
                        modifier = Modifier.padding(vertical = 4.dp)
                    )

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    Text(
                            "📂 显示模块",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    showModules.forEach { (key, value) ->
                        SettingSwitch(
                                title =
                                        when (key) {
                                            "solution_steps" -> "解题思路"
                                            "full_solution" -> "完整解析"
                                            "mind_map" -> "思维导图"
                                            "suggested_questions" -> "延伸问题"
                                            "mistakes" -> "易错点详解"
                                            "extension" -> "知识拓展"
                                            else -> key
                                        },
                                checked = value,
                                onCheckedChange = {
                                    viewModel.showModules.value =
                                            showModules.toMutableMap().apply { put(key, it) }
                                }
                        )
                    }

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    // ⑤ AI模型设置：大语言模型 + 视觉/OCR模型（千问）
                    Text(
                            "🤖 大语言模型",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                                selected = llmProvider == "deepseek",
                                onClick = {
                                    viewModel.llmProvider.value = "deepseek"
                                    viewModel.llmModel.value = ""
                                    viewModel.saveAiSettings()
                                },
                                label = { Text("DeepSeek", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = Color(0xFF2196F3)
                                )
                        )
                        FilterChip(
                                selected = llmProvider == "qwen",
                                onClick = {
                                    viewModel.llmProvider.value = "qwen"
                                    viewModel.llmModel.value = "qwen3.8-max"
                                    viewModel.saveAiSettings()
                                },
                                label = { Text("千问 Qwen", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = Color(0xFFFF9800)
                                )
                        )
                    }
                    if (llmProvider == "qwen") {
                        Row(
                                modifier = Modifier.horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            listOf("qwen3.8-max", "qwen3.7-plus", "qwen3.8-flash", "deepseek-v4-flash-0731", "kimi-k3", "glm-5.3", "MiniMax-M3").forEach { m ->
                                FilterChip(
                                        selected = llmModel == m,
                                        onClick = {
                                            viewModel.llmModel.value = m
                                            viewModel.saveAiSettings()
                                        },
                                        label = { Text(m, fontSize = 10.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFFFF9800)
                                        )
                                )
                            }
                        }
                    }

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    Text(
                            "👁️ 文字识别（OCR）",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                                selected = ocrMode == "paddle",
                                onClick = {
                                    viewModel.ocrMode.value = "paddle"
                                    viewModel.saveAiSettings()
                                },
                                label = { Text("PaddleOCR\n本地 & API", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = Color(0xFF4CAF50)
                                )
                        )
                        FilterChip(
                                selected = ocrMode == "qwen",
                                onClick = {
                                    viewModel.ocrMode.value = "qwen"
                                    viewModel.visionModel.value = "qwen3.8-max"
                                    viewModel.saveAiSettings()
                                },
                                label = { Text("AI视觉\n千问/DeepSeek", fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = Color(0xFFFF9800)
                                )
                        )
                    }
                    if (ocrMode == "qwen") {
                        Row(
                                modifier = Modifier.horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            listOf("qwen3.8-max", "qwen3.7-plus", "qwen3.5-omni-plus", "kimi-k3", "deepseek-v4-flash-vision-exp").forEach { m ->
                                FilterChip(
                                        selected = visionModel == m,
                                        onClick = {
                                            viewModel.visionModel.value = m
                                            viewModel.saveAiSettings()
                                        },
                                        label = { Text(m, fontSize = 10.sp) },
                                        colors = FilterChipDefaults.filterChipColors(
                                                selectedContainerColor = Color(0xFFFF9800)
                                        )
                                )
                            }
                        }
                    }

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    Text(
                            "📅 历史记录范围",
                            color = tC(Color.White, Color(0xFF16181D)),
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                                value = historyStartDate,
                                onValueChange = { viewModel.historyStartDate.value = it },
                                modifier = Modifier.weight(1f),
                                placeholder = { Text("开始日期 (2026-01-01)", color = tC(Color.Gray, Color(0xFF5C6470))) },
                                singleLine = true,
                                colors = darkTextFieldColors()
                        )
                        OutlinedTextField(
                                value = historyEndDate,
                                onValueChange = { viewModel.historyEndDate.value = it },
                                modifier = Modifier.weight(1f),
                                placeholder = { Text("截止日期 (2026-12-31)", color = tC(Color.Gray, Color(0xFF5C6470))) },
                                singleLine = true,
                                colors = darkTextFieldColors()
                        )
                    }

                    Divider(color = tC(Color.White.copy(alpha = 0.2f), Color(0xFF16181D).copy(alpha = 0.2f)))

                    Button(
                            onClick = { viewModel.clearHistory() },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF44336))
                    ) { Text("🗑️ 清除历史记录") }
                    
                    Spacer(modifier = Modifier.height(8.dp))
                    
                    Button(
                            onClick = { viewModel.logout(); onDismiss() },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF9800))
                    ) { Text("🚪 退出登录 (${loggedInUsername})") }
                }
            },
            confirmButton = {},
            containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

@Composable
fun SettingSwitch(
        title: String,
        subtitle: String = "",
        checked: Boolean,
        onCheckedChange: (Boolean) -> Unit,
        enabled: Boolean = true
) {
    Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                    title,
                    color = if (enabled) tC(Color.White, Color(0xFF16181D)) else tC(Color.Gray, Color(0xFF5C6470)),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold
            )
            if (subtitle.isNotEmpty()) {
                Text(subtitle, color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 11.sp)
            }
        }
        Switch(
                checked = checked,
                onCheckedChange = onCheckedChange,
                enabled = enabled,
                colors = SwitchDefaults.colors(checkedThumbColor = Color(0xFF00D2FF))
        )
    }
}

@Composable
fun darkTextFieldColors() =
        OutlinedTextFieldDefaults.colors(
                focusedTextColor = tC(Color.White, Color(0xFF16181D)),
                unfocusedTextColor = tC(Color.White, Color(0xFF16181D)),
                focusedBorderColor = Color(0xFF00D2FF),
                unfocusedBorderColor = tC(Color.Gray, Color(0xFF5C6470)),
        )

// ==================== Feature 11: 番茄钟设置弹窗（点击顶部计时打开） ====================

@Composable
fun PomodoroSettingsDialog(viewModel: MainViewModel, onDismiss: () -> Unit) {
    val work by viewModel.pomodoroWorkDuration.collectAsState()
    val rest by viewModel.pomodoroRestDuration.collectAsState()
    val mode by viewModel.pomodoroMode.collectAsState()
    val running by viewModel.pomodoroRunning.collectAsState()
    val tomatoEnabled by viewModel.tomatoEnabled.collectAsState()

    var workInput by remember(work) { mutableStateOf(work.toString()) }
    var restInput by remember(rest) { mutableStateOf(rest.toString()) }
    // 番茄钟 AI 推荐时长
    var recommendReason by remember { mutableStateOf("") }
    var recommending by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val pickImageLauncher = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            recommending = true
            recommendReason = ""
            viewModel.viewModelScope.launch(Dispatchers.IO) {
                try {
                    val bytes = context.contentResolver.openInputStream(uri)?.readBytes()
                    val b64 = if (bytes != null) android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP) else ""
                    val r = viewModel.apiService.recommendPomodoro("", "", b64)
                    withContext(Dispatchers.Main) {
                        recommending = false
                        if (r.status == "ok") {
                            workInput = r.durationMinutes.toString()
                            recommendReason = "推荐 ${r.durationMinutes} 分钟：${r.reason}"
                        } else {
                            recommendReason = "推荐失败：${r.message}"
                        }
                    }
                } catch (e: Exception) {
                    withContext(Dispatchers.Main) {
                        recommending = false
                        recommendReason = "推荐失败：${e.message}"
                    }
                }
            }
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("🍅 番茄钟设置", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth().verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                SettingSwitch(
                    title = "启用番茄钟",
                    subtitle = "关闭后不进行任何计时",
                    checked = tomatoEnabled,
                    onCheckedChange = { viewModel.tomatoEnabled.value = it }
                )

                Text("⏱ 计时模式", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp, fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = !mode,
                        onClick = { viewModel.setPomodoroMode(false) },
                        label = { Text("⏳ 倒计时", fontSize = 13.sp) },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Color(0xFFFF5722))
                    )
                    FilterChip(
                        selected = mode,
                        onClick = { viewModel.setPomodoroMode(true) },
                        label = { Text("⏱ 正计时", fontSize = 13.sp) },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Color(0xFF00D2FF))
                    )
                }

                if (!mode) {
                    Text("工作时长（分钟，可输入）", color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                    OutlinedTextField(
                        value = workInput,
                        onValueChange = { workInput = it.filter { c -> c.isDigit() }.take(3) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        colors = darkTextFieldColors()
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf(15, 25, 45, 60, 90).forEach { min ->
                            TextButton(onClick = { workInput = min.toString() }) {
                                Text("${min}分", color = Color(0xFF00D2FF), fontSize = 13.sp)
                            }
                        }
                    }

                    // 拍照上传 AI 推荐做题时长
                    Button(
                            onClick = { pickImageLauncher.launch("image/*") },
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !recommending,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF7B2FBE))
                    ) {
                        Text(if (recommending) "🤖 正在推荐..." else "🤖 拍照上传，AI 推荐时长", color = Color.White, fontSize = 13.sp)
                    }
                    if (recommendReason.isNotEmpty()) {
                        Text(
                                recommendReason,
                                color = Color(0xFF00D2FF),
                                fontSize = 12.sp,
                                lineHeight = 16.sp
                        )
                    }

                    Text("休息时长（分钟，可输入）", color = tC(Color.White, Color(0xFF16181D)), fontSize = 13.sp)
                    OutlinedTextField(
                        value = restInput,
                        onValueChange = { restInput = it.filter { c -> c.isDigit() }.take(2) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        colors = darkTextFieldColors()
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf(5, 10, 15, 30).forEach { min ->
                            TextButton(onClick = { restInput = min.toString() }) {
                                Text("${min}分", color = Color(0xFF4CAF50), fontSize = 13.sp)
                            }
                        }
                    }
                } else {
                    Text(
                        "正计时：从 0 开始累计学习时间，可暂停/重置，适合自由学习场景",
                        color = tC(Color.Gray, Color(0xFF5C6470)),
                        fontSize = 12.sp
                    )
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    TextButton(onClick = { viewModel.togglePomodoro() }) {
                        Text(if (running) "⏸ 暂停" else "▶ 继续", color = Color(0xFF00D2FF), fontSize = 14.sp)
                    }
                    TextButton(onClick = { viewModel.resetPomodoro() }) {
                        Text("🔄 重置", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 14.sp)
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val w = workInput.toIntOrNull() ?: 25
                    val r = restInput.toIntOrNull() ?: 5
                    viewModel.applyPomodoroSettings(w, r, mode)
                    onDismiss()
                },
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
            ) {
                Text("✅ 应用", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
            }
        },
        containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

// ==================== Feature 11: 番茄钟休息全屏界面 ====================

@Composable
fun PomodoroRestScreen(viewModel: MainViewModel) {
    val restTime by viewModel.restTimeRemaining.collectAsState()
    
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                "☕ 休息时间",
                color = tC(Color.White, Color(0xFF16181D)),
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(24.dp))
            Text(
                formatTime(restTime),
                color = Color(0xFF00D2FF),
                fontSize = 72.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(24.dp))
            Text(
                "休息一下，让眼睛放松",
                color = tC(Color.Gray, Color(0xFF5C6470)),
                fontSize = 18.sp
            )
            Spacer(modifier = Modifier.height(32.dp))
            Button(
                onClick = { viewModel.skipRest() },
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
            ) {
                Text("跳过休息", color = Color.White, fontSize = 16.sp)
            }
        }
    }
}

// ==================== Feature 4: 掌握程度弹窗 ====================

@Composable
fun MasteryDialog(onSelect: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = {},
        title = { Text("📊 掌握程度", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("你对这道题的掌握程度如何？", color = tC(Color.White.copy(alpha = 0.8f), Color(0xFF16181D).copy(alpha = 0.8f)), fontSize = 14.sp)
                
                Button(
                    onClick = { onSelect("completely_mastered") },
                    modifier = Modifier.fillMaxWidth().height(50.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF4CAF50)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Text("✅ 完全掌握", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                }
                
                Button(
                    onClick = { onSelect("partially_mastered") },
                    modifier = Modifier.fillMaxWidth().height(50.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF9800)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Text("⚠️ 部分掌握", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                }
                
                Button(
                    onClick = { onSelect("not_mastered") },
                    modifier = Modifier.fillMaxWidth().height(50.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF44336)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Text("❌ 完全没掌握", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                }
            }
        },
        confirmButton = {},
        containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

// ==================== Feature 9/10: 倒计时确认弹窗 ====================

@Composable
fun CountdownConfirmDialog(
    title: String,
    content: String,
    onConfirm: () -> Unit,
    onCancel: () -> Unit
) {
    var countdown by remember { mutableStateOf(8) }
    var isCounting by remember { mutableStateOf(true) }
    
    LaunchedEffect(Unit) {
        while (isCounting && countdown > 0) {
            delay(1000)
            countdown--
        }
        if (isCounting && countdown == 0) {
            isCounting = false
            onConfirm()
        }
    }
    
    AlertDialog(
        onDismissRequest = {},
        title = { Text(title, color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                if (content.isNotEmpty()) {
                    Surface(
                        color = tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)),
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        // Markdown渲染（公式/加粗等可正常显示），高度受限可滚动
                        Box(
                            modifier = Modifier.fillMaxWidth().heightIn(max = 400.dp)
                        ) {
                            MarkdownView(
                                content = content,
                                fontSize = 13f,
                                modifier = Modifier.fillMaxWidth()
                            )
                        }
                    }
                    Spacer(modifier = Modifier.height(12.dp))
                }
                Text(
                    if (countdown > 0) "将在 ${countdown} 秒后自动确认..." else "正在处理...",
                    color = tC(Color.Gray, Color(0xFF5C6470)),
                    fontSize = 13.sp
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    isCounting = false
                    onConfirm()
                },
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF00D2FF))
            ) {
                Text(
                    if (countdown > 0) "确认(${countdown})" else "确认",
                    color = tC(Color.White, Color(0xFF16181D)),
                    fontSize = 14.sp
                )
            }
        },
        dismissButton = {
            TextButton(onClick = {
                isCounting = false
                onCancel()
            }) {
                Text("取消", color = Color(0xFFF44336), fontSize = 14.sp)
            }
        },
        containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}

// ==================== Feature 20: GeoGebra 图形绘制 ====================

@Composable
fun GeoGebraScreen(url: String = "", onBack: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize().background(tC(Color(0xFF1A1A2E), Color(0xFFF2F4F8)))) {
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Button(onClick = onBack) { Text("← 返回") }
                Text("📐 数学图形", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold)
                Spacer(modifier = Modifier.width(60.dp))
            }
        }

        if (url.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = Color(0xFF00D2FF))
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("正在生成数学图形...", color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp)
                }
            }
            return@Column
        }

        // WebView引用，供缩放按钮控制（JS缩放，避免页面user-scalable=no导致缩放失效）
        var webViewRef by remember { mutableStateOf<android.webkit.WebView?>(null) }
        var zoomScale by remember { mutableStateOf(1f) }

        Box(modifier = Modifier.weight(1f)) {
            AndroidView(
                    factory = { ctx ->
                        android.webkit.WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.allowFileAccess = true
                            settings.domStorageEnabled = true
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            settings.builtInZoomControls = false
                            settings.displayZoomControls = false
                            settings.setSupportZoom(false)
                            loadUrl(url)
                            webViewRef = this
                        }
                    },
                    modifier = Modifier.fillMaxSize()
            )
        }

        // 缩放控制条
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = tC(Color(0xFF16213E), Color(0xFFFFFFFF)),
            shadowElevation = 4.dp
        ) {
            Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically
            ) {
                Button(
                        onClick = {
                            zoomScale = (zoomScale * 0.8f).coerceAtLeast(0.5f)
                            webViewRef?.evaluateJavascript("document.body.style.zoom = '${zoomScale}';", null)
                        },
                        modifier = Modifier.size(width = 64.dp, height = 40.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("−", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp) }
                Text("缩放", color = tC(Color.Gray, Color(0xFF5C6470)), fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp))
                Button(
                        onClick = {
                            zoomScale = (zoomScale * 1.25f).coerceAtMost(5f)
                            webViewRef?.evaluateJavascript("document.body.style.zoom = '${zoomScale}';", null)
                        },
                        modifier = Modifier.size(width = 64.dp, height = 40.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                ) { Text("+", color = tC(Color.White, Color(0xFF16181D)), fontSize = 20.sp) }
                Spacer(modifier = Modifier.width(12.dp))
                TextButton(onClick = {
                    zoomScale = 1f
                    webViewRef?.evaluateJavascript("document.body.style.zoom = '1';", null)
                    webViewRef?.reload()
                }) {
                    Text("🔄 适应", color = Color(0xFF00D2FF), fontSize = 13.sp)
                }
            }
        }
    }
}


// ==================== ④ 截图 / 导出（PDF/Word 由服务端生成） ====================
/** 把当前 Activity 画面截图为 PNG 存到相册，返回保存路径（失败返回null） */
fun captureScreenToGallery(context: android.content.Context): String? {
    try {
        val activity = context as? android.app.Activity ?: return null
        val window = activity.window
        val bitmap = android.graphics.Bitmap.createBitmap(window.decorView.width, window.decorView.height, android.graphics.Bitmap.Config.ARGB_8888)
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            // PixelCopy 截取当前可见窗口（不含滚动区外的内容）
            val latch = java.util.concurrent.CountDownLatch(1)
            var ok = false
            android.view.PixelCopy.request(window, bitmap, { copyResult ->
                ok = copyResult == android.view.PixelCopy.SUCCESS
                latch.countDown()
            }, android.os.Handler(android.os.Looper.getMainLooper()))
            latch.await(3, java.util.concurrent.TimeUnit.SECONDS)
            if (!ok) return null
        } else {
            val canvas = android.graphics.Canvas(bitmap)
            window.decorView.draw(canvas)
        }
        val name = "learntogether_${System.currentTimeMillis()}.png"
        return saveBitmapToGallery(context, bitmap, name, "image/png")
    } catch (e: Exception) {
        android.util.Log.e("Export", "截图失败: ${e.message}")
        return null
    }
}

/** 保存位图到相册（Android 10+ 用 MediaStore，否则存 DCIM 目录），返回路径 */
fun saveBitmapToGallery(context: android.content.Context, bitmap: android.graphics.Bitmap, fileName: String, mimeType: String): String? {
    return try {
        if (android.os.Build.VERSION.SDK_INT >= 29) {
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, fileName)
                put(android.provider.MediaStore.Images.Media.MIME_TYPE, mimeType)
                put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/LearningAssistant")
            }
            val resolver = context.contentResolver
            val uri = resolver.insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return null
            resolver.openOutputStream(uri)?.use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            uri.toString()
        } else {
            val dir = android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_PICTURES)
            val sub = java.io.File(dir, "LearningAssistant")
            if (!sub.exists()) sub.mkdirs()
            val f = java.io.File(sub, fileName)
            java.io.FileOutputStream(f).use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
            f.absolutePath
        }
    } catch (e: Exception) {
        android.util.Log.e("Export", "保存图片失败: ${e.message}")
        null
    }
}

/** 通过系统分享/打开一个文件 */
fun shareFile(context: android.content.Context, path: String, mimeType: String) {
    try {
        val f = java.io.File(path)
        val uri = androidx.core.content.FileProvider.getUriForFile(context, context.packageName + ".provider", f)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = mimeType
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(Intent.createChooser(intent, "导出/分享"))
    } catch (e: Exception) {
        android.widget.Toast.makeText(context, "文件已保存：$path", android.widget.Toast.LENGTH_LONG).show()
    }
}

/** ④ 通用导出操作行：截图 / 导出PDF / 导出Word（数据报告不允许PDF时 allowPdf=false） */
@Composable
fun ExportActions(
        viewModel: MainViewModel?,
        title: String,
        content: String,
        allowPdf: Boolean = true,
) {
    val context = LocalContext.current
    Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Button(
                onClick = {
                    val saved = captureScreenToGallery(context)
                    Toast.makeText(context, if (saved != null) "截图已保存到相册" else "截图失败", Toast.LENGTH_SHORT).show()
                },
                colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
        ) { Text("📷 截图", fontSize = 12.sp) }
        if (allowPdf) {
            Button(
                    onClick = {
                        if (viewModel != null && content.isNotBlank()) {
                            viewModel.exportContent(title, content, "pdf") { path, err ->
                                if (path != null) shareFile(context, path, "application/pdf")
                                else Toast.makeText(context, err ?: "导出失败", Toast.LENGTH_SHORT).show()
                            }
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
            ) { Text("📄 PDF", fontSize = 12.sp) }
        }
        Button(
                onClick = {
                    if (viewModel != null && content.isNotBlank()) {
                        viewModel.exportContent(title, content, "word") { path, err ->
                            if (path != null) shareFile(context, path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                            else Toast.makeText(context, err ?: "导出失败", Toast.LENGTH_SHORT).show()
                        }
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
        ) { Text("📝 Word", fontSize = 12.sp) }
    }
}

// ==================== ② 报告时间范围选择弹窗 ====================
@Composable
fun ReportRangeDialog(onSelect: (Int) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("📅 选择统计时间范围", color = tC(Color.White, Color(0xFF16181D)), fontSize = 18.sp, fontWeight = FontWeight.Bold) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(7 to "最近 7 天", 30 to "最近 30 天", 90 to "最近 90 天", 0 to "全部历史").forEach { (days, label) ->
                        Button(
                                onClick = { onSelect(days) },
                                modifier = Modifier.fillMaxWidth().height(48.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = tC(Color(0xFF2D2D44), Color(0xFFE9EDF4)))
                        ) { Text(label, color = tC(Color.White, Color(0xFF16181D)), fontSize = 14.sp) }
                    }
                }
            },
            confirmButton = {},
            dismissButton = {
                TextButton(onClick = onDismiss) { Text("取消", color = tC(Color.Gray, Color(0xFF5C6470))) }
            },
            containerColor = tC(Color(0xFF16213E), Color(0xFFFFFFFF))
    )
}
