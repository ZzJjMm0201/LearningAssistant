#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second pass: fix remaining garbled strings in MainActivity.kt"""

FILE_PATH = r"e:\programCodeVersion2.0(DeepSeek)\client\app\src\main\java\com\gyyz\assistant\MainActivity.kt"

REPLACEMENTS = {
    # FocusState labels
    'WRITING("娑旓箑鍟撴稉")': 'WRITING("书写中")',
    'THINKING("閹�婵娾偓鍐ц厬")': 'THINKING("思考中")',
    'PAGE_TURNING("缂堝�氥��")': 'PAGE_TURNING("翻页中")',
    'SEEKING_HELP("濮瑰倸濮�")': 'SEEKING_HELP("求助中")',

    # handleVoiceCommand - voice command matching patterns
    'text.contains("娑撳秳绱�") || text.contains("閹峰秶鍙�") || text.contains("鐟欙綁顣�") -> onButtonSolve()': 
        'text.contains("不会") || text.contains("拍照") || text.contains("解题") -> onButtonSolve()',
    'text.contains("閹跺�熻杽") || text.contains("閸斻劎鏁�") -> onButtonAnimation()':
        'text.contains("抽象") || text.contains("动画") -> onButtonAnimation()',
    'text.contains("閸嬫矮绨�") || text.contains("婢舵艾鐨�") -> loadDataReport()':
        'text.contains("做了多少") || text.contains("数据") -> loadDataReport()',
    'text.contains("閹哄本褰�") || text.contains("娴滃棜袙") -> loadAiReport()':
        'text.contains("掌握") || text.contains("了解") -> loadAiReport()',
    'text.contains("鏉╂ɑ鍏傛禍鍡毿�") || text.contains("瀵ゆ湹鍑�") -> onButtonExtend()':
        'text.contains("还想了解") || text.contains("延伸") -> onButtonExtend()',

    # Log messages
    'Log.d("MainViewModel", "鐠嬪啰鏁� apiService.startSolve()")':
        'Log.d("MainViewModel", "调用 apiService.startSolve()")',
    'Log.d("MainViewModel", "娑撳﹣绱堕幋鎰�濮涢敍瀹篹questId=$requestId")':
        'Log.d("MainViewModel", "上传成功，requestId=$requestId")',
    'Log.d("MainViewModel", "SSE娴滃�╂��: $data")':
        'Log.d("MainViewModel", "SSE事件: $data")',
    'Log.e("MainViewModel", "鐟欙絾鐎絊SE娴滃�╂�㈡径杈�瑙�: $data", e)':
        'Log.e("MainViewModel", "解析SSE事件失败: $data", e)',
    'Log.e("MainViewModel", "SSE鏉╃偞甯存径杈�瑙�: ${e.message}", e)':
        'Log.e("MainViewModel", "SSE连接失败: ${e.message}", e)',
    'Log.d("MainViewModel", "鐠囬攱鐪癆I閸斻劎鏁鹃敍灞肩瑐娴肩姴娴橀悧? ${photoFile.absolutePath}")':
        'Log.d("MainViewModel", "请求AI动画，上传图片: ${photoFile.absolutePath}")',
    'Log.d("MainViewModel", "鐠囬攱鐪伴惌銉ㄧ槕瀵ゆ湹鍑�: ${photoFile.absolutePath}")':
        'Log.d("MainViewModel", "请求知识延伸: ${photoFile.absolutePath}")',

    # Speech recognition error messages
    'SpeechRecognizer.ERROR_NO_MATCH -> "閺堫亜鎯夊〒鍜冪礉鐠囩兘鍣哥拠"':
        'SpeechRecognizer.ERROR_NO_MATCH -> "未听清，请重试"',
    'SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "閺堫亝顥呭ù瀣�鍩岀拠顓㈢叾"':
        'SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "未检测到语音"',
    'SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缂傚搫鐨�瑜版洟鐓堕弶鍐�妾�"':
        'SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "缺少录音权限"',
    'else -> "鐠囶參鐓剁拠鍡楀焼闁挎瑨顕�: $error"':
        'else -> "语音识别错误: $error"',

    # AskDialog
    'Text("閹恒劏宕橀梻顕�顣介敍", color = Color.Gray, fontSize = 13.sp)':
        'Text("推荐问题：", color = Color.Gray, fontSize = 13.sp)',
    'placeholder = { Text("鏉堟挸鍙嗘担鐘垫畱闂傤噣顣�...", color = Color.Gray) },':
        'placeholder = { Text("输入你的问题...", color = Color.Gray) },',
    'Text("閸欐垿鈧�", color = Color.White)':
        'Text("发送", color = Color.White)',

    # HistoryViewScreen
    'errorMessage = "閸旂姾娴囨径杈�瑙�: ${e.message}"':
        'errorMessage = "加载失败: ${e.message}"',
    'Toast.makeText(context, "瀹告彃鍨归梽?${selectedIds.size} 閺壜ゎ唶瑜�", Toast.LENGTH_SHORT)':
        'Toast.makeText(context, "已删除 ${selectedIds.size} 条记录", Toast.LENGTH_SHORT)',
    'Toast.makeText(context, "閸掔娀娅庢径杈�瑙�: ${e.message}", Toast.LENGTH_SHORT).show()':
        'Toast.makeText(context, "删除失败: ${e.message}", Toast.LENGTH_SHORT).show()',

    # HistoryViewScreen buttons
    ') { Text("閸欐牗绉�", color = Color.White) }':
        ') { Text("取消", color = Color.White) }',
    'Text("瀹告煡鈧�?${selectedIds.size} 妞�", color = Color(0xFF00D2FF), fontSize = 16.sp':
        'Text("已选 ${selectedIds.size} 项", color = Color(0xFF00D2FF), fontSize = 16.sp',

    # HistoryViewScreen date filters
    'placeholder = { Text("瀵�鈧�婵�瀣�妫╅張?(2026-01-01)", color = Color.Gray, fontSize = ':
        'placeholder = { Text("开始日期 (2026-01-01)", color = Color.Gray, fontSize = ',
    'Text("閼�", color = Color.Gray, fontSize = 12.sp)':
        'Text("至", color = Color.Gray, fontSize = 12.sp)',

    # Empty history
    'Text("閳跨媴绗�", fontSize = 32.sp)':
        'Text("⚠️", fontSize = 32.sp)',
    'Text("闁插秷鐦�")':
        'Text("重试")',

    # Settings title
    'Text("閳挎瑱绗� 鐠佸墽鐤�", color = Color.White)':
        'Text("⚙️ 设置", color = Color.White)',
    'TextButton(onClick = onDismiss) { Text("閸忔娊妫�", color = Color(0xFF00D2FF)) }':
        'TextButton(onClick = onDismiss) { Text("关闭", color = Color(0xFF00D2FF)) }',
}


def main():
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    count = 0
    for old, new in REPLACEMENTS.items():
        if old in content:
            content = content.replace(old, new)
            count += 1
        else:
            print(f"NOT FOUND: {old[:60]}...")

    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Applied {count}/{len(REPLACEMENTS)} replacements.")


if __name__ == '__main__':
    main()
