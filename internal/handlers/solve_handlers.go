package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gyyz/lago/internal/ai"
	"github.com/gyyz/lago/internal/events"
	"github.com/gyyz/lago/internal/store"
)

// maxUpload 读取上传体的上限。
func (s *Server) maxUpload() int64 { return int64(s.cfg.MaxUploadMB) << 20 }

// saveUpload 读取并保存上传文件。
func (s *Server) saveUpload(r *http.Request, field, dst string) error {
	r.Body = http.MaxBytesReader(nil, r.Body, s.maxUpload())
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		return fmt.Errorf("上传内容解析失败（上限 %dMB）: %w", s.cfg.MaxUploadMB, err)
	}
	file, hdr, err := r.FormFile(field)
	if err != nil {
		return fmt.Errorf("未找到上传文件: %w", err)
	}
	defer file.Close()

	// 按实际后缀决定文件名，避免一律写成 .jpg
	if ext := strings.ToLower(filepath.Ext(hdr.Filename)); ext == ".png" || ext == ".webp" {
		dst = strings.TrimSuffix(dst, ".jpg") + ext
	}
	content, err := io.ReadAll(file)
	if err != nil {
		return fmt.Errorf("读取上传文件失败: %w", err)
	}
	if len(content) == 0 {
		return errors.New("上传内容为空")
	}
	if err := saveImage(content, dst); err != nil {
		return fmt.Errorf("保存图片失败: %w", err)
	}
	return nil
}

// solveOptions 从请求头汇总解题参数。
func (s *Server) solveOptions(r *http.Request, sessionID string, userID *int64) ai.SolveOptions {
	return ai.SolveOptions{
		SessionID:     sessionID,
		UserID:        userID,
		BaseHost:      s.baseHost(r),
		Engine:        s.hEngine(r),
		Model:         s.hLLMModel(r),
		Style:         s.hStyle(r),
		Thinking:      s.hThinking(r),
		LatexHelper:   s.hLatexHelper(r),
		SearchEnabled: s.hSearchEnabled(r),
		Dialect:       s.hDialect(r),
		Grade:         s.hGrade(r),
		Personality:   s.hPersonality(r),
		Subject:       s.hSubject(r),
		Detail:        s.hDetail(r),
		WeakCount:     s.weakCount(userID),
		Interactive:   s.hInteractiveQuiz(r),
		VisionModel:   s.hVisionModel(r),
	}
}

// HandleSolveImage 提交图片解题。
func (s *Server) HandleSolveImage(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	c, _ := s.currentUser(r)
	var userID *int64
	if c != nil {
		id := c.UserID
		userID = &id
	}

	requestID := newID()
	imagePath := filepath.Join(s.cfg.HistDir, requestID+".jpg")
	if err := s.saveUpload(r, "file", imagePath); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}

	opt := s.solveOptions(r, requestID, userID)
	s.bus.Start(requestID)
	go s.runSolve(r.Context(), requestID, imagePath, opt, s.hOCRMode(r), s.hVisionModel(r))

	writeJSON(w, http.StatusOK, map[string]any{
		"request_id": requestID,
		"status":     "processing",
		"message":    "解题已启动",
	})
}

type solveTextReq struct {
	Text string `json:"text"`
}

// HandleSolveText 提交纯文本解题（跳过 OCR）。
func (s *Server) HandleSolveText(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	var body solveTextReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Text = strings.TrimSpace(body.Text)
	if body.Text == "" {
		writeErr(w, http.StatusBadRequest, "题目文本不能为空")
		return
	}

	c, _ := s.currentUser(r)
	var userID *int64
	if c != nil {
		id := c.UserID
		userID = &id
	}

	requestID := newID()
	opt := s.solveOptions(r, requestID, userID)
	opt.TextInput = body.Text

	s.bus.Start(requestID)
	// 文字模式无图片，用不存在的占位路径；Python 侧见 text_input 即跳过 OCR
	go s.runSolve(r.Context(), requestID, filepath.Join(s.cfg.HistDir, requestID+".txt"), opt,
		s.hOCRMode(r), s.hVisionModel(r))

	writeJSON(w, http.StatusOK, map[string]any{
		"request_id": requestID,
		"status":     "processing",
		"message":    "文字解题已启动",
	})
}

// HandleSolveMultipage 长按多页拍摄：抽关键帧 → 逐页识别 → 合并 → 走同一条分题链路。
func (s *Server) HandleSolveMultipage(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	c, _ := s.currentUser(r)
	var userID *int64
	if c != nil {
		id := c.UserID
		userID = &id
	}

	requestID := newID()
	videoPath := filepath.Join(s.cfg.HistDir, "mp_"+requestID+".mp4")

	r.Body = http.MaxBytesReader(nil, r.Body, s.maxUpload()*4) // 视频放宽到 4 倍
	if err := r.ParseMultipartForm(16 << 20); err != nil {
		writeErr(w, http.StatusBadRequest, "上传内容解析失败")
		return
	}
	file, _, err := r.FormFile("file")
	if err != nil {
		writeErr(w, http.StatusBadRequest, "未找到上传视频")
		return
	}
	defer file.Close()
	content, err := io.ReadAll(file)
	if err != nil || len(content) == 0 {
		writeErr(w, http.StatusBadRequest, "视频内容为空")
		return
	}
	if err := os.WriteFile(videoPath, content, 0o644); err != nil {
		writeErr(w, http.StatusInternalServerError, "保存视频失败")
		return
	}

	opt := s.solveOptions(r, requestID, userID)
	opt.VideoPath = videoPath
	s.bus.Start(requestID)
	go s.runSolve(r.Context(), requestID, videoPath, opt, s.hOCRMode(r), s.hVisionModel(r))

	writeJSON(w, http.StatusOK, map[string]any{
		"request_id": requestID,
		"status":     "processing",
		"message":    "多页拍摄解题已启动",
	})
}

// runSolve 调用 Python 侧流水线，把事件转发到事件总线。
//
// 上下文要脱离请求生命周期：HTTP 响应早已返回，但解题要继续跑完。
func (s *Server) runSolve(ctx context.Context, requestID, mediaPath string, opt ai.SolveOptions, ocrMode, visionModel string) {
	ctx = context.WithoutCancel(ctx)
	defer s.bus.Finish(requestID)

	// 多页拍摄：先抽关键帧，逐页识别后拼成一篇文章再按单题走
	if opt.VideoPath != "" {
		s.bus.Emit(requestID, events.Event{Stage: events.StageInfo, Content: "正在从视频中提取关键帧..."})
		frames, err := s.ai.ExtractKeyframes(ctx, opt.VideoPath, filepath.Join(s.cfg.HistDir, "keyframes", requestID))
		if err != nil {
			s.log.Error("抽帧失败", "request_id", requestID, "err", err)
			s.bus.Emit(requestID, events.Event{Stage: events.StageError, Content: "视频处理失败: " + err.Error()})
			s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
				"request_id": requestID, "failed": true,
			}})
			return
		}
		if len(frames) == 0 {
			s.bus.Emit(requestID, events.Event{Stage: events.StageError, Content: "视频里没有可用画面"})
			s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
				"request_id": requestID, "failed": true,
			}})
			return
		}
		s.bus.Emit(requestID, events.Event{Stage: events.StageInfo,
			Content: fmt.Sprintf("已提取 %d 页，正在逐页识别...", len(frames))})

		texts := make([]string, 0, len(frames))
		for i, f := range frames {
			res, err := s.ai.OCR(ctx, f.Path, ocrMode, visionModel)
			if err == nil && strings.TrimSpace(res.Text) != "" {
				texts = append(texts, res.Text)
			}
			s.bus.Emit(requestID, events.Event{Stage: events.StageInfo,
				Content: fmt.Sprintf("第 %d/%d 页识别完成", i+1, len(frames))})
		}
		if len(texts) == 0 {
			s.bus.Emit(requestID, events.Event{Stage: events.StageError, Content: "所有页面均未识别出题目文字"})
			s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
				"request_id": requestID, "failed": true,
			}})
			return
		}
		// 用 %%% 分隔各页，让下游按"独立大题"切分
		opt.TextInput = strings.Join(texts, "\n\n%%%\n\n")
		mediaPath = frames[0].Path
	}

	start := time.Now()
	// 记录过程中是否出现错误：流水线"HTTP 200 但流里是 error 事件"也算失败，
	// 否则会在历史里留下一条空解析的垃圾记录。
	sawError := false

	err := s.ai.SolveStream(ctx, mediaPath, opt, func(ev ai.SolveEvent) error {
		if s.bus.IsCancelled(requestID) {
			return context.Canceled
		}
		if ev.Stage == string(events.StageError) {
			sawError = true
		}
		var qi *int
		if ev.QI != nil {
			v := *ev.QI
			qi = &v
		}
		s.bus.Emit(requestID, events.Event{Stage: events.Stage(ev.Stage), Content: ev.Content, QI: qi})
		return nil
	})
	if err != nil {
		if errors.Is(err, context.Canceled) || s.bus.IsCancelled(requestID) {
			s.log.Info("解题已取消", "request_id", requestID)
			return
		}
		s.log.Error("解题流水线失败", "request_id", requestID, "err", err)
		s.bus.Emit(requestID, events.Event{Stage: events.StageError, Content: err.Error()})
		s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
			"request_id": requestID, "failed": true,
		}})
		return
	}

	// 保存记录：优先用已渲染（含 LaTeX 图片）的完整解析。
	// 解题失败时只报错不入库——否则历史里会堆一堆空解析记录。
	if sawError {
		s.log.Info("解题过程含错误事件，跳过落库", "request_id", requestID)
	} else {
		s.persistSolve(requestID, mediaPath, opt, start)
	}
	s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
		"request_id": requestID,
		"total_time": round2(time.Since(start).Seconds()),
	}})
}

func round2(f float64) float64 {
	return float64(int64(f*100+0.5)) / 100
}

// existingPath 只返回真实存在的路径；文字模式的占位路径不该写进库。
func existingPath(p string) string {
	if p == "" {
		return ""
	}
	if _, err := os.Stat(p); err != nil {
		return ""
	}
	return p
}

// persistSolve 落库：Python 侧返回结构化结果，网关负责写入。
func (s *Server) persistSolve(requestID, imagePath string, opt ai.SolveOptions, start time.Time) {
	res, err := s.loadSolveResult(requestID)
	if err != nil {
		s.log.Warn("解题结果文件缺失，跳过落库（不计为失败）", "request_id", requestID, "err", err)
		return
	}
	// 空解析说明流水线没真正产出内容，落库只会污染历史
	if strings.TrimSpace(res.FullSolution) == "" {
		s.log.Info("解题结果为空，跳过落库", "request_id", requestID)
		return
	}
	rec := &store.Submission{
		SessionID:       opt.SessionID,
		UserID:          opt.UserID,
		OCRText:         res.OCRText,
		QuestionInfo:    res.QuestionInfo,
		SolutionSteps:   res.SolutionSteps,
		FullSolution:    res.FullSolution,
		MindMap:         res.MindMap,
		SuggestedQs:     res.SuggestedQs,
		SearchResult:    res.SearchResult,
		OCRTime:         res.OCRTime,
		SearchTime:      res.SearchTime,
		AITime:          time.Since(start).Seconds(),
		OriginalImgPath: existingPath(imagePath),
		RenderedSVGDir:  filepath.Join(s.cfg.HistDir, "svgs_"+opt.SessionID),
	}
	if _, err := s.db.SaveSubmission(rec); err != nil {
		s.log.Error("保存解题记录失败", "request_id", requestID, "err", err)
	}
}

type solveResult struct {
	OCRText       string         `json:"ocr_text"`
	OCRTime       float64        `json:"ocr_time"`
	QuestionInfo  map[string]any `json:"question_info"`
	SolutionSteps string         `json:"solution_steps"`
	FullSolution  string         `json:"full_solution"`
	MindMap       string         `json:"mind_map"`
	SuggestedQs   []string       `json:"suggested_questions"`
	SearchResult  string         `json:"search_result"`
	SearchTime    float64        `json:"search_time"`
	Messages      []ai.Msg       `json:"messages"`
}

func (s *Server) loadSolveResult(requestID string) (*solveResult, error) {
	p := filepath.Join(s.cfg.HistDir, "results", requestID+".json")
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	var r solveResult
	if err := json.Unmarshal(b, &r); err != nil {
		return nil, err
	}
	return &r, nil
}

// HandleSolveStream SSE 推送解题过程。
func (s *Server) HandleSolveStream(w http.ResponseWriter, r *http.Request) {
	requestID := r.PathValue("request_id")
	s.streamEvents(w, r, requestID)
}

// streamEvents 是所有 SSE 端点的公共实现。
func (s *Server) streamEvents(w http.ResponseWriter, r *http.Request, requestID string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeErr(w, http.StatusInternalServerError, "当前连接不支持流式响应")
		return
	}

	ch, release, ok := s.bus.Subscribe(requestID)
	if !ok {
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Accel-Buffering", "no")
		w.WriteHeader(http.StatusOK)
		writeSSE(w, events.Event{Stage: events.StageError, Content: "无效的request_id"})
		writeSSE(w, events.Event{Stage: events.StageComplete, Content: "end"})
		flusher.Flush()
		return
	}
	defer release()

	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no") // 禁用 nginx 缓冲
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()

	// 补读模式：任务在本次订阅前就已结束。此时缓冲里是历史事件，
	// 读空后必须自己收尾，否则客户端会一直挂着等一个永不到来的 complete。
	replay := !s.bus.Alive(requestID)
	drain := time.NewTicker(200 * time.Millisecond)
	defer drain.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ping.C:
			// 心跳：保持连接，防止中间设备按空闲超时断开
			fmt.Fprint(w, ": ping\n\n")
			flusher.Flush()
		case ev, open := <-ch:
			if !open {
				writeSSE(w, events.Event{Stage: events.StageComplete, Content: "end"})
				flusher.Flush()
				return
			}
			writeSSE(w, ev)
			flusher.Flush()
			if ev.Stage == events.StageComplete {
				return
			}
		case <-drain.C:
			// 补读模式下缓冲已空且任务早已结束 → 补一个 complete 让客户端收尾
			if !s.bus.Alive(requestID) {
				replay = true
			}
			if replay && s.bus.Drained(requestID) {
				writeSSE(w, events.Event{Stage: events.StageComplete, Content: "end"})
				flusher.Flush()
				return
			}
		}
	}
}

func writeSSE(w io.Writer, ev events.Event) {
	b, err := json.Marshal(ev)
	if err != nil {
		return
	}
	fmt.Fprintf(w, "data: %s\n\n", b)
}

// HandleSolveConfirm 用户确认 OCR 结果。
func (s *Server) HandleSolveConfirm(w http.ResponseWriter, r *http.Request) {
	if s.bus.Confirm(r.PathValue("request_id")) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "message": "已确认，继续处理"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "error", "message": "request_id无效或已过期"})
}

// HandleSelectQuestions 回传多题模式下用户选中的题号。
func (s *Server) HandleSelectQuestions(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Indices []int `json:"indices"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if s.bus.SelectQuestions(r.PathValue("request_id"), body.Indices) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "message": "已确认选题"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "error", "message": "request_id无效或已超时"})
}

// HandleCancelSolve 取消解题。
func (s *Server) HandleCancelSolve(w http.ResponseWriter, r *http.Request) {
	s.bus.Cancel(r.PathValue("request_id"))
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "message": "已取消"})
}