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
import java.util.concurrent.TimeUnit

class ApiService(private var BASE_URL: String = "http://10.100.55.167:8000") {

    companion object {
        private const val PREFS_NAME = "learning_assistant_prefs"
        private const val KEY_TOKEN = "auth_token"
        private const val KEY_USERNAME = "auth_username"
    }

    private var sharedPreferences: SharedPreferences? = null
    private var authToken: String? = null

    /**
     * 初始化SharedPreferences（需在Activity中调用）
     */
    fun init(context: Context) {
        sharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        authToken = sharedPreferences?.getString(KEY_TOKEN, null)
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
     * 构建带Authorization header的请求
     */
    private fun buildAuthenticatedRequest(url: String): Request.Builder {
        val builder = Request.Builder().url(url)
        authToken?.let { token ->
            builder.addHeader("Authorization", "Bearer $token")
        }
        return builder
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
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            json.getString("request_id")
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
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: throw Exception("Empty response")
            val result = JSONObject(body)
            result.getString("answer")
        }
    }

    /**
     * 获取AI动画
     */
    data class AnimationResponse(val status: String, val url: String = "", val message: String = "")
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
            val request = Request.Builder().url("$BASE_URL/report/data").post(body).build()
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
            val request = Request.Builder().url("$BASE_URL/report/ai").post(body).build()
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
            val request = Request.Builder().url("$BASE_URL/history/batch-delete").post(body).build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            if (!response.isSuccessful) {
                throw Exception("批量删除失败: ${response.code}")
            }
            respJson.optInt("deleted_count", 0)
        }
    }

    suspend fun getHistory(startDate: String, endDate: String): List<HistoryRecord> {
        return withContext(Dispatchers.IO) {
            val json = JSONObject().apply {
                put("start_date", startDate)
                put("end_date", endDate)
            }
            val body = json.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$BASE_URL/history").post(body).build()
            val response = client.newCall(request).execute()
            val respJson = JSONObject(response.body?.string() ?: "{}")
            val arr = respJson.optJSONArray("records") ?: return@withContext emptyList()
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                // 解析知识点的 JSON 数组
                val kpArr = obj.optJSONArray("knowledge_points")
                val knowledgePoints = if (kpArr != null) {
                    (0 until kpArr.length()).map { kpArr.getString(it) }
                } else emptyList()

                HistoryRecord(
                    id = obj.optInt("id"),
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
                )
            }
        }
    }

    data class HistoryRecord(
        val id: Int,
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