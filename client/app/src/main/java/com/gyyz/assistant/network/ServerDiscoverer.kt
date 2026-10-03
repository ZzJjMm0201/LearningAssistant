package com.gyyz.assistant.network

import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress

/**
 * 服务端自动发现器
 * 通过 UDP 广播发现局域网内的学习助手服务端
 */
class ServerDiscoverer {

    companion object {
        private const val TAG = "ServerDiscoverer"
        private const val DISCOVERY_PORT = 9999
        private const val DISCOVERY_MESSAGE = "LEARNING_ASSISTANT_DISCOVER"
        private const val DISCOVERY_TIMEOUT_MS = 3000L  // 3秒超时
    }

    // 发现状态
    sealed class DiscoveryState {
        object Idle : DiscoveryState()
        object Searching : DiscoveryState()
        data class Found(val host: String, val port: Int) : DiscoveryState()
        object Failed : DiscoveryState()
    }

    private val _state = MutableStateFlow<DiscoveryState>(DiscoveryState.Idle)
    val state: StateFlow<DiscoveryState> = _state.asStateFlow()

    // 已发现的服务器地址
    private val _serverAddress = MutableStateFlow<String?>(null)
    val serverAddress: StateFlow<String?> = _serverAddress.asStateFlow()

    /**
     * 开始搜索服务端
     */
    suspend fun discover(): String? {
        _state.value = DiscoveryState.Searching
        Log.d(TAG, "开始搜索服务端...")

        return withContext(Dispatchers.IO) {
            try {
                val socket = DatagramSocket()
                socket.broadcast = true
                socket.soTimeout = 500  // 500ms 超时

                // 发送广播
                val message = DISCOVERY_MESSAGE.toByteArray()
                val broadcastAddr = InetAddress.getByName("255.255.255.255")
                val packet = DatagramPacket(message, message.size, broadcastAddr, DISCOVERY_PORT)
                socket.send(packet)
                Log.d(TAG, "已发送 UDP 广播")

                // 等待回复（最多3秒）
                val startTime = System.currentTimeMillis()
                while (System.currentTimeMillis() - startTime < DISCOVERY_TIMEOUT_MS) {
                    try {
                        val receiveBuffer = ByteArray(1024)
                        val receivePacket = DatagramPacket(receiveBuffer, receiveBuffer.size)
                        socket.receive(receivePacket)

                        val response = String(receivePacket.data, 0, receivePacket.length)
                        Log.d(TAG, "收到回复: $response")

                        val json = JSONObject(response)
                        if (json.optString("service") == "LearningAssistant") {
                            val host = json.getString("host")
                            val port = json.getInt("api_port")
                            val address = "http://$host:$port"

                            _serverAddress.value = address
                            _state.value = DiscoveryState.Found(host, port)

                            Log.d(TAG, "发现服务端: $address")
                            return@withContext address
                        }
                    } catch (e: java.net.SocketTimeoutException) {
                        // 超时，继续等待
                        continue
                    }
                }

                Log.w(TAG, "未发现服务端（超时）")
                _state.value = DiscoveryState.Failed
                null

            } catch (e: Exception) {
                Log.e(TAG, "搜索失败: ${e.message}", e)
                _state.value = DiscoveryState.Failed
                null
            }
        }
    }
}