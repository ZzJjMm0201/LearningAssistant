package com.gyyz.assistant.viewmodel

import android.graphics.Bitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.gyyz.assistant.network.ApiService
import com.gyyz.assistant.network.SSEClient
import com.gyyz.assistant.network.SolveEvent
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

/**
 * 解题ViewModel - 管理解题全流程
 */
class SolveViewModel : ViewModel() {
    
    companion object {
        private const val BASE_URL = "http://10.100.55.167:8000"
    }
    
    private val apiService = ApiService()
    private val sseClient = SSEClient(BASE_URL)
    
    // UI状态
    private val _uiState = MutableStateFlow(SolveUiState())
    val uiState: StateFlow<SolveUiState> = _uiState.asStateFlow()
    
    private var sseJob: Job? = null
    
    /**
     * 开始解题
     */
    fun startSolve(photoBitmap: Bitmap) {
        _uiState.value = SolveUiState(
            stage = SolveUiStage.UPLOADING,
            statusMessage = "正在上传题目..."
        )
        
        viewModelScope.launch {
            try {
                // 压缩图片
                val bytes = compressBitmap(photoBitmap)
                
                // 上传并获取request_id
                val requestId = apiService.startSolve(bytes)
                
                _uiState.value = _uiState.value.copy(
                    requestId = requestId,
                    statusMessage = "正在处理..."
                )
                
                // 连接SSE流
                sseJob?.cancel()
                sseJob = viewModelScope.launch {
                    sseClient.connectSolveStream(requestId).collect { event ->
                        handleSolveEvent(event.toSolveEvent() ?: return@collect)
                    }
                }
                
            } catch (e: Exception) {
                _uiState.value = _uiState.value.copy(
                    stage = SolveUiStage.ERROR,
                    statusMessage = "请求失败: ${e.message}"
                )
            }
        }
    }
    
    /**
     * 处理解题事件
     */
    private fun handleSolveEvent(event: SolveEvent) {
        val currentState = _uiState.value
        
        when (event.stage) {
            "info" -> {
                _uiState.value = currentState.copy(
                    statusMessage = event.content as? String ?: currentState.statusMessage
                )
            }
            "ocr_complete" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.ANALYZING,
                    statusMessage = "正在分析题目..."
                )
            }
            "question_info" -> {
                _uiState.value = currentState.copy(
                    questionInfo = event.content?.toString() ?: currentState.questionInfo
                )
            }
            "solution_steps" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.DISPLAY_STEPS,
                    solutionSteps = event.content as? String ?: currentState.solutionSteps
                )
            }
            "solution", "solution_rendered" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.DISPLAY_FULL,
                    fullSolution = event.content as? String ?: currentState.fullSolution
                )
            }
            "mindmap" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.DISPLAY_MINDMAP,
                    mindMap = event.content as? String ?: currentState.mindMap
                )
            }
            "suggested_questions" -> {
                val questions = (event.content as? List<*>)?.mapNotNull { it?.toString() } ?: emptyList()
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.INTERACTIVE,
                    suggestedQuestions = questions
                )
            }
            "error" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.ERROR,
                    statusMessage = event.content as? String ?: "未知错误"
                )
            }
            "complete" -> {
                _uiState.value = currentState.copy(
                    stage = SolveUiStage.COMPLETED,
                    statusMessage = "解答完成"
                )
                sseJob?.cancel()
            }
        }
    }
    
    /**
     * 继续提问
     */
    fun askQuestion(question: String) {
        viewModelScope.launch {
            try {
                val requestId = _uiState.value.requestId
                val answer = apiService.askQuestion(requestId, question)
                // 追加到对话中
                _uiState.value = _uiState.value.copy(
                    fullSolution = _uiState.value.fullSolution + "\n\n**Q:** $question\n**A:** $answer"
                )
            } catch (e: Exception) {
                _uiState.value = _uiState.value.copy(
                    statusMessage = "提问失败: ${e.message}"
                )
            }
        }
    }
    
    /**
     * 压缩Bitmap
     */
    private fun compressBitmap(bitmap: Bitmap, quality: Int = 85): ByteArray {
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, quality, stream)
        return stream.toByteArray()
    }
    
    override fun onCleared() {
        super.onCleared()
        sseJob?.cancel()
        sseClient.close()
    }
}

/**
 * 解题UI状态
 */
data class SolveUiState(
    val stage: SolveUiStage = SolveUiStage.IDLE,
    val statusMessage: String = "",
    val requestId: String = "",
    val questionInfo: String = "",
    val solutionSteps: String = "",
    val fullSolution: String = "",
    val mindMap: String = "",
    val suggestedQuestions: List<String> = emptyList(),
)

/**
 * 解题UI阶段
 */
enum class SolveUiStage {
    IDLE,           // 空闲
    UPLOADING,      // 上传中
    ANALYZING,      // 分析中
    DISPLAY_STEPS,  // 显示解题思路
    DISPLAY_FULL,   // 显示完整解析
    DISPLAY_MINDMAP,// 显示思维导图
    INTERACTIVE,    // 互动提问
    COMPLETED,      // 完成
    ERROR           // 错误
}
