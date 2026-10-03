package com.gyyz.assistant.network

import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import okhttp3.*
import org.json.JSONObject

class SSEClient(private val baseUrl: String) {
    private val client =
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()

    fun connect(url: String): Flow<SSEEvent> = flow {
        // ===== 关键修复：使用 withContext(Dispatchers.IO) =====
        val response = withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url(url)
                .header("Accept", "text/event-stream")
                .header("Cache-Control", "no-cache")
                .build()
            client.newCall(request).execute()
        }

        if (!response.isSuccessful) {
            throw Exception("SSE连接失败: ${response.code}")
        }

        val reader = BufferedReader(
            InputStreamReader(response.body?.byteStream() ?: throw Exception("空响应"))
        )

        var eventType = ""
        var dataBuffer = StringBuilder()

        try {
            while (true) {
                // 检查协程是否被取消
                kotlin.coroutines.coroutineContext.ensureActive()

                // ===== 读取行也在 IO 线程执行 =====
                val line = withContext(Dispatchers.IO) {
                    reader.readLine()
                } ?: break

                when {
                    line.startsWith("event:") -> {
                        eventType = line.substring(6).trim()
                    }
                    line.startsWith("data:") -> {
                        dataBuffer.append(line.substring(5).trim())
                    }
                    line.isEmpty() && dataBuffer.isNotEmpty() -> {
                        val event = SSEEvent(
                            type = eventType.ifEmpty { "message" },
                            data = dataBuffer.toString()
                        )
                        emit(event)

                        dataBuffer = StringBuilder()
                        eventType = ""

                        if (event.data.contains("\"stage\":\"complete\"")) {
                            break
                        }
                    }
                }
            }
        } finally {
            reader.close()
            response.close()
        }
    }.flowOn(Dispatchers.IO)  // ===== 指定在 IO 线程执行 =====

    fun connectSolveStream(requestId: String): Flow<SSEEvent> {
        return connect("$baseUrl/solve/stream/$requestId")
    }

    fun close() {
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }
}

data class SSEEvent(val type: String, val data: String) {
    fun toSolveEvent(): SolveEvent? {
        return try {
            val json = JSONObject(data)
            SolveEvent(stage = json.optString("stage", "unknown"), content = json.opt("content"))
        } catch (e: Exception) {
            null
        }
    }
}

data class SolveEvent(
    val stage: String,
    val content: Any?
)