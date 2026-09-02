package com.gyyz.assistant.network

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

class ApiService(private var BASE_URL: String = "http://10.100.55.231:8000") {

    companion object {
        private const val PREFS_NAME = "learning_assistant_prefs"
        private const val KEY_SERVER_ADDRESS = "server_address"
        private const val KEY_WELCOME_DISMISSED = "welcome_dismissed"
        private const val KEY_LLM_PROVIDER = "llm_provider"
        private const val KEY_LLM_MODEL = "llm_model"
        private const val KEY_OCR_MODE = "ocr_mode"
        private const val KEY_VISION_MODEL = "vision_model"
        private const val KEY_TOKEN = "auth_token"
        private const val KEY_USERNAME = "auth_username"
        // ②④十一⑦ 新增设置
        private const val KEY_ANSWER_STYLE = "answer_style"
        private const val KEY_SEARCH_ENABLED = "search_enabled"
        private const val KEY_THINKING_ENABLED = "thinking_enabled"
        private const val KEY_THEME_MODE = "theme_mode"
    }

    private var sharedPreferences: SharedPreferences? = null
    private var authToken: String? = null

    /**
     * 初始化SharedPreferences（需在Activity中调用）
     */
    fun init(context: Context) {
        sharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        authToken = sharedPreferences?.getString(KEY_TOKEN, null)
        loadAiSettings()
        // 服务端地址：优先用户已保存的地址，其次 assets/server_ip.txt（随项目 server_ip.txt 打包），最后默认值
        val saved = sharedPreferences?.getString(KEY_SERVER_ADDRESS, null)
        if (!saved.isNullOrEmpty()) {
            BASE_URL = saved
        } else {
            val assetIp = try {
                context.assets.open("server_ip.txt").bufferedReader().use { it.readText() }.trim()
            } catch (e: Exception) {
                ""
            }
            if (assetIp.isNotEmpty()) {
                BASE_URL = if (assetIp.startsWith("http")) assetIp else "http://$assetIp"
            }
        }
    }

    /**
     * 获取当前服务端地址
     */
    fun getBaseUrl(): String = BASE_URL

    /**
     * 更新服务端地址（发现后调用）
     */
    fun updateServerAddress(address: String) {
        BASE_URL = address
        sharedPreferences?.edit()?.putString(KEY_SERVER_ADDRESS, address)?.apply()
    }

    /**
     * 是否已勾选“欢迎提示不再提醒”
     */
    fun isWelcomeDismissed(): Boolean = sharedPreferences?.getBoolean(KEY_WELCOME_DISMISSED, false) ?: false

    fun setWelcomeDismissed(v: Boolean) {
        sharedPreferences?.edit()?.putBoolean(KEY_WELCOME_DISMISSED, v)?.apply()
    }

    /**
     * 是否已登录（有token）
     */
    fun isLoggedIn(): Boolean = authToken != null

    /**
     * 获取当前用户名（可能为空）
     */
    fun getUsername(): String? = sharedPreferences?.getString(KEY_USERNAME, null)

    /**
     * 保存token
     */
    private fun saveToken(token: String) {
        authToken = token
        sharedPreferences?.edit()?.putString(KEY_TOKEN, token)?.apply()
    }

    /**
     * 保存用户名
     */
    private fun saveUsername(username: String) {
        sharedPreferences?.edit()?.putString(KEY_USERNAME, username)?.apply()
    }

    /**
     * 退出登录，清除token
     */
    fun logout() {
        authToken = null
        sharedPreferences?.edit()?.remove(KEY_TOKEN)?.remove(KEY_USERNAME)?.apply()
    }

    /**
     * AI模型设置（随请求头发给服务端）
     */
    var llmProvider: String = "deepseek"   // deepseek / qwen
    var llmModel: String = ""              // 大语言模型名（空=提供方默认）
    var ocrMode: String = "paddle"         // paddle / qwen（千问视觉OCR）
    var visionModel: String = ""           // 视觉模型名（空=默认 qwen3.8-max）
    // ② 回答风格 formal/plain/concise/lively（④⑦十一 相关请求头）
    var answerStyle: String = "formal"
    var searchEnabled: Boolean = true
    var thinkingEnabled: Boolean = false
    var themeMode: String = "system"       // system / light / dark（纯客户端，不发服务器）

    /**
     * 为任意请求追加认证头 + AI模型头（统一入口）
     */
    private fun Request.Builder.withAuth(): Request.Builder {
        authToken?.let { addHeader("Authorization", "Bearer $it") }
        addHeader("X-Engine", llmProvider)
        if (llmModel.isNotEmpty()) addHeader("X-LLM-Model", llmModel)
        addHeader("X-OCR-Mode", ocrMode)
        if (visionModel.isNotEmpty()) addHeader("X-Vision-Model", visionModel)
        // ② 回答风格 / 十一 思考模式 / ④ 搜题开关
        addHeader("X-Style", answerStyle)
        if (thinkingEnabled) addHeader("X-Thinking", "1")
        if (!searchEnabled) addHeader("X-Search-Enabled", "0")
        return this
    }

    /**
     * 持久化AI模型设置
     */
    fun saveAiSettings(llmProvider: String, llmModel: String, ocrMode: String, visionModel: String) {
        sharedPreferences?.edit()
            ?.putString(KEY_LLM_PROVIDER, llmProvider)
            ?.putString(KEY_LLM_MODEL, llmModel)
            ?.putString(KEY_OCR_MODE, ocrMode)
            ?.putString(KEY_VISION_MODEL, visionModel)
            ?.apply()
        this.llmProvider = llmProvider
        this.llmModel = llmModel
        this.ocrMode = ocrMode
        this.visionModel = visionModel
    }

    fun loadAiSettings() {
        llmProvider = sharedPreferences?.getString(KEY_LLM_PROVIDER, "deepseek") ?: "deepseek"
        llmModel = sharedPreferences?.getString(KEY_LLM_MODEL, "") ?: ""
        ocrMode = sharedPreferences?.getString(KEY_OCR_MODE, "paddle") ?: "paddle"
        visionModel = sharedPreferences?.getString(KEY_VISION_MODEL, "") ?: ""
        answerStyle = sharedPreferences?.getString(KEY_ANSWER_STYLE, "formal") ?: "formal"
        searchEnabled = sharedPreferences?.getBoolean(KEY_SEARCH_ENABLED, true) ?: true
        thinkingEnabled = sharedPreferences?.getBoolean(KEY_THINKING_ENABLED, false) ?: false
        themeMode = sharedPreferences?.getString(KEY_THEME_MODE, "system") ?: "system"
    }

    /**
     * 持久化 ②④⑦十一 扩展设置（风格/搜题/思考模式/主题）
     */
    fun saveExtraSettings(style: String, search: Boolean, thinking: Boolean, theme: String) {
        sharedPreferences?.edit()
            ?.putString(KEY_ANSWER_STYLE, style)
            ?.putBoolean(KEY_SEARCH_ENABLED, search)
            ?.putBoolean(KEY_THINKING_ENABLED, thinking)
            ?.putString(KEY_THEME_MODE, theme)
            ?.apply()
        answerStyle = style
        searchEnabled = search
        thinkingEnabled = thinking
        themeMode = theme
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun startSolve(imageBytes: ByteArray): String {
        return withContext(Dispatchers.IO) {
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", "photo.jpg",
                    imageBytes.toRequestBody("image/jpeg".toMediaType()))
                .build()

            val request = Request.Builder()
                .url("$BASE_URL/solve")
                .post(requestBody)
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            json.getString("request_id")
        }
    }
    
    /**
     * 确认OCR结果，让解题流水线立即继续（不确认则服务端等30秒超时）
     */
    suspend fun confirmSolve(requestId: String) {
        return withContext(Dispatchers.IO) {
            try {
                val body = "{}".toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("$BASE_URL/solve/confirm/$requestId")
                    .post(body)
                    .withAuth()
                    .build()
                client.newCall(request).execute().close()
            } catch (e: Exception) {
                // 确认失败不阻塞主流程（服务端超时后也会自动继续）
                android.util.Log.w("ApiService", "confirmSolve失败: ${e.message}")
            }
        }
    }

    /**
     * 多轮对话提问
     */
    suspend fun askQuestion(sessionId: String, question: String): String {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("session_id", sessionId)
                put("question", question)
            }
            val requestBody = json.toString()
                .toRequestBody("application/json".toMediaType())

            val request = Request.Builder()
                .url("$BASE_URL/ask")
                .post(requestBody)
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val result = JSONObject(body)
            result.getString("answer")
        }
    }

    /**
     * 追问（SSE流式）：与AI解题相同的流式方式，回答逐chunk回调
     * onRendered: 服务端把LaTeX代码块渲染成图片后回调完整渲染结果（替换气泡内容）
     */
    suspend fun askQuestionStream(
        sessionId: String,
        question: String,
        onChunk: suspend (String) -> Unit,
        onRendered: (suspend (String) -> Unit)? = null,
    ) {
        withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("session_id", sessionId)
                put("question", question)
            }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                    .url("$BASE_URL/ask/stream")
                    .post(body)
                    .withAuth()
                    .build()
            val streamClient = OkHttpClient.Builder()
                    .connectTimeout(30, TimeUnit.SECONDS)
                    .readTimeout(0, TimeUnit.MILLISECONDS)
                    .writeTimeout(60, TimeUnit.SECONDS)
                    .build()
            val response = streamClient.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("追问请求失败: ${response.code}")
            }
            val reader = BufferedReader(InputStreamReader(response.body?.byteStream()))
            var dataBuffer = StringBuilder()
            try {
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("data:") -> dataBuffer.append(line.substring(5).trim())
                        line.isEmpty() && dataBuffer.isNotEmpty() -> {
                            val data = dataBuffer.toString()
                            dataBuffer = StringBuilder()
                            try {
                                val ev = JSONObject(data)
                                when (ev.optString("stage")) {
                                    "answer_chunk" -> onChunk(ev.optString("content", ""))
                                    "answer_rendered" -> onRendered?.invoke(ev.optString("content", ""))
                                    "complete" -> return@withContext
                                    "error" -> throw Exception(ev.optString("content", "生成失败"))
                                }
                            } catch (e: Exception) {
                                // JSON解析失败忽略
                            }
                        }
                    }
                }
            } finally {
                reader.close()
                response.close()
            }
        }
    }

    /**
     * 获取AI动画
     */
    data class AnimationResponse(val status: String, val url: String = "", val message: String = "")
    data class GeoGebraResponse(val status: String, val url: String = "", val message: String = "")

    suspend fun generateGeoGebra(ocrText: String): GeoGebraResponse {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("ocr_text", ocrText) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                    .url("$BASE_URL/geogebra")
                    .post(body)
                    .withAuth()
                    .build()
            val response = client.newCall(request).execute()
            val result = JSONObject(response.body?.string() ?: "{}")
            GeoGebraResponse(
                    status = result.optString("status", "error"),
                    url = result.optString("url", ""),
                    message = result.optString("message", "")
            )
        }
    }
    suspend fun requestAnimation(imageBytes: ByteArray): AnimationResponse {
        return withContext(Dispatchers.IO) {
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", "photo.jpg",
                    imageBytes.toRequestBody("image/jpeg".toMediaType()))
                .build()

            val request = Request.Builder()
                .url("$BASE_URL/animation")
                .post(requestBody)
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            AnimationResponse(
                status = json.optString("status"),
                url = json.optString("url", ""),
                message = json.optString("message", "")
            )
        }
    }

    suspend fun getDataReport(days: Int = 30): ReportResponse {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("days", days) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/report/data").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            ReportResponse(
                status = respJson.optString("status"),
                url = respJson.optString("url", ""),
                report = respJson.optString("report", ""),
                message = respJson.optString("message", "")
            )
        }
    }

    suspend fun getAiReport(days: Int = 30): ReportResponse {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("days", days) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/report/ai").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            ReportResponse(
                status = respJson.optString("status"),
                url = respJson.optString("url", ""),
                report = respJson.optString("report", ""),
                message = respJson.optString("message", "")
            )
        }
    }

    /**
     * AI学情报告（SSE流式）：onChunk 回调累积文本，实现打字机效果
     */
    suspend fun getAiReportStream(days: Int = 7, onChunk: suspend (String) -> Unit) {
        withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("days", days) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/report/ai/stream").post(body).withAuth().build()
            // 长流式连接：用不限读超时的独立客户端（与AI解题一致），避免生成慢时被掐断
            val streamClient = OkHttpClient.Builder()
                    .connectTimeout(30, TimeUnit.SECONDS)
                    .readTimeout(0, TimeUnit.MILLISECONDS)
                    .writeTimeout(60, TimeUnit.SECONDS)
                    .build()
            val response = streamClient.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("报告请求失败: ${response.code}")
            }
            val reader = BufferedReader(InputStreamReader(response.body?.byteStream()))
            var dataBuffer = StringBuilder()
            try {
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("data:") -> dataBuffer.append(line.substring(5).trim())
                        line.isEmpty() && dataBuffer.isNotEmpty() -> {
                            val data = dataBuffer.toString()
                            dataBuffer = StringBuilder()
                            try {
                                val ev = JSONObject(data)
                                when (ev.optString("stage")) {
                                    "report_chunk" -> onChunk(ev.optString("content", ""))
                                    "complete" -> return@withContext
                                    "error" -> throw Exception(ev.optString("content", "生成失败"))
                                }
                            } catch (e: Exception) {
                                // JSON解析失败忽略
                            }
                        }
                    }
                }
            } finally {
                reader.close()
                response.close()
            }
        }
    }

    suspend fun startExtend(imageBytes: ByteArray): String {
        return withContext(Dispatchers.IO) {
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", "photo.jpg",
                    imageBytes.toRequestBody("image/jpeg".toMediaType()))
                .build()

            val request = Request.Builder()
                .url("$BASE_URL/extend")
                .post(requestBody)
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            json.getString("request_id")
        }
    }

    suspend fun clearHistory() {
        return withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$BASE_URL/history")
                .delete()
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("清除历史记录失败: ${response.code}")
            }
        }
    }

    suspend fun deleteHistoryRecord(recordId: Int) {
        return withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$BASE_URL/history/$recordId")
                .delete()
                .withAuth()
                .build()
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("删除记录失败: ${response.code}")
            }
        }
    }

    suspend fun batchDeleteHistory(ids: List<Int>): Int {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("ids", JSONArray(ids))
            }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/history/batch-delete").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            if (!response.isSuccessful) {
                throw Exception("批量删除失败: ${response.code}")
            }
            respJson.optInt("deleted_count", 0)
        }
    }

    suspend fun renderHistoryRecord(recordId: Int): String {
        return withContext(Dispatchers.IO) {
            try {
                val body = "{}".toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("$BASE_URL/history/render/$recordId")
                    .post(body)
                    .withAuth()
                    .build()
                val response = client.newCall(request).execute()
                val respJson = JSONObject(response.body?.string() ?: "{}")
                respJson.optString("full_solution", "")
            } catch (e: Exception) {
                ""
            }
        }
    }

    data class HistoryResult(
        val records: List<HistoryRecord>,
        val totalCount: Int,
        val subjectCount: Int,
    )

    suspend fun getHistory(
        startDate: String,
        endDate: String,
        subject: String = "",
        grade: String = "",
        difficulty: String = "",
        mastery: String = "",
    ): HistoryResult {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("start_date", startDate)
                put("end_date", endDate)
                put("subject", subject)
                put("grade", grade)
                put("difficulty", difficulty)
                put("mastery", mastery)
            }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/history").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            val arr = respJson.optJSONArray("records") ?: return@withContext HistoryResult(emptyList(), 0, 0)
            val records = (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                // 解析知识点的 JSON 数组
                val kpArr = obj.optJSONArray("knowledge_points")
                val knowledgePoints = if (kpArr != null) {
                    (0 until kpArr.length()).map { kpArr.getString(it) }
                } else emptyList()

                HistoryRecord(
                    id = obj.optInt("id"),
                    sessionId = obj.optString("session_id", ""),
                    timestamp = obj.optString("timestamp"),
                    ocrText = obj.optString("ocr_text"),
                    questionInfoRaw = obj.optString("question_info_raw"),
                    grade = obj.optString("grade"),
                    subject = obj.optString("subject"),
                    difficulty = obj.optString("difficulty"),
                    knowledgePoints = knowledgePoints,
                    solutionSteps = obj.optString("solution_steps"),
                    fullSolution = obj.optString("full_solution"),
                    imageUrl = obj.optString("image_url", ""),
                    masteryLevel = obj.optString("mastery_level", ""),
                )
            }
            HistoryResult(
                records = records,
                totalCount = respJson.optInt("total_count", records.size),
                subjectCount = respJson.optInt("subject_count", 0),
            )
        }
    }

    data class HistoryRecord(
        val id: Int,
        val sessionId: String = "",
        val timestamp: String,
        val ocrText: String = "",
        val questionInfoRaw: String = "",
        val grade: String = "",
        val subject: String = "",
        val difficulty: String = "",
        val knowledgePoints: List<String> = emptyList(),
        val solutionSteps: String = "",
        val fullSolution: String = "",
        val imageUrl: String = "",
        val masteryLevel: String = "",
    )

    data class AuthResult(val token: String, val user: AuthUser)
    data class AuthUser(val id: Int, val username: String, val createdAt: String, val isAdmin: Boolean)

    /**
     * 注册
     */
    suspend fun register(username: String, password: String): AuthResult {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("username", username)
                put("password", password)
            }
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$BASE_URL/auth/register")
                .post(requestBody)
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val respJson = JSONObject(body)
            if (respJson.optString("status") != "ok") {
                throw Exception(respJson.optString("detail", "注册失败"))
            }
            val data = respJson.getJSONObject("data")
            val token = data.getString("token")
            val userJson = data.getJSONObject("user")
            val user = AuthUser(
                id = userJson.getInt("id"),
                username = userJson.getString("username"),
                createdAt = userJson.optString("created_at", ""),
                isAdmin = userJson.optBoolean("is_admin", false)
            )
            // 保存token
            saveToken(token)
            saveUsername(username)
            AuthResult(token, user)
        }
    }

    /**
     * 登录
     */
    suspend fun login(username: String, password: String): AuthResult {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("username", username)
                put("password", password)
            }
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$BASE_URL/auth/login")
                .post(requestBody)
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val respJson = JSONObject(body)
            if (respJson.optString("status") != "ok") {
                throw Exception(respJson.optString("detail", "登录失败"))
            }
            val data = respJson.getJSONObject("data")
            val token = data.getString("token")
            val userJson = data.getJSONObject("user")
            val user = AuthUser(
                id = userJson.getInt("id"),
                username = userJson.getString("username"),
                createdAt = userJson.optString("created_at", ""),
                isAdmin = userJson.optBoolean("is_admin", false)
            )
            // 保存token
            saveToken(token)
            saveUsername(username)
            AuthResult(token, user)
        }
    }

    /**
     * 验证token是否有效
     */
    suspend fun verifyToken(token: String): AuthUser? {
        return withContext(Dispatchers.IO) {
            try {
                val json = JSONObject().apply { put("token", token) }
                val requestBody = json.toString().toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("$BASE_URL/auth/verify")
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: return@withContext null
                val respJson = JSONObject(body)
                if (respJson.optString("status") != "ok") return@withContext null
                val userJson = respJson.getJSONObject("user")
                AuthUser(
                    id = userJson.getInt("id"),
                    username = userJson.getString("username"),
                    createdAt = userJson.optString("created_at", ""),
                    isAdmin = userJson.optBoolean("is_admin", false)
                )
            } catch (e: Exception) {
                null
            }
        }
    }

    data class ReportResponse(
        val status: String,
        val url: String = "",
        val report: String = "",
        val message: String = ""
    )

    /**
     * Feature 4: 保存掌握程度
     */
    suspend fun saveMastery(requestId: String, level: String) {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("request_id", requestId)
                put("mastery_level", level)
            }
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$BASE_URL/mastery")
                .post(requestBody)
                .withAuth()
                .build()
            client.newCall(request).execute()
        }
    }
}

data class TrackingDataItem(
    val sessionId: String,
    val focusState: String,
    val durationSeconds: Float,
    val pageNumber: Int = 0,
    val pomodoroCount: Int = 0,
)