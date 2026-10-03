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

class ApiService(private var BASE_URL: String = "http://121.199.23.213:8000") {

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
    private const val KEY_INTERACTIVE_QUIZ = "interactive_quiz"
        private const val KEY_THINKING_ENABLED = "thinking_enabled"
    private const val KEY_LATEX_HELPER = "latex_helper"
        private const val KEY_THEME_MODE = "theme_mode"
        private const val KEY_DIALECT = "dialect"
        private const val KEY_GRADE = "grade"
        private const val KEY_PERSONALITY = "personality"
        private const val KEY_DETAIL = "detail"
        private const val KEY_SUBJECT = "subject"
    }

    private var sharedPreferences: SharedPreferences? = null
    private var authToken: String? = null
    private var appContext: Context? = null

    /**
     * 初始化SharedPreferences（需在Activity中调用）
     */
    fun init(context: Context) {
        appContext = context.applicationContext
        sharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        authToken = sharedPreferences?.getString(KEY_TOKEN, null)
        loadAiSettings()
        // 服务端地址：优先用户已保存的地址，其次 assets/server_ip.txt（随项目 server_ip.txt 打包），最后默认值
        val saved = sharedPreferences?.getString(KEY_SERVER_ADDRESS, null)
        if (!saved.isNullOrEmpty()) {
            // 🔁 自动纠偏：若保存的是内网地址（10.x / 192.168.x / 172.16-31.x），
            //    而当前默认地址已是公网，则升级为默认地址。
            //    否则老设备会永远卡在过时的内网地址上（换网/出门即失效）。
            BASE_URL = if (isLanAddress(saved) && !isLanAddress(defaultBaseUrl(context))) {
                val upgraded = defaultBaseUrl(context)
                sharedPreferences?.edit()?.putString(KEY_SERVER_ADDRESS, upgraded)?.apply()
                upgraded
            } else {
                saved
            }
        } else {
            BASE_URL = defaultBaseUrl(context)
        }
    }

    /**
     * 读取打包在 assets/server_ip.txt 的默认地址；缺失时回退到类默认值。
     */
    private fun defaultBaseUrl(context: Context): String {
        val assetIp = try {
            context.assets.open("server_ip.txt").bufferedReader().use { it.readText() }.trim()
        } catch (e: Exception) {
            ""
        }
        return if (assetIp.isNotEmpty()) {
            if (assetIp.startsWith("http")) assetIp else "http://$assetIp"
        } else {
            BASE_URL
        }
    }

    /**
     * 判断是否内网/局域网地址。
     * 10.0.0.0/8、172.16.0.0/12、192.168.0.0/16
     */
    private fun isLanAddress(url: String): Boolean {
        val host = url.removePrefix("http://").removePrefix("https://")
            .substringBefore("/").substringBefore(":")
        return when {
            host.startsWith("10.") -> true
            host.startsWith("192.168.") -> true
            host.startsWith("172.") -> {
                val second = host.split(".").getOrNull(1)?.toIntOrNull() ?: -1
                second in 16..31
            }
            else -> false
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
    // ② 回答风格 formal(正式)/encouraging(鼓励)/humorous(幽默)（④⑦十一 相关请求头）
    var answerStyle: String = "formal"
    var searchEnabled: Boolean = true
    /** ⑤ 边解答边设问：开启后解题过程中插入小问（对应 X-Interactive-Quiz 头） */
    var interactiveQuiz: Boolean = false
    var thinkingMode: String = "off"        // 十一 思考模式：off / on / auto（auto按难度）
    var latexHelper: String = "auto"       // 图解辅助：off / on / auto（auto: 数学/物理且较难/难）
    var themeMode: String = "system"       // system / light / dark（纯客户端，不发服务器）
    var dialect: String = "普通话"         // ③ 方言名称（style=dialect 时；默认普通话）
    var grade: String = ""                 // ④ 年级（小学/初中/高中/考研）
    var personality: String = "auto"       // ⑧ 人格：MBTI16型 或 auto
    var detail: String = "auto"            // ⑧ 详细度：very_detailed/detailed/brief/auto
    var subject: String = ""               // ⑧ 学科（用于personality=auto推荐）

    /**
     * 为任意请求追加认证头 + AI模型头（统一入口）
     */
    /**
     * 统一解析"启动解题"类响应。
     *
     * 服务端失败时返回 {"status":"error","code":401,"message":"..."}，不含 request_id。
     * 以前直接 getString("request_id") 会抛 JSONException，用户只看到
     * "No value for request_id"，无从判断是登录过期还是别的问题。
     */
    /**
     * 解析 JSON 响应；若响应不是 JSON（常见于公共 WiFi 的强制认证门户
     * 把请求劫持成 HTML 登录页），转为用户可以照着做的提示。
     */
    private fun parseJsonOrExplain(body: String): JSONObject {
        val trimmed = body.trimStart()
        if (trimmed.startsWith("<")) {
            // 典型的强制门户 / 网关错误页
            throw Exception(
                "当前网络需要先登录认证（检测到网页登录页）。\n" +
                    "请先用浏览器打开任意网页完成 WiFi 登录，或切换到移动数据后重试。"
            )
        }
        if (trimmed.isEmpty()) {
            throw Exception("服务器没有返回内容，请稍后重试")
        }
        return try {
            JSONObject(trimmed)
        } catch (e: Exception) {
            throw Exception("服务器返回了无法识别的内容，请检查网络或稍后重试")
        }
    }

    private fun parseSolveResponse(json: JSONObject): String {
        // 1) 未登录 / 无权限：给出明确提示
        val code = json.optInt("code", 0)
        val status = json.optString("status", "")
        if (code == 401 || code == 403) {
            throw Exception(json.optString("message").ifBlank { "登录已过期，请重新登录" })
        }
        if (status == "error") {
            throw Exception(
                json.optString("message").ifBlank { json.optString("detail").ifBlank { "解题失败" } }
            )
        }
        // 2) 正常情况：取 request_id（缺失也要给可读提示）
        if (!json.has("request_id") || json.isNull("request_id")) {
            throw Exception(json.optString("message").ifBlank { "服务端未返回任务号，请稍后重试" })
        }
        return json.getString("request_id")
    }

    private fun Request.Builder.withAuth(): Request.Builder {
        authToken?.let { addHeader("Authorization", "Bearer $it") }
        addHeader("X-Engine", llmProvider)
        if (llmModel.isNotEmpty()) addHeader("X-LLM-Model", llmModel)
        addHeader("X-OCR-Mode", ocrMode)
        if (visionModel.isNotEmpty()) addHeader("X-Vision-Model", visionModel)
        // ② 回答风格 / 十一 思考模式 / ④ 搜题开关 / ③ 方言 / ④ 年级 / ⑧ 人格/详细度
        if (answerStyle.isNotEmpty()) addHeader("X-Style", answerStyle)
        if (thinkingMode == "on") addHeader("X-Thinking", "1")
        else if (thinkingMode == "auto") addHeader("X-Thinking", "auto")
        if (latexHelper == "on") addHeader("X-Latex-Helper", "1")
        else if (latexHelper == "auto") addHeader("X-Latex-Helper", "auto")
        if (!searchEnabled) addHeader("X-Search-Enabled", "0")
        // ⑤ 边解答边设问：仅开启时发送该头（服务端默认关闭）
        if (interactiveQuiz) addHeader("X-Interactive-Quiz", "1")
        // 中文值需 URL 编码，否则 OkHttp 报 "Unexpected char"（HTTP 头仅允许 ASCII）
        // 方言：非普通话才发送（以前要选“方言风格”才发，导致选了方言不生效）
        if (dialect.isNotEmpty() && dialect != "普通话") addHeader("X-Dialect", java.net.URLEncoder.encode(dialect, "UTF-8"))
        if (grade.isNotEmpty()) addHeader("X-Grade", java.net.URLEncoder.encode(grade, "UTF-8"))
        if (personality.isNotEmpty()) addHeader("X-Personality", java.net.URLEncoder.encode(personality, "UTF-8"))
        if (detail.isNotEmpty()) addHeader("X-Detail", detail)
        if (subject.isNotEmpty()) addHeader("X-Subject", java.net.URLEncoder.encode(subject, "UTF-8"))
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
        interactiveQuiz = sharedPreferences?.getBoolean(KEY_INTERACTIVE_QUIZ, false) ?: false
        // 十一 思考模式：新版存字符串 off/on/auto；兼容旧版 bool（直接按原始类型取，避免 getString 转换抛 ClassCastException）
        var tm: String? = null
        try {
            sharedPreferences?.let { sp ->
                val raw = sp.all[KEY_THINKING_ENABLED]
                tm = when (raw) {
                    is Boolean -> if (raw) "on" else "off"
                    is String -> raw
                    else -> null
                }
            }
        } catch (e: Exception) { tm = null }
        thinkingMode = if (tm == "on" || tm == "auto") tm else "off"
        latexHelper = sharedPreferences?.getString(KEY_LATEX_HELPER, "auto") ?: "auto"
        themeMode = sharedPreferences?.getString(KEY_THEME_MODE, "system") ?: "system"
        dialect = sharedPreferences?.getString(KEY_DIALECT, "普通话") ?: "普通话"
        grade = sharedPreferences?.getString(KEY_GRADE, "") ?: ""
        personality = sharedPreferences?.getString(KEY_PERSONALITY, "auto") ?: "auto"
        detail = sharedPreferences?.getString(KEY_DETAIL, "auto") ?: "auto"
        subject = sharedPreferences?.getString(KEY_SUBJECT, "") ?: ""
    }

    /**
     * 持久化 ②④⑦十一 扩展设置（风格/搜题/思考模式/主题）
     */
    fun saveExtraSettings(style: String, search: Boolean, thinking: String, theme: String, latexHelper: String = "auto", interactiveQuiz: Boolean = false) {
        sharedPreferences?.edit()
            ?.putString(KEY_ANSWER_STYLE, style)
            ?.putBoolean(KEY_SEARCH_ENABLED, search)
            ?.putString(KEY_THINKING_ENABLED, thinking)
            ?.putString(KEY_THEME_MODE, theme)
            ?.putString(KEY_LATEX_HELPER, latexHelper)
            ?.putBoolean(KEY_INTERACTIVE_QUIZ, interactiveQuiz)
            ?.apply()
        answerStyle = style
        searchEnabled = search
        thinkingMode = thinking
        themeMode = theme
        this.latexHelper = latexHelper
        this.interactiveQuiz = interactiveQuiz
    }

    /** 持久化 ⑧ 人格/详细度/学科 */
    fun savePersonalityDetail(personality: String, detail: String, subject: String) {
        sharedPreferences?.edit()
            ?.putString(KEY_PERSONALITY, personality)
            ?.putString(KEY_DETAIL, detail)
            ?.putString(KEY_SUBJECT, subject)
            ?.apply()
        this.personality = personality
        this.detail = detail
        this.subject = subject
    }

    /** 持久化 ③④ 方言/年级 */
    fun saveDialectGrade(dialect: String, grade: String) {
        sharedPreferences?.edit()
            ?.putString(KEY_DIALECT, dialect)
            ?.putString(KEY_GRADE, grade)
            ?.apply()
        this.dialect = dialect
        this.grade = grade
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
            parseSolveResponse(json)
        }
    }

    /** 📷 多页拍摄（多图版）：一次上传多张页面图，服务端逐页 OCR 后合并分题 */
    suspend fun startSolveMultipageImages(images: List<ByteArray>): String {
        return withContext(Dispatchers.IO) {
            val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
            images.forEachIndexed { i, bytes ->
                builder.addFormDataPart(
                    "files", "page_${i + 1}.jpg",
                    bytes.toRequestBody("image/jpeg".toMediaType())
                )
            }
            val request = Request.Builder()
                .url("$BASE_URL/solve/multipage-images")
                .post(builder.build())
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            parseSolveResponse(json)
        }
    }

    /** ⑧ 文字输入解题：跳过OCR和分题 */
    suspend fun startSolveText(text: String): String {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().put("text", text)
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$BASE_URL/solve/text")
                .post(requestBody)
                .withAuth()
                .build()
            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val respJson = JSONObject(body)
            parseSolveResponse(respJson)
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

    /** ① 多题：上传用户选定的题目索引，让服务端只解选中的题 */
    suspend fun selectQuestions(requestId: String, indices: List<Int>) {
        return withContext(Dispatchers.IO) {
            try {
                val arr = org.json.JSONArray()
                indices.forEach { arr.put(it) }
                val json = JSONObject().apply { put("indices", arr) }
                val body = json.toString().toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url("$BASE_URL/solve/select_questions/$requestId")
                    .post(body)
                    .withAuth()
                    .build()
                client.newCall(request).execute().close()
            } catch (e: Exception) {
                // 失败不阻塞（服务端超时后默认全选）
                android.util.Log.w("ApiService", "selectQuestions失败: ${e.message}")
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

    suspend fun animateText(text: String): AnimationResponse {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("text", text) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/animation/text").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            AnimationResponse(
                status = respJson.optString("status"),
                url = respJson.optString("url", ""),
                message = respJson.optString("message", "")
            )
        }
    }

    suspend fun startExtendText(text: String): String {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("text", text) }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/extend/text").post(body).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            parseSolveResponse(respJson)
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

    suspend fun getDataReport(days: Int = 30, theme: String = "dark"): ReportResponse {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply { put("days", days); put("theme", theme) }
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
            parseSolveResponse(json)
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

    /** 📣 上报问题：提交描述 + 环境信息（不消耗 AI 额度、不受权限限制） */
    suspend fun reportIssue(description: String, version: String = "3.0.0"): Boolean {
        return withContext(Dispatchers.IO) {
            val payload = JSONObject().apply {
                put("description", description)
                put("version", version)
                put("page", "android")
                put(
                    "user_agent",
                    "Android ${android.os.Build.VERSION.RELEASE} / ${android.os.Build.MODEL}"
                )
            }
            val request = Request.Builder()
                .url("$BASE_URL/report-issue")
                .post(payload.toString().toRequestBody("application/json".toMediaType()))
                .withAuth()
                .build()

            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("提交失败: ${response.code}")
            }
            val body = response.body?.string() ?: ""
            val obj = runCatching { JSONObject(body) }.getOrNull()
            obj?.optString("status") == "ok"
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
                    recordType = obj.optString("record_type", "solve"),
                    title = obj.optString("title", ""),
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
                    mindMap = obj.optString("mind_map", ""),
                    latexExtras = obj.optString("latex_extras", ""),
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
        val recordType: String = "solve",
        val title: String = "",
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
        val mindMap: String = "",
        val latexExtras: String = "",
    )

    data class AuthResult(val token: String, val user: AuthUser)
    data class AuthUser(val id: Int, val username: String, val createdAt: String, val isAdmin: Boolean, val aiPermission: Boolean = true)

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
            val respJson = parseJsonOrExplain(body)
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
                isAdmin = userJson.optBoolean("is_admin", false),
                aiPermission = userJson.optBoolean("ai_permission", true)
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
            val respJson = parseJsonOrExplain(body)
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
                isAdmin = userJson.optBoolean("is_admin", false),
                aiPermission = userJson.optBoolean("ai_permission", true)
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
                    isAdmin = userJson.optBoolean("is_admin", false),
                    aiPermission = userJson.optBoolean("ai_permission", true)
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

    /**
     * ⑰ AI批注：上传作业图片，返回带批注的图片 URL + 批注列表
     * 通道由 X-OCR-Mode 决定（qwen=视觉模型直出；paddle=坐标投给大模型），与设置一致
     */
    suspend fun annotateImage(imageBytes: ByteArray): JSONObject {
        return withContext(Dispatchers.IO) {
            val requestBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", "photo.jpg",
                    imageBytes.toRequestBody("image/jpeg".toMediaType()))
                .build()
            val request = Request.Builder()
                .url("$BASE_URL/annotate")
                .post(requestBody)
                .withAuth()
                .build()
            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("批注响应为空")
            val json = JSONObject(body)
            if (json.optString("status") == "error") {
                throw Exception(json.optString("message", "批注失败"))
            }
            json
        }
    }

    /**
     * ④ 导出 PDF / Word：调用服务端生成，下载到本地缓存，返回本地文件路径
     */
    suspend fun exportDocument(title: String, content: String, format: String = "pdf"): String {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("title", title)
                put("content", content)
                put("format", format)
            }
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url("$BASE_URL/export")
                .post(requestBody)
                .withAuth()
                .build()
            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("导出响应为空")
            val respJson = JSONObject(body)
            if (respJson.optString("status") != "ok") {
                throw Exception(respJson.optString("message", "导出失败"))
            }
            val url = respJson.optString("url", "")
            val fname = respJson.optString("filename", "export.$format")
            // 下载文件到缓存目录
            val fileReq = Request.Builder().url("$BASE_URL$url").withAuth().build()
            val fileResp = client.newCall(fileReq).execute()
            val bytes = fileResp.body?.bytes() ?: throw Exception("下载文件为空")
            val dir = java.io.File(appContext?.getExternalFilesDir(null), "export")
            if (!dir.exists()) dir.mkdirs()
            val out = java.io.File(dir, fname)
            out.writeBytes(bytes)
            out.absolutePath
        }
    }

    data class PomodoroRecommend(val status: String, val durationMinutes: Int = 30, val reason: String = "", val message: String = "")

    /** 番茄钟 AI 推荐做题时长 */
    suspend fun recommendPomodoro(ocrText: String, summary: String = "", imageBase64: String = ""): PomodoroRecommend {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("ocr_text", ocrText)
                put("summary", summary)
                if (imageBase64.isNotEmpty()) put("image_base64", imageBase64)
            }
            val requestBody = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/pomodoro/recommend").post(requestBody).withAuth().build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            PomodoroRecommend(
                    status = respJson.optString("status", "error"),
                    durationMinutes = respJson.optInt("duration_minutes", 30),
                    reason = respJson.optString("reason", ""),
                    message = respJson.optString("message", "")
            )
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