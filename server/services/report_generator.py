"""
学情报告生成器
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List
from collections import Counter
from pathlib import Path
import plotly.graph_objects as go
import plotly.express as px
from server.config import REPORT_DIR, HISTORY_DIR
from server.database.models import SubmissionRecord


class ReportGenerator:
    
    def __init__(self, db_session):
        self.db = db_session
    
    def generate_data_report_html(self, days: int = 30) -> str | None:
        """
        生成数据版学情报告（HTML 格式）
        
        Args:
            days: 统计最近几天的数据
        
        Returns:
            HTML 文件路径，失败返回 None
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        records = self.db.query(SubmissionRecord).filter(
            SubmissionRecord.timestamp >= cutoff_date
        ).all()
        
        if not records:
            return None
        
        # 统计数据
        total = len(records)
        
        # 学科分布
        subjects = [r.question_info.get("subject", "未知") if r.question_info else "未知" for r in records]
        subject_counts = dict(Counter(subjects))
        
        # 难度分布
        difficulties = [r.question_info.get("difficulty", "未知") if r.question_info else "未知" for r in records]
        diff_counts = dict(Counter(difficulties))
        
        # 知识点统计
        all_kp = []
        for r in records:
            if r.question_info:
                all_kp.extend(r.question_info.get("knowledge_points", []))
        kp_counts = dict(Counter(all_kp).most_common(10))
        
        # 易错点统计
        all_mistakes = []
        for r in records:
            if r.question_info:
                all_mistakes.extend(r.question_info.get("easy_mistakes", []))
        mistake_counts = dict(Counter(all_mistakes).most_common(10))
        
        # 生成图表
        # 1. 学科分布饼图
        fig1 = px.pie(
            names=list(subject_counts.keys()),
            values=list(subject_counts.values()),
            title="学科分布",
            hole=0.4,
        )
        fig1.update_layout(margin=dict(l=20, r=20, t=50, b=20), height=350)
        fig1.update_layout(template='plotly_dark', paper_bgcolor='#16213e', plot_bgcolor='#16213e', font=dict(color='white'))
        
        # 2. 难度分布柱状图
        difficulty_order = ["易", "较易", "中", "较难", "难"]
        diff_data = {d: diff_counts.get(d, 0) for d in difficulty_order}
        fig2 = go.Figure(data=[
            go.Bar(
                x=list(diff_data.keys()),
                y=list(diff_data.values()),
                marker_color=["#4CAF50", "#8BC34A", "#FFC107", "#FF9800", "#F44336"],
                text=list(diff_data.values()),
                textposition="auto",
            )
        ])
        fig2.update_layout(
            title="难度分布",
            xaxis_title="难度",
            yaxis_title="题目数量",
            margin=dict(l=20, r=20, t=50, b=20),
            height=350,
        )
        fig2.update_layout(template='plotly_dark', paper_bgcolor='#16213e', plot_bgcolor='#16213e', font=dict(color='white'))
        
        # 3. 高频知识点横向柱状图
        fig3 = go.Figure(data=[
            go.Bar(
                y=list(kp_counts.keys()),
                x=list(kp_counts.values()),
                orientation="h",
                marker_color="#2196F3",
                text=list(kp_counts.values()),
                textposition="auto",
            )
        ])
        fig3.update_layout(
            title="高频知识点 TOP10",
            margin=dict(l=20, r=20, t=50, b=20),
            height=400,
            yaxis=dict(autorange="reversed"),
        )
        fig3.update_layout(template='plotly_dark', paper_bgcolor='#16213e', plot_bgcolor='#16213e', font=dict(color='white'))
        
        # 4. 易错点统计
        fig4 = go.Figure(data=[
            go.Bar(
                y=list(mistake_counts.keys()),
                x=list(mistake_counts.values()),
                orientation="h",
                marker_color="#FF5722",
                text=list(mistake_counts.values()),
                textposition="auto",
            )
        ])
        fig4.update_layout(
            title="常见易错点 TOP10",
            margin=dict(l=20, r=20, t=50, b=20),
            height=400,
            yaxis=dict(autorange="reversed"),
        )
        fig4.update_layout(template='plotly_dark', paper_bgcolor='#16213e', plot_bgcolor='#16213e', font=dict(color='white'))
        
        # 组装 HTML
        period = f"{cutoff_date.strftime('%Y-%m-%d')} 至 {datetime.utcnow().strftime('%Y-%m-%d')}"
        
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>学情报告</title>
    <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, sans-serif; background: #1a1a2e; color: white; padding: 20px; }}
        .header {{ text-align: center; padding: 20px; background: #16213e; border-radius: 12px; margin-bottom: 16px; }}
        .header h1 {{ font-size: 24px; color: #00d2ff; }}
        .header p {{ color: #aaa; margin-top: 8px; }}
        .stats {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
        .stat-card {{ flex: 1; min-width: 120px; background: #16213e; border-radius: 12px; padding: 16px; text-align: center; }}
        .stat-card .number {{ font-size: 32px; font-weight: bold; color: #00d2ff; }}
        .stat-card .label {{ color: #aaa; margin-top: 4px; font-size: 14px; }}
        .chart-container {{ background: #16213e; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 学情报告（数据版）</h1>
        <p>统计周期：{period}</p>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="number">{total}</div>
            <div class="label">总做题数</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(subjects)}</div>
            <div class="label">涉及学科</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(kp_counts)}</div>
            <div class="label">覆盖知识点</div>
        </div>
    </div>
    
    <div class="chart-container">
        {fig1.to_html(full_html=False, include_plotlyjs=False)}
    </div>
    
    <div class="chart-container">
        {fig2.to_html(full_html=False, include_plotlyjs=False)}
    </div>
    
    <div class="chart-container">
        {fig3.to_html(full_html=False, include_plotlyjs=False)}
    </div>
    
    <div class="chart-container">
        {fig4.to_html(full_html=False, include_plotlyjs=False)}
    </div>
</body>
</html>
"""
        
        # 保存文件
        report_dir = REPORT_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"data_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
        report_path.write_text(html, encoding='utf-8')
        print(f"报告已保存: {report_path}")
        
        return str(report_path)
    
    def get_report_summary(self, days: int = 30) -> str:
        """生成报告文字摘要（用于AI报告）"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        records = self.db.query(SubmissionRecord).filter(
            SubmissionRecord.timestamp >= cutoff_date
        ).all()
        
        if not records:
            return "暂无学习记录"
        
        total = len(records)
        subjects = Counter(r.question_info.get("subject", "未知") if r.question_info else "未知" for r in records)
        difficulties = Counter(r.question_info.get("difficulty", "未知") if r.question_info else "未知" for r in records)
        
        all_kp = []
        for r in records:
            if r.question_info:
                all_kp.extend(r.question_info.get("knowledge_points", []))
        top_kp = Counter(all_kp).most_common(5)
        
        all_mistakes = []
        for r in records:
            if r.question_info:
                all_mistakes.extend(r.question_info.get("easy_mistakes", []))
        top_mistakes = Counter(all_mistakes).most_common(5)
        
        return f"""
学生在过去{days}天共解答{total}道题。
学科分布：{dict(subjects)}
难度分布：{dict(difficulties)}
高频知识点：{top_kp}
常见易错点：{top_mistakes}
"""