#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fix garbled Chinese in MainActivity.kt:
1. Remove all comments (both // and /** */) that contain garbled Chinese
2. Replace garbled Chinese strings in code with proper Chinese
"""

import re
import os

FILE_PATH = r"e:\programCodeVersion2.0(DeepSeek)\client\app\src\main\java\com\gyyz\assistant\MainActivity.kt"
BACKUP_PATH = FILE_PATH + ".bak"

# Map of garbled strings -> correct Chinese
# These are runtime/user-facing strings used in the code
STRING_REPLACEMENTS = {
    # Status text in ViewModel
    '"鐠虹喕閲滅�涳缚绡勬稉?.."': '"正在跟踪学习中..."',
    '"濞嗐垼绻嬮崶鐐存降閿�?{_loggedInUsername.value}"': '"欢迎回来，${_loggedInUsername.value}"',
    '"濞夈劌鍞介幋鎰�濮涢敍灞绢偨鏉�?${result.user.username}"': '"注册成功，欢迎 ${result.user.username}"',
    '"閻ц�茬秿閹存劕濮涢敍灞绢偨鏉╁骸娲栭弶?${result.user.username}"': '"登录成功，欢迎 ${result.user.username}"',
    '"瀹告煡鈧�鈧�閸戣櫣娅ヨぐ"': '"已退出登录"',
    '"鐎瑰本鍨氭稉鈧�娑擃亞鏆橀懠鍕�鎸撻敍浣风搐閹�顖欑�存稉瀣�鎯倊"': '"完成一个番茄钟！休息一下吧~"',
    '"闂団偓鐟曚礁搴滈崝鈺佹偋閿涚喐顒滈崷銊ュ櫙婢跺洩袙妫�?.."': '"需要帮助吗？正在准备解题..."',
    '"閺囨潙顦块崝鐔诲厴..."': '"更多功能..."',
    
    # handleVoiceCommand
    '"閺堫亣鐦戦崚顐㈠煂閸涙垝鎶�: $text"': '"未识别到命令: $text"',
    
    # startSolving
    '"濮濓絽婀�閹峰秶鍙�..."': '"正在拍照..."',
    
    # onPhotoReady
    '"濮濓絽婀�娑撳﹣绱舵０妯兼窗..."': '"正在上传题目..."',
    '"濮濓絽婀�閸掑棙鐎芥０妯兼窗..."': '"正在分析题目..."',
    
    # onPhotoError
    '"閹峰秶鍙庢径杈�瑙�: $error"': '"拍照失败: $error"',
    '"閹峰秶鍙庢径杈�瑙�: ${exception.message}"': '"拍照失败: ${exception.message}"',
    '"閹峰秶鍙庢径杈�瑙�"': '"拍照失败"',
    
    # SSE status
    '"閻撗呭�栫亸杈╁崕閿涘苯绱戞慨瀣╃瑐娴肩姴鑻熺拠閿嬬湴AI鐟欙綁顣� ${photoFile.absolutePath}"': '"照片就绪，开始上传并请求AI解题: ${photoFile.absolutePath}"',
    '"閻撗呭�栫亸杈╁崕閿涘苯绱戞慨瀣╃瑐娴�? ${photoFile.absolutePath}"': '"照片就绪，开始上传: ${photoFile.absolutePath}"',
    '"SSE鐠囬攱鐪癠RL: ${request.url}"': '"SSE请求URL: ${request.url}"',
    '"SSE鏉╃偞甯存径杈�瑙�: ${response.code}"': '"SSE连接失败: ${response.code}"',
    '"鏉╃偞甯存径杈�瑙�: ${response.code}"': '"连接失败: ${response.code}"',
    '"SSE婢惰精瑙�: ${e.message}"': '"SSE失败: ${e.message}"',
    '"鏉╃偞甯存径杈�瑙�: ${e.message}"': '"连接失败: ${e.message}"',
    '"鐟欙綁顣芥径杈�瑙�: ${e.message}"': '"解题失败: ${e.message}"',
    '"鐟欙綁顣芥径杈�瑙�: ${e.message}"': '"解题失败: ${e.message}"',
    '"鐟欙絿鐡熺�瑰本鍨�"': '"解答完成"',
    '"闁挎瑨顕�: ${json.optString("content")}"': '"错误: ${json.optString("content")}"',
    
    # SSE stage messages
    '"婢跺嫮鎮婃稉?.."': '"处理中..."',
    '"濮濓絽婀�閹兼粎鍌ㄦ０妯虹氨..."': '"正在搜索题库..."',
    '"AI濮濓絽婀�閸掑棙鐎�..."': '"AI正在分析..."',
    '"濮濓絽婀�閻㈢喐鍨氱憴锝夘暯閹�婵婄熅..."': '"正在生成解题思路..."',
    
    # askQuestion
    '"濮濓絽婀�閼惧嘲褰囬崶鐐电摕..."': '"正在获取回答..."',
    '"瀹告彃娲栫粵"': '"已回答"',
    '"閹绘劙妫舵径杈�瑙�: ${e.message}"': '"提问失败: ${e.message}"',
    
    # requestAnimation
    '"濮濓絽婀�閻㈢喐鍨氶崝銊ф暰..."': '"正在生成动画..."',
    '"閸斻劎鏁惧�歌尙鏁撻幋"': '"动画已生成"',
    '"閸斻劎鏁鹃悽鐔稿灇婢惰精瑙�: ${response.message}"': '"动画生成失败: ${response.message}"',
    '"閸斻劎鏁剧拠閿嬬湴婢惰精瑙�: ${e.message}"': '"动画请求失败: ${e.message}"',
    
    # loadAiReport
    '"濮濓絽婀�閻㈢喐鍨欰I鐎涳附鍎忛幎銉ユ啞..."': '"正在生成AI学情报告..."',
    '"AI閹躲儱鎲″�歌尙鏁撻幋"': '"AI报告已生成"',
    '"閹躲儱鎲￠悽鐔稿灇婢惰精瑙�: ${response.message}"': '"报告生成失败: ${response.message}"',
    '"閹躲儱鎲＄拠閿嬬湴婢惰精瑙�: ${e.message}"': '"报告请求失败: ${e.message}"',
    
    # loadDataReport
    '"濮濓絽婀�閻㈢喐鍨氶弫鐗堝祦鐎涳附鍎忛幎銉ユ啞..."': '"正在生成数据学情报告..."',
    '"閺佺増宓侀幎銉ユ啞瀹歌尙鏁撻幋"': '"数据报告已生成"',
    
    # requestKnowledgeExtension
    '"濮濓絽婀�娑撳﹣绱�..."': '"正在上传..."',
    '"濮濓絽婀�閸掑棙鐎�..."': '"正在分析..."',
    '"鐠囬攱鐪版径杈�瑙�: ${e.message}"': '"请求失败: ${e.message}"',
    
    # connectExtendSSE
    '"濮濓絽婀�閸掑棙鐎介崘鍛�顔�..."': '"正在分析内容..."',
    '"濮濓絽婀�閹�鑽ょ波閻�銉ㄧ槕閻�?.."': '"正在总结知识点..."',
    '"閺勬捇鏁婇悙鐟板嚒閻㈢喐鍨�"': '"易错点已生成"',
    '"閻�銉ㄧ槕閹锋挸鐫嶅�歌尙鏁撻幋"': '"知识拓展已生成"',
    '"瀵ゆ湹鍑犵�瑰本鍨�"': '"延伸完成"',
    '"闁挎瑨顕�: ${json.optString("content")}"': '"错误: ${json.optString("content")}"',
    
    # backToTracking
    '"鐠虹喕閲滅�涳缚绡勬稉?.."': '"跟踪学习中..."',
    
    # clearHistory
    '"閸樺棗褰剁拋鏉跨秿瀹稿弶绔婚梽"': '"历史记录已清除"',
    '"濞撳懘娅庢径杈�瑙�: ${e.message}"': '"清除失败: ${e.message}"',
    
    # updateServerAddress
    '"閺堝秴濮熺粩顖氭勾閸р偓瀹稿弶娲块弬? $address"': '"服务器地址已更新: $address"',
    
    # onButtonSolve
    '"濮濓絽婀�閹峰秶鍙�..."': '"正在拍照..."',
    
    # onButtonAnimation
    '"濮濓絽婀�閸戝棗顦�閸斻劎鏁�..."': '"正在准备动画..."',
    
    # onButtonExtend
    '"濮濓絽婀�閸戝棗顦�閻�銉ㄧ槕瀵ゆ湹鍑�..."': '"正在准备知识延伸..."',
    
    # MainActivity - permissions
    '"閹碘偓閺堝�嬫綀闂勬劕鍑￠幒鍫滅埃"': '"所有权限已授予"',
    '"閺夊啴妾虹悮顐ｅ珕缂�"': '"权限被拒绝"',
    '"闂団偓鐟曚胶娴夐張鐑樻綀闂勬劖澧犻懗鑺ヮ劀鐢�闀愬▏閻�"': '"需要相机权限才能正常使用"',
    '"閺夊啴妾洪悩鑸碘偓?- 閻╁憡婧�:$hasCamera, 缂冩垹绮�:$hasInternet"': '"权限状态- 相机:$hasCamera, 网络:$hasInternet"',
    '"閻╁憡婧�閺夊啴妾哄�稿弶宸挎禍鍫�绱濋惄瀛樺复鐠佸墽鐤�"': '"相机权限已授予，直接设置"',
    '"閻╁憡婧�閺夊啴妾洪張顏呭房娴滃牞绱濇稉璇插З鐠囬攱鐪�"': '"相机权限未授予，主动请求"',
    
    # Camera related
    '"閻╁憡婧�閺堫亜姘ㄧ紒顏庣礉鐠囬�庘棦閸氬骸鍟�鐠�"': '"相机未就绪，请稍后再试"',
    '"閻╁憡婧�鐏忚京鍗�"': '"相机就绪"',
    '"ImageCapture 瀹告彃姘ㄧ紒"': '"ImageCapture 已就绪"',
    '"imageCapture 娑�?null"': '"imageCapture 为 null"',
    
    # discoverAndConnect
    '"閼奉亜濮╅崣鎴犲箛閹存劕濮�: $address"': '"自动发现成功: $address"',
    '"閺堫亜褰傞悳鐗堟箛閸旓紕顏�閿涘苯鐨㈡担璺ㄦ暏姒涙�款吇閸︽澘娼�"': '"未发现服务端，将使用默认地址"',
    
    # takePhoto
    '"閻╁憡婧�閺堫亜姘ㄧ紒"': '"相机未就绪"',
    '"閹峰秶鍙庣搾鍛�妞�"': '"拍照超时"',
    '"閻撗呭�栧�歌弓绻氱��? ${photoFile.absolutePath}"': '"照片已保存: ${photoFile.absolutePath}"',
    
    # Gesture
    '"閹靛��濞嶇拠鍡楀焼: $fingerCount 閺嶈�勫�滈幐"': '"手势识别: $fingerCount 根手指"',
    '"imageCapture 娑撹櫣鈹栭敍灞炬￥濞夋洘濯块悡"': '"imageCapture 为空，无法拍照"',
    '"閹靛��濞嶇拠鍡楀焼闁挎瑨顕�: ${error.message}"': '"手势识别错误: ${error.message}"',
    '"閹靛��濞嶇拠鍡楀焼婢惰精瑙�: ${error.message}"': '"手势识别失败: ${error.message}"',
    '"閹靛��濞嶇拠鍡楀焼閸ｃ劌鍨垫慨瀣�瀵茬�瑰本鍨�"': '"手势识别器初始化完成"',
    
    # Permission request
    '"閺夊啴妾� $it: ${if (hasIt) "瀹稿弶宸挎禍? else "閺堫亝宸挎禍?}"': '"权限 $it: ${if (hasIt) "已授予" else "未授予"}"',
    '"閺夊啴妾� $it: ${if (hasIt) \\"瀹稿弶宸挎禍? else \\"閺堫亝宸挎禍?\\"}"': '"权限 $it: ${if (hasIt) "已授予" else "未授予"}"',
    '"濮濓絽婀�鐠囬攱鐪伴弶鍐�妾�..."': '"正在请求权限..."',
    '"閹碘偓閺堝�嬫綀闂勬劕鍑￠幒鍫滅埃閿涘本妫ら棁鈧�鐠囬攱鐪�"': '"所有权限已授予，无需请求"',
    
    # extend photo
    '"瀵ゆ湹鍑犻悡褏澧栧�歌弓绻氱��? ${photoFile.absolutePath}"': '"延伸照片已保存: ${photoFile.absolutePath}"',
    
    # Animation photo
    '"閸斻劎鏁鹃悡褏澧栧�歌弓绻氱��? ${photoFile.absolutePath}"': '"动画照片已保存: ${photoFile.absolutePath}"',
    
    # MainScreen
    '"閻╁憡婧�瀹告彃姘ㄧ紒"': '"相机已就绪"',
    '"CameraPreview 閼惧嘲褰� CameraProvider 鐡掑懏妞� (缁�?{retryCount + 1}濞�?"': '"获取 CameraProvider 超时 (第${retryCount + 1}次)"',
    '"CameraProvider 閼惧嘲褰囬幋鎰�濮�"': '"CameraProvider 获取成功"',
    '"閼惧嘲褰� CameraProvider 婢惰精瑙� (缁�?{retryCount + 1}濞�?"': '"获取 CameraProvider 失败 (第${retryCount + 1}次)"',
    '"CameraProvider 閼惧嘲褰囨径杈�瑙﹂敍灞藉嚒闁插秷鐦� $maxRetries 濞�"': '"CameraProvider 获取失败，已重试 $maxRetries 次"',
    '"閻╁憡婧�缂佹垵鐣鹃幋鎰�濮�"': '"相机绑定成功"',
    '"閻╁憡婧�缂佹垵鐣炬径杈�瑙�"': '"相机绑定失败"',
    '"鐟欙絿绮﹂惄鍛婃簚婢惰精瑙�"': '"解绑相机失败"',
    '"閻╁憡婧�瀹歌尪袙缂�"': '"相机已解绑"',
    
    # TrackingOverlay
    '"瀹告彃浠�${pageCount}妞�"': '"已做${pageCount}页"',
    '"閹�婵娾偓鍐ц厬... ${thinkingTimer}s"': '"思考中... ${thinkingTimer}s"',
    '"$fingerCount 閺嶈�勫�滈幐"': '"$fingerCount 根手指"',
    '"閹靛��濞嶉崨鎴掓姢: 5閳�鎺曅掓０?| 4閳�鎺戝З閻�?| 3閳�鎺撴殶閹诡喗濮ら崨?| 2閳�鎵怚閹躲儱鎲� | 1閳�鎺旂叀鐠囧棗娆㈡导"': '"手势命令: 5→解题 | 4→动画 | 3→数据报告 | 2→AI报告 | 1→知识延伸"',
    
    # LoginScreen
    '"棣冩憥 鐎涳缚绡勯崝鈺傚��"': '"📎 学习助手"',
    '"閸掓稑缂撻弬鎷屽�勯崣"': '"创建新账号"',
    '"閻ц�茬秿"': '"登录"',
    '"閻�銊﹀煕閸�"': '"用户名"',
    '"鐎靛棛鐖�"': '"密码"',
    '"濞夈劌鍞�"': '"注册"',
    '"瀹稿弶婀佺拹锕�褰块敍鐔哄仯閸戣崵娅ヨぐ"': '"已有账号？点击登录"',
    '"濞屸剝婀佺拹锕�褰块敍鐔哄仯閸戠粯鏁為崘"': '"没有账号？点击注册"',
    '"閻ц�茬秿/濞夈劌鍞介幐澶愭尦"': '"登录/注册按钮"',
    
    # SolvingScreen
    '"鏉╂柨娲�"': '"返回"',
    '"娑撳﹣绱舵稉?.."': '"上传中..."',
    '"閸掑棙鐎芥稉?.."': '"分析中..."',
    '"鐟欙綁顣介幀婵婄熅"': '"解题思路"',
    '"鐎瑰本鏆ｇ憴锝嗙��"': '"完整解析"',
    '"閹�婵堟樊鐎电厧娴�"': '"思维导图"',
    '"娴滄帒濮╅梻顔剧摕"': '"互动问答"',
    '"鐟欙絿鐡熺�瑰本鍨�"': '"解答完成"',
    '"濮濓絽婀�閸掑棙鐎芥０妯兼窗..."': '"正在分析题目..."',
    '"棣冩寱 鐟欙綁顣介幀婵婄熅"': '"💡 解题思路"',
    '"棣冩憫 鐎瑰本鏆ｇ憴锝嗙��"': '"📝 完整解析"',
    '"棣冩�囬敂?閹�婵堟樊鐎电厧娴�"': '"🗺️ 思维导图"',
    '"棣冩尠 娴ｇ姴褰查懗鍊熺箷閹�鎶芥６閿�"': '"💬 您可能还想问："',
    
    # ReportScreen
    '"鐎涳附鍎忛幎銉ユ啞"': '"学情报告"',
    
    # KnowledgeScreen
    '"棣冩憥 閻�銉ㄧ槕瀵ゆ湹鍑�"': '"📎 知识延伸"',
    '"棣冩惖 閻�銉ㄧ槕閻愯�勨偓鑽ょ波"': '"📝 知识点总结"',
    '"棣冩畬 閻�銉ㄧ槕閹锋挸鐫�"': '"🚀 知识拓展"',
    '"棣冩尠 瀵ゆ湹鍑犻幀婵娾偓鍐跨窗"': '"💬 延伸思考："',
    
    # AnimationScreen
    '"棣冨箑 AI閸斻劎鏁�"': '"🎬 AI动画"',
    '"濮濓絽婀�閸旂姾娴囬崝銊ф暰..."': '"正在加载动画..."',
    
    # HistoryScreen
    '"棣冩憥 閸樺棗褰剁拋鏉跨秿"': '"📎 历史记录"',
    '"鐠囧嘲婀�鐠佸墽鐤嗘稉顓熷ⅵ瀵�鈧�閸樺棗褰剁拋鏉跨秿"': '"请在设置中打开历史记录"',
    
    # LoadingOverlay
    '"濮濓絽婀�閻㈢喐鍨氶幎銉ユ啞..."': '"正在生成报告..."',
    '"濮濓絽婀�閻㈢喐鍨氶崝銊ф暰..."': '"正在生成动画..."',
    
    # MarkdownView
    '"濞撳弶鐓嬫径杈�瑙�: ${e.message}"': '"渲染失败: ${e.message}"',
}


def main():
    # Backup
    import shutil
    shutil.copy2(FILE_PATH, BACKUP_PATH)
    print(f"Backup saved to: {BACKUP_PATH}")

    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Step 1: Remove Javadoc-style comments /** ... */
    # This regex removes /** ... */ blocks (including multi-line)
    content = re.sub(r'/\*\*.*?\*/\s*', '', content, flags=re.DOTALL)
    
    # Step 2: Handle inline // comments that contain non-ASCII characters (garbled Chinese)
    # We split into lines first
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        # Check if line contains a // comment
        comment_pos = line.find('//')
        if comment_pos >= 0:
            code_part = line[:comment_pos]
            comment_part = line[comment_pos:]
            
            # If the comment contains non-ASCII chars (garbled chinese), remove the comment
            # Keep comments that are pure ASCII (like // ===== Config =====)
            has_non_ascii = any(ord(c) > 127 for c in comment_part)
            
            if has_non_ascii:
                # Remove the comment entirely
                code_part = code_part.rstrip()
                if code_part:
                    new_lines.append(code_part)
                # else skip empty line
                continue
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    content = '\n'.join(new_lines)

    # Step 3: Replace garbled strings with proper Chinese
    for old, new in STRING_REPLACEMENTS.items():
        if old in content:
            content = content.replace(old, new)
            # print(f"Replaced: {old[:40]}... -> {new[:40]}...")

    # Step 4: Also handle remaining garbled Chinese in non-string contexts
    # (like in Log.d, Log.e calls that might have been missed)
    
    # Write back
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print("Done! File has been processed.")
    print(f"Original backup: {BACKUP_PATH}")


if __name__ == '__main__':
    main()
