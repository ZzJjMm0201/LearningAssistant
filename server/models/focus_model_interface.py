"""
专注度模型接口定义

即使模型还未训练完成，先定义好接口规范，
方便后续对接。客户端和服务端都遵循此规范。
"""
from pydantic import BaseModel
from enum import Enum
from typing import List, Optional
from datetime import datetime

class FocusState(str, Enum):
    """专注度状态枚举"""
    WRITING = "writing"           # 书写中 - 正常做题
    THINKING = "thinking"         # 思考中 - 可能需要帮助
    PAGE_TURNING = "page_turning" # 翻页 - 换题了
    SEEKING_HELP = "seeking_help" # 求助 - 主动抬头

class FocusPrediction(BaseModel):
    """单次专注度预测结果"""
    state: FocusState
    confidence: float  # 0.0 - 1.0
    timestamp: float   # Unix时间戳
    
class FocusSessionData(BaseModel):
    """一次学习会话的专注度数据"""
    session_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    
    # 统计
    total_duration_seconds: float = 0.0
    writing_duration: float = 0.0
    thinking_duration: float = 0.0
    page_turns: int = 0
    help_requests: int = 0
    
    # 连续思考超时次数
    prolonged_thinking_count: int = 0
    
    # 专注度评分 (0-100)
    focus_score: float = 0.0
    
    # 原始预测序列
    predictions: List[FocusPrediction] = []

# ============ 客户端接口规范 ============

class FocusModelInterface:
    """
    专注度模型客户端接口
    
    当模型训练完成后，实现此接口即可接入系统。
    """
    
    def load_model(self, model_path: str) -> bool:
        """加载模型文件"""
        raise NotImplementedError
    
    def predict(self, image_bytes: bytes) -> FocusPrediction:
        """
        对单帧图像进行预测
        
        Args:
            image_bytes: 相机帧的JPEG/PNG字节数据
        
        Returns:
            FocusPrediction: 预测结果
        """
        raise NotImplementedError
    
    def get_model_info(self) -> dict:
        """获取模型信息：版本、输入规格等"""
        raise NotImplementedError
    
    def release(self):
        """释放模型资源"""
        raise NotImplementedError

# ============ 本地规则引擎（备用方案） ============

class RuleBasedFocusDetector(FocusModelInterface):
    """
    基于规则的专注度检测器（模型未就绪时的备用方案）
    
    通过简单的图像变化检测来判断状态：
    - 页面内容变化大 → PAGE_TURNING
    - 页面内容长时间不变 → THINKING
    - 页面有连续变化 → WRITING
    - （无法检测SEEKING_HELP，需依赖用户手势）
    """
    
    def __init__(self):
        self.last_frame_hash = None
        self.last_change_time = datetime.now()
        self.thinking_threshold = 30  # 30秒无变化视为思考
    
    def load_model(self, model_path: str) -> bool:
        return True  # 规则引擎无需加载模型
    
    def predict(self, image_bytes: bytes) -> FocusPrediction:
        import hashlib
        
        # 计算图像哈希
        frame_hash = hashlib.md5(image_bytes).hexdigest()
        now = datetime.now()
        
        if self.last_frame_hash is None:
            self.last_frame_hash = frame_hash
            self.last_change_time = now
            return FocusPrediction(
                state=FocusState.WRITING,
                confidence=0.5,
                timestamp=now.timestamp()
            )
        
        # 检测变化
        has_changed = (frame_hash != self.last_frame_hash)
        time_since_change = (now - self.last_change_time).total_seconds()
        
        if has_changed:
            self.last_frame_hash = frame_hash
            self.last_change_time = now
            
            if time_since_change > 5:
                # 长时间无变化后突然变化 → 翻页
                return FocusPrediction(
                    state=FocusState.PAGE_TURNING,
                    confidence=0.7,
                    timestamp=now.timestamp()
                )
            else:
                return FocusPrediction(
                    state=FocusState.WRITING,
                    confidence=0.6,
                    timestamp=now.timestamp()
                )
        else:
            if time_since_change > self.thinking_threshold:
                return FocusPrediction(
                    state=FocusState.THINKING,
                    confidence=0.8,
                    timestamp=now.timestamp()
                )
            else:
                return FocusPrediction(
                    state=FocusState.WRITING,
                    confidence=0.5,
                    timestamp=now.timestamp()
                )
    
    def get_model_info(self) -> dict:
        return {
            "name": "RuleBasedFocusDetector",
            "version": "0.1.0",
            "type": "heuristic",
            "thinking_threshold": self.thinking_threshold,
        }
    
    def release(self):
        pass

# 工厂函数 - 获取可用的专注度检测器
def get_focus_detector(model_path: str = None) -> FocusModelInterface:
    """
    获取专注度检测器实例
    
    如果模型文件存在则加载ML模型，否则使用规则引擎
    """
    if model_path and __import__('os').path.exists(model_path):
        # TODO: 当模型就绪后，在此处加载实际模型
        # from server.models.focus_model_ml import MLFocusDetector
        # return MLFocusDetector(model_path)
        pass
    
    return RuleBasedFocusDetector()