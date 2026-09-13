"""
学情报告生成器
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter
from pathlib import Path
import plotly.graph_objects as go
import plotly.express as px
from server.config import REPORT_DIR, HISTORY_DIR
from server.database.models import SubmissionRecord, TrackingRecord

# ① 知识点词云：中文分词（jieba）+ 停用词过滤
import jieba

def _segment_knowledge_points(all_kp):
    """对知识点短语列表做分词，统计词频，返回 [(词, 次数)] 按频率降序（过滤单字/停用词/纯符号）"""
    stop = {"的", "了", "与", "和", "及", "或", "是", "在", "等", "中", "定律", "规律", "关系", "条件", "方法",
            "特点", "概念", "计算", "求解", "应用", "性质", "分析", "理解", "其", "一个", "进行", "通过"}
    counter = {}
    for kp in all_kp:
        if not kp or not isinstance(kp, str):
            continue
        for w in jieba.cut(kp):
            w = w.strip()
            if len(w) < 2:
                continue
            if w.isdigit() or w in stop:
                continue
            if not any('\u4e00' <= ch <= '\u9fff' or ch.isalpha() for ch in w):
                continue
            counter[w] = counter.get(w, 0) + 1
    return sorted(counter.items(), key=lambda x: -x[1])

# 词云中文字体候选（优先系统字体）
_WC_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
# 自定义遮罩（可选，参考用户素材 your_template.png）
_WC_MASK_CANDIDATES = [
    r"C:\Users\gyyzkczx\WPSDrive\337413621_6\WPS云盘\桌面\HighSchool\高二上\物理公开课\your_template.png",
]


def _wc_font_path():
    import os as _os
    for f in _WC_FONT_CANDIDATES:
        if _os.path.exists(f):
            return f
    return None


def _wc_mask_path():
    import os as _os
    for f in _WC_MASK_CANDIDATES:
        if _os.path.exists(f):
            return f
    return None


def render_wordcloud_png(top_words, out_path, width=1100, height=680) -> bool:
    """用 wordcloud 库生成词云 PNG（中文字体 + 可选遮罩 + 分层配色：高频蓝/中频青绿/低频紫）。
    top_words: [(词, 次数)]。返回是否成功。"""
    try:
        if not top_words:
            return False
        from wordcloud import WordCloud
        font = _wc_font_path()
        if not font:
            print("[词云] 未找到中文字体，跳过")
            return False
        freq = {str(w): int(cnt) for w, cnt in top_words if w}
        if not freq:
            return False
        mask = None
        mp = _wc_mask_path()
        if mp:
            try:
                import numpy as _np
                from PIL import Image as _Image
                arr = _np.array(_Image.open(mp).convert("L"))
                if arr.ndim == 2 and arr.shape[0] > 20 and arr.shape[1] > 20:
                    mask = arr
            except Exception as e:
                print(f"[词云] 遮罩读取失败(忽略): {e}")
                mask = None
        # 分层配色：按频率分三档（高频/中频/低频），参考用户素材中心词/二级词/附加词配色
        vals = sorted(freq.values(), reverse=True)
        hi = vals[0] if vals else 1
        lo = vals[-1] if vals else 0
        def _tier(v):
            if hi == lo:
                return 0
            return 0 if v >= hi * 0.66 else (1 if v >= hi * 0.33 else 2)
        tier_colors = [["#1565C0", "#1E88E5"], ["#00897B", "#26A69A"], ["#6A1B9A", "#8E24AA"]]
        import random as _random
        def _color_func(word, font_size, position, orientation, random_state=None, **kwargs):
            t = _tier(freq.get(word, lo))
            return _random.choice(tier_colors[t])
        wc_kwargs = dict(width=width, height=height, background_color=None, mode="RGBA",
                         font_path=font, max_words=60, relative_scaling=0.55,
                         prefer_horizontal=0.9, margin=4, random_state=42)
        if mask is not None:
            wc_kwargs["mask"] = mask
            wc_kwargs["contour_width"] = 0
        wc = WordCloud(**wc_kwargs)
        wc.generate_from_frequencies(freq)
        wc.recolor(color_func=_color_func, random_state=42)
        img = wc.to_image()
        img.save(str(out_path))
        return True
    except Exception as e:
        print(f"[词云] 生成失败: {e}")
        return False


def _wordcloud_trace(top_words):
    """兼容旧调用：plotly scatter 版已弃用（保留空实现，避免破坏其它引用）"""
    return None, top_words, 0.0, 0.0


def ensure_plotly_local() -> str:
    """确保本地 plotly.min.js 存在（下载缓存一次），返回HTML里用的相对路径；失败回退CDN"""
    try:
        target = REPORT_DIR / "plotly.min.js"
        if not target.exists() or target.stat().st_size < 100_000:
            import urllib.request
            print("[Plotly] 下载 plotly.min.js 到本地...")
            tmp = target.with_suffix(".js.tmp")
            _urls = [
                "https://cdn.plot.ly/plotly-2.32.0.min.js",
                "https://cdn.staticfile.org/plotly.js/2.32.0/plotly.min.js",
                "https://cdn.bootcdn.net/ajax/libs/plotly.js/2.32.0/plotly.min.js",
                "https://fastly.jsdelivr.net/npm/plotly.js-dist-min@2.32.0/plotly.min.js",
                "https://unpkg.com/plotly.js-dist-min@2.32.0/plotly.min.js",
            ]
            _ok = False
            for _u in _urls:
                try:
                    _req = urllib.request.Request(_u, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(_req, timeout=120) as resp, open(tmp, "wb") as f:
                        f.write(resp.read())
                    if tmp.exists() and tmp.stat().st_size > 100_000:
                        tmp.replace(target)
                        print(f"[Plotly] 本地缓存完成: {target.stat().st_size} bytes ({_u})")
                        _ok = True
                        break
                except Exception as _e:
                    print(f"[Plotly] 镜像下载失败 {_u}: {_e}")
            if not _ok:
                print("[Plotly] 所有镜像下载失败，回退CDN")
                if tmp.exists():
                    tmp.unlink()
                return "https://cdn.plot.ly/plotly-2.32.0.min.js"
        return "plotly.min.js"
    except Exception as e:
        print(f"[Plotly] 本地缓存失败，回退CDN: {e}")
        return "https://cdn.plot.ly/plotly-2.32.0.min.js"


class ReportGenerator:
    
    def __init__(self, db_session):
        self.db = db_session
    
    def generate_data_report_html(self, days: int = 30, user_id: Optional[int] = None, theme: str = "dark") -> str | None:
        """
        生成数据版学情报告（HTML 格式）
        
        Args:
            days: 统计最近几天的数据（<=0 表示全部）
            user_id: 用户ID（多用户隔离）
        
        Returns:
            HTML 文件路径，失败返回 None
        """
        from sqlalchemy import or_
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        query = self.db.query(SubmissionRecord)
        if days and days > 0:
            query = query.filter(SubmissionRecord.timestamp >= cutoff_date)
        if user_id is not None:
            query = query.filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            )
        else:
            # 未登录用户只能统计公共(NULL)记录
            query = query.filter(SubmissionRecord.user_id.is_(None))
        records = query.all()
        
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
        
        # 知识点统计（① 改为分词后的词频，用于词云图）
        all_kp = []
        for r in records:
            if r.question_info:
                all_kp.extend(r.question_info.get("knowledge_points", []))
        kp_counts = dict(Counter(all_kp).most_common(10))  # 兼容旧图需要
        kp_word_freq = _segment_knowledge_points(all_kp)[:24]  # ① 分词词频（词云用，取前24个）
        
        # 易错点统计（① 改为文字段落，放在报告最后）
        all_mistakes = []
        for r in records:
            if r.question_info:
                all_mistakes.extend(r.question_info.get("easy_mistakes", []))
        mistake_counts = list(Counter(all_mistakes).most_common(10))  # [(易错点, 次数)]
        mistake_text = "".join(f"{'①②③④⑤⑥⑦⑧⑨⑩'[i]} {m}\n" for i, (m, _) in enumerate(mistake_counts)) if mistake_counts else ""
        
        # 掌握程度统计
        mastery_dir = HISTORY_DIR / "mastery_records"
        mastery_levels = {"completely_mastered": "完全掌握", "partially_mastered": "部分掌握", "not_mastered": "完全没掌握"}
        mastery_counts = {}
        if mastery_dir.exists():
            for f in mastery_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding='utf-8'))
                    level = data.get("mastery_level", "")
                    mastery_counts[level] = mastery_counts.get(level, 0) + 1
                except:
                    pass
        
        # 每日做题趋势
        daily_counts = {}
        for r in records:
            day = r.timestamp.strftime('%m-%d') if r.timestamp else "未知"
            daily_counts[day] = daily_counts.get(day, 0) + 1
        daily_dates = sorted(daily_counts.keys())  # 按选择的时间范围(7/30/90/全部)，不再固定14天
        daily_values = [daily_counts.get(d, 0) for d in daily_dates]
        
        # 7日移动平均（算法统计：平滑做题趋势）
        def moving_average(values, window=3):
            if not values:
                return []
            result = []
            for i in range(len(values)):
                start = max(0, i - window + 1)
                chunk = values[start:i + 1]
                result.append(round(sum(chunk) / len(chunk), 2))
            return result
        daily_ma = moving_average(daily_values, 3)
        
        # 掌握率趋势（mastery_records × submission_records 关联，加权得分）
        mastery_scores = {"completely_mastered": 1.0, "partially_mastered": 0.5, "not_mastered": 0.0}
        mastery_timeline = []  # [(日期, 得分, 学科)]
        if mastery_dir.exists():
            for f in mastery_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding='utf-8'))
                    # 多用户隔离：仅统计当前用户的掌握记录；未登录仅统计公共记录
                    data_uid = data.get("user_id")
                    if user_id is not None:
                        if data_uid is not None and data_uid != user_id:
                            continue
                    else:
                        if data_uid is not None:
                            continue
                    level = data.get("mastery_level", "")
                    score = mastery_scores.get(level)
                    if score is None:
                        continue
                    rid = data.get("request_id", "")
                    subject = "未知"
                    rec = None
                    if rid:
                        rec = self.db.query(SubmissionRecord).filter(SubmissionRecord.session_id == rid).first()
                    if rec and rec.question_info:
                        subject = rec.question_info.get("subject", "未知") or "未知"
                    ts = data.get("timestamp", "")[:10]
                    mastery_timeline.append((ts, score, subject))
                except Exception:
                    pass
        mastery_timeline.sort(key=lambda x: x[0])
        mastery_dates = [x[0] for x in mastery_timeline]
        mastery_values = [x[1] for x in mastery_timeline]
        mastery_ma = moving_average(mastery_values, 3) if mastery_values else []
        # 学科掌握率（算法统计：按学科聚合加权平均）
        subject_mastery = {}
        for _, score, subject in mastery_timeline:
            bucket = subject_mastery.setdefault(subject, [])
            bucket.append(score)
        subject_mastery_avg = {s: round(sum(v) / len(v), 2) for s, v in subject_mastery.items()}
        
        # 专注度分析（TrackingRecord：书写/思考/翻页/求助时长）
        focus_labels = {"writing": "书写", "thinking": "思考", "page_turning": "翻页", "seeking_help": "求助"}
        focus_times = {}
        focus_records = self.db.query(TrackingRecord).all()
        if user_id is not None:
            focus_records = [t for t in focus_records if t.user_id == user_id or t.user_id is None]
        else:
            focus_records = [t for t in focus_records if t.user_id is None]
        if days and days > 0:
            focus_records = [t for t in focus_records if t.timestamp and t.timestamp >= cutoff_date]
        for t in focus_records:
            st = t.focus_state or "unknown"
            focus_times[st] = focus_times.get(st, 0) + (t.duration_seconds or 0)
        # 每日专注时长趋势（最近7天）
        daily_focus = {}
        for t in focus_records:
            if not t.timestamp:
                continue
            day = t.timestamp.strftime('%m-%d')
            daily_focus[day] = daily_focus.get(day, 0) + (t.duration_seconds or 0)
        focus_dates = sorted(daily_focus.keys())[-7:]
        focus_values = [round(daily_focus.get(d, 0) / 60, 1) for d in focus_dates]  # 分钟
        
        # ② 浅色/深色主题变量（theme=light 时背景/文字变浅）
        _light = theme == "light"
        TPL = "plotly_white" if _light else "plotly_dark"
        PBG = "#ffffff" if _light else "#16213e"
        FCOL = "#222222" if _light else "white"
        CARD_BG = "#f5f7fa" if _light else "#16213e"
        BODY_BG = "#f2f4f8" if _light else "#1a1a2e"
        BODY_FG = "#222222" if _light else "white"
        BODY_SUB = "#555555" if _light else "#aaa"
        BODY_ACCENT = "#0086b3" if _light else "#00d2ff"

        # 生成图表
        # 禁用所有图表的交互功能（缩放、点击放大等）
        config = {
            'displayModeBar': False,  # 隐藏模式栏
            'staticPlot': True,       # 静态图，完全禁用交互
            'responsive': True,
            'editable': False,
            'scrollZoom': False,
        }
        
        # 1. 学科分布饼图
        fig1 = px.pie(
            names=list(subject_counts.keys()),
            values=list(subject_counts.values()),
            title="学科分布",
            hole=0.4,
        )
        fig1.update_layout(margin=dict(l=20, r=20, t=50, b=20), height=350)
        fig1.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
        fig1.update_layout(dragmode=False, hovermode=False)
        fig1.update_xaxes(fixedrange=True)
        fig1.update_yaxes(fixedrange=True)
        
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
            dragmode=False,
            hovermode=False,
        )
        fig2.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
        fig2.update_xaxes(fixedrange=True)
        fig2.update_yaxes(fixedrange=True)
        
        # 3. 高频知识点词云图（wordcloud 库生成 PNG，中文分词词频）
        wordcloud_img_html = ""
        if kp_word_freq:
            try:
                import base64 as _b64
                _wc_png = REPORT_DIR / f"wordcloud_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.png"
                if render_wordcloud_png(kp_word_freq, _wc_png):
                    _b64s = _b64.b64encode(_wc_png.read_bytes()).decode("ascii")
                    wordcloud_img_html = (
                        '<div class="chart-container">'
                        '<h3 style="color:' + BODY_ACCENT + ';margin-bottom:10px">高频知识点词云</h3>'
                        '<img src="data:image/png;base64,' + _b64s + '" '
                        'style="width:100%;height:auto;display:block;border-radius:8px"/>'
                        '</div>'
                    )
                    os.remove(_wc_png)
            except Exception as _e:
                print(f"[词云] 内联失败: {_e}")
                wordcloud_img_html = ""
        fig3 = None
        
        # 4. 易错点（① 改为文字段落，见HTML组装末尾）
        fig4 = None
        
        # 5. 每日做题趋势（自上而下横向条形图：日期自上而下，最新在最上面）
        fig5 = go.Figure()
        _d5_rows = list(zip(daily_dates, daily_values, daily_ma or [None] * len(daily_dates)))
        _d5_rows = list(reversed(_d5_rows))  # 最新日期排最上
        _d5_labels = [d for d, _, _ in _d5_rows]
        _d5_vals = [v for _, v, _ in _d5_rows]
        fig5.add_trace(go.Bar(
            x=_d5_vals, y=_d5_labels, orientation='h',
            marker_color='#00D2FF',
            text=[str(v) for v in _d5_vals],
            textposition='outside',
            textfont=dict(color=FCOL, size=11),
            name='每日做题数',
            hoverinfo='skip',
        ))
        if daily_ma:
            fig5.add_trace(go.Scatter(
                x=[m for _, _, m in _d5_rows],
                y=_d5_labels,
                mode='markers+lines',
                marker=dict(color='#FFD700', size=8),
                line=dict(color='#FFD700', width=2, dash='dot'),
                name='3日移动平均',
                hoverinfo='skip',
            ))
        fig5.update_layout(
            title="每日做题趋势（自上而下，最新在最上）",
            barmode='overlay',
            margin=dict(l=10, r=40, t=50, b=90),
            height=max(280, 30 * len(_d5_labels) + 140),
            dragmode=False,
            hovermode=False,
            bargap=0.3,
            xaxis=dict(title="做题数", fixedrange=True, rangemode='tozero'),
            yaxis=dict(autorange='reversed', fixedrange=True, tickfont=dict(size=11)),
            legend=dict(orientation="h", yanchor="top", y=-0.08, x=0.5, xanchor="center",
                        font=dict(size=12, color=FCOL)),
        )
        fig5.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))

        # 7. 专注度分析（算法统计：书写/思考/翻页/求助时长占比）
        fig7 = None
        if focus_times:
            labels = [focus_labels.get(k, k) for k in focus_times.keys()]
            values = [round(v / 60, 1) for v in focus_times.values()]  # 秒→分钟
            fig7 = go.Figure(data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.4,
                    marker=dict(colors=['#00D2FF', '#4CAF50', '#FFC107', '#F44336']),
                    textinfo='label+percent',
                )
            ])
            fig7.update_layout(title="专注状态分布（分钟）", margin=dict(l=20, r=20, t=50, b=20), height=320, dragmode=False, hovermode=False)
            fig7.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
        
        # 8. 每日专注时长趋势（最近7天，分钟）
        fig8 = None
        if focus_dates:
            fig8 = go.Figure(data=[
                go.Bar(
                    x=focus_dates,
                    y=focus_values,
                    marker_color='#4CAF50',
                    text=focus_values,
                    textposition='auto',
                    name='专注时长(分钟)',
                )
            ])
            fig8.update_layout(
                title="每日专注时长（最近7天）",
                margin=dict(l=20, r=20, t=50, b=20),
                height=280,
                dragmode=False,
                hovermode=False,
            )
            fig8.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
            fig8.update_xaxes(fixedrange=True)
            fig8.update_yaxes(fixedrange=True)

        # 9. 掌握程度（横向条形图，每题一行从上到下，按日期排序；图例在底部、清晰可见）
        fig9 = None
        if mastery_timeline:
            _level_name = {1.0: "完全掌握", 0.5: "部分掌握", 0.0: "完全没掌握"}
            _lvl_color = {1.0: "#4CAF50", 0.5: "#FF9800", 0.0: "#F44336"}
            # 每道题一行；行标签=日期+学科；按日期升序（最新在下）
            _rows = mastery_timeline[:40]  # 最多 40 条，避免过长
            _y_labels = [f"{d}  {s}" for d, _, s in _rows]
            _scores = [sc for _, sc, _ in _rows]
            fig9 = go.Figure()
            # 按掌握等级分组绘制（保证图例只出现 3 项）
            for _sc in (1.0, 0.5, 0.0):
                _xs = [sc if sc == _sc else None for sc in _scores]
                fig9.add_trace(go.Bar(
                    x=_xs, y=_y_labels, orientation='h',
                    marker_color=_lvl_color[_sc], name=_level_name[_sc],
                    text=[_level_name[_sc] if sc == _sc else "" for sc in _scores],
                    textposition='inside', insidetextanchor='middle',
                    textfont=dict(color='white', size=11),
                    hoverinfo='skip',
                ))
            fig9.update_layout(
                title="掌握程度（每道题，自上而下）",
                barmode='stack',
                margin=dict(l=10, r=20, t=50, b=90),
                height=max(280, 34 * len(_y_labels) + 130),
                dragmode=False,
                hovermode=False,
                bargap=0.28,
                xaxis=dict(range=[0, 1], tickvals=[0, 0.5, 1.0],
                           ticktext=["完全没掌握", "部分掌握", "完全掌握"], fixedrange=True),
                yaxis=dict(autorange='reversed', fixedrange=True, tickfont=dict(size=11)),
                legend=dict(orientation="h", yanchor="top", y=-0.08, x=0.5, xanchor="center",
                            font=dict(size=12, color=FCOL)),
            )
            fig9.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))

        # 10. 学科掌握率（算法统计：按学科聚合加权平均）
        fig10 = None
        if subject_mastery_avg:
            subjects_sorted = sorted(subject_mastery_avg.keys())
            fig10 = go.Figure(data=[
                go.Bar(
                    x=[subject_mastery_avg[s] for s in subjects_sorted],
                    y=subjects_sorted,
                    orientation='h',
                    marker_color='#7B2FBE',
                    text=[f"{subject_mastery_avg[s]*100:.0f}%" for s in subjects_sorted],
                    textposition='auto',
                )
            ])
            fig10.update_layout(
                title="学科掌握率（自评加权平均）",
                margin=dict(l=20, r=20, t=50, b=20),
                height=max(280, len(subjects_sorted) * 50),
                xaxis=dict(range=[0, 1], tickformat=".0%"),
                dragmode=False,
                hovermode=False,
            )
            fig10.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
            fig10.update_xaxes(fixedrange=True)
            fig10.update_yaxes(fixedrange=True)
        
        # 6. 掌握程度分布
        if mastery_counts:
            fig6 = px.pie(
                names=[mastery_levels.get(k, k) for k in mastery_counts.keys()],
                values=list(mastery_counts.values()),
                title="掌握程度分布",
                hole=0.4,
                color_discrete_sequence=["#4CAF50", "#FFC107", "#F44336"],
            )
            fig6.update_layout(margin=dict(l=20, r=20, t=50, b=20), height=300, dragmode=False, hovermode=False)
            fig6.update_layout(template=TPL, paper_bgcolor=PBG, plot_bgcolor=PBG, font=dict(color=FCOL))
            fig6.update_xaxes(fixedrange=True)
            fig6.update_yaxes(fixedrange=True)
        
        # 组装 HTML
        period = f"{cutoff_date.strftime('%Y-%m-%d')} 至 {datetime.utcnow().strftime('%Y-%m-%d')}"
        plotly_src = ensure_plotly_local()
        
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>学情报告</title>
    <script src="{plotly_src}"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, sans-serif; background: {BODY_BG}; color: {BODY_FG}; padding: 20px; }}
        .header {{ text-align: center; padding: 20px; background: {CARD_BG}; border-radius: 12px; margin-bottom: 16px; }}
        .header h1 {{ font-size: 24px; color: {BODY_ACCENT}; }}
        .header p {{ color: {BODY_SUB}; margin-top: 8px; }}
        .stats {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
        .stat-card {{ flex: 1; min-width: 120px; background: {CARD_BG}; border-radius: 12px; padding: 16px; text-align: center; }}
        .stat-card .number {{ font-size: 32px; font-weight: bold; color: {BODY_ACCENT}; }}
        .stat-card .label {{ color: {BODY_SUB}; margin-top: 4px; font-size: 14px; }}
        .chart-container {{ background: {CARD_BG}; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
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
            <div class="number">{len(subject_counts)}</div>
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
    
    {wordcloud_img_html}
    
    {'<div class="chart-container">' + fig5.to_html(full_html=False, include_plotlyjs=False) + '</div>' if daily_dates else ''}
    {'<div class="chart-container">' + fig6.to_html(full_html=False, include_plotlyjs=False) + '</div>' if mastery_counts else ''}
    {'<div class="chart-container">' + fig7.to_html(full_html=False, include_plotlyjs=False) + '</div>' if fig7 else ''}
    {'<div class="chart-container">' + fig8.to_html(full_html=False, include_plotlyjs=False) + '</div>' if fig8 else ''}
    {'<div class="chart-container">' + fig9.to_html(full_html=False, include_plotlyjs=False) + '</div>' if fig9 else ''}
    {'<div class="chart-container">' + fig10.to_html(full_html=False, include_plotlyjs=False) + '</div>' if fig10 else ''}
    
    {f'<div class="chart-container" style="text-align:left"><h3 style="color:{BODY_ACCENT};margin-bottom:10px">常见易错点</h3><p style="line-height:2;white-space:pre-line">{mistake_text}</p></div>' if mistake_text else ''}
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
    
    def get_report_summary(self, days: int = 30, user_id: Optional[int] = None) -> str:
        """生成报告文字摘要（用于AI报告）；days<=0 表示全部历史；user_id 用于多用户隔离"""
        from sqlalchemy import or_
        query = self.db.query(SubmissionRecord)
        if days and days > 0:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            query = query.filter(SubmissionRecord.timestamp >= cutoff_date)
        if user_id is not None:
            query = query.filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            )
        else:
            query = query.filter(SubmissionRecord.user_id.is_(None))
        records = query.all()
        
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