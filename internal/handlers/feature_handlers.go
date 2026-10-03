package handlers

import (
	"context"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/gyyz/lago/internal/ai"
	"github.com/gyyz/lago/internal/events"
	"github.com/gyyz/lago/internal/store"
)

type askReq struct {
	SessionID string   `json:"session_id"`
	Question  string   `json:"question"`
	Context   []ai.Msg `json:"context"`
}

// HandleAsk 非流式追问。
func (s *Server) HandleAsk(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	var body askReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Question = strings.TrimSpace(body.Question)
	if body.SessionID == "" || body.Question == "" {
		writeErr(w, http.StatusBadRequest, "session_id 与 question 不能为空")
		return
	}

	_, userID := s.currentUser(r)

	// 上下文优先取数据库；该会话无对话（如知识延伸/动画）时用客户端传入的正文兜底
	msgs, err := s.db.ConversationBySession(body.SessionID)
	var history []ai.Msg
	for _, m := range msgs {
		history = append(history, ai.Msg{Role: m.Role, Content: m.Content})
	}
	history = cleanConversation(history)
	if len(history) == 0 && len(body.Context) > 0 {
		history = cleanConversation(body.Context)
	}

	answer, err := s.ai.Ask(r.Context(), history, body.Question,
		s.hEngine(r), s.hLLMModel(r), s.hStyle(r), s.hDialect(r), s.hGrade(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}

	// 保存问答，保证后续追问上下文连续
	s.db.AppendConversation(body.SessionID, userID, "user", body.Question)
	s.db.AppendConversation(body.SessionID, userID, "assistant", answer)

	writeJSON(w, http.StatusOK, map[string]any{"answer": answer, "session_id": body.SessionID})
}

// HandleAskStream 流式追问。
func (s *Server) HandleAskStream(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusForbidden, resp)
		return
	}
	var body askReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Question = strings.TrimSpace(body.Question)
	if body.SessionID == "" || body.Question == "" {
		writeErr(w, http.StatusBadRequest, "session_id 与 question 不能为空")
		return
	}

	_, userID := s.currentUser(r)
	msgs, _ := s.db.ConversationBySession(body.SessionID)
	var history []ai.Msg
	for _, m := range msgs {
		history = append(history, ai.Msg{Role: m.Role, Content: m.Content})
	}
	history = cleanConversation(history)
	if len(history) == 0 && len(body.Context) > 0 {
		history = cleanConversation(body.Context)
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeErr(w, http.StatusInternalServerError, "当前连接不支持流式响应")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	var answer strings.Builder
	err := s.ai.StreamSSE(r.Context(), "/ask/stream", map[string]any{
		"messages": history,
		"question": body.Question,
		"engine":   s.hEngine(r),
		"model":    s.hLLMModel(r),
		"style":    s.hStyle(r),
		"dialect":  s.hDialect(r),
		"grade":    s.hGrade(r),
	}, func(ev ai.SolveEvent) error {
		if ev.Stage == "chunk" || ev.Stage == "solution_chunk" {
			if str, ok := ev.Content.(string); ok {
				answer.WriteString(str)
			}
		}
		writeSSE(w, events.Event{Stage: events.Stage(ev.Stage), Content: ev.Content})
		flusher.Flush()
		if ev.Stage == string(events.StageComplete) {
			return context.Canceled // 提前收尾，不再读剩余帧
		}
		return nil
	})
	if err != nil && !errors.Is(err, context.Canceled) {
		writeSSE(w, events.Event{Stage: events.StageError, Content: err.Error()})
		flusher.Flush()
		return
	}

	if txt := answer.String(); txt != "" {
		s.db.AppendConversation(body.SessionID, userID, "user", body.Question)
		s.db.AppendConversation(body.SessionID, userID, "assistant", txt)
	}
}

// internalPromptPrefixes 是解题流水线的内部提示词前缀。
//
// 追问时必须剔除这些轮次：模型会把"输出 JSON 数组"的指令延续到追问答案里，
// 导致回答变成 JSON。
var internalPromptPrefixes = []string{
	"请分析这道题目的结构",
	"请给出完整的解题过程和答案",
	"请生成思维导图",
	"请预判",
	"请输出",
}

const fullSolutionAsk = "请给出完整的解题过程和答案"

// cleanConversation 从解题会话中提取干净的追问上下文：
// 保留题面、完整解析与真实问答对，剔除内部流水线指令及其回复。
func cleanConversation(in []ai.Msg) []ai.Msg {
	var out []ai.Msg
	solution := ""
	skipNext, captureNext := false, false

	for _, m := range in {
		content := strings.TrimSpace(m.Content)
		switch m.Role {
		case "system":
			out = append(out, ai.Msg{Role: "system", Content: content})
		case "user":
			if strings.HasPrefix(content, fullSolutionAsk) {
				captureNext = true
				continue
			}
			if hasInternalPrefix(content) {
				skipNext = true
				continue
			}
			captureNext, skipNext = false, false
			out = append(out, ai.Msg{Role: "user", Content: content})
		default:
			if captureNext {
				captureNext = false
				solution = content
				continue
			}
			if skipNext {
				skipNext = false
				continue
			}
			out = append(out, ai.Msg{Role: "assistant", Content: content})
		}
	}

	if solution != "" {
		insertAt := 0
		if len(out) > 0 && out[0].Role == "system" {
			insertAt = 1
		}
		rest := append([]ai.Msg{}, out[insertAt:]...)
		out = append(append(out[:insertAt:insertAt], ai.Msg{Role: "assistant", Content: solution}), rest...)
	}
	return out
}

func hasInternalPrefix(s string) bool {
	for _, p := range internalPromptPrefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// ---------- 知识延伸 ----------

type extendReq struct {
	Text string `json:"text"`
}

// HandleExtendText 文字版知识延伸。
func (s *Server) HandleExtendText(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	var body extendReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Text = strings.TrimSpace(body.Text)
	if body.Text == "" {
		writeErr(w, http.StatusBadRequest, "内容不能为空")
		return
	}
	s.startExtend(w, r, filepath.Join(s.cfg.HistDir, newID()+".txt"), body.Text)
}

// HandleExtendImage 图片版知识延伸。
func (s *Server) HandleExtendImage(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	requestID := newID()
	imagePath := filepath.Join(s.cfg.HistDir, requestID+".jpg")
	if err := s.saveUpload(r, "file", imagePath); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	s.startExtendWithID(w, r, requestID, imagePath, "")
}

func (s *Server) startExtend(w http.ResponseWriter, r *http.Request, imagePath, text string) {
	s.startExtendWithID(w, r, newID(), imagePath, text)
}

func (s *Server) startExtendWithID(w http.ResponseWriter, r *http.Request, requestID, imagePath, text string) {
	_, userID := s.currentUser(r)

	opt := s.solveOptions(r, requestID, userID)
	opt.TextInput = text

	s.bus.Start(requestID)
	go func() {
		ctx := context.WithoutCancel(r.Context())
		defer s.bus.Finish(requestID)
		start := time.Now()
		var final struct {
			Title   string `json:"title"`
			Content string `json:"content"`
			OCRText string `json:"ocr_text"`
		}
		err := s.ai.ExtendStream(ctx, imagePath, requestID, userID, opt, func(ev ai.SolveEvent) error {
			switch ev.Stage {
			case "extension_result":
				if m, ok := ev.Content.(map[string]any); ok {
					if t, ok := m["title"].(string); ok {
						final.Title = t
					}
					if t, ok := m["content"].(string); ok {
						final.Content = t
					}
					if t, ok := m["ocr_text"].(string); ok {
						final.OCRText = t
					}
				}
			case "error":
				if str, ok := ev.Content.(string); ok {
					final.Content = "生成失败：" + str
				}
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
				return
			}
			s.log.Error("知识延伸失败", "request_id", requestID, "err", err)
			s.bus.Emit(requestID, events.Event{Stage: events.StageError, Content: err.Error()})
		}
		if final.Content != "" {
			s.db.SaveAux(store.AuxRecord{
				SessionID: requestID, UserID: userID, Type: "extension",
				Title: orDefault(final.Title, "知识延伸"), Content: final.Content,
			})
		}
		s.bus.Emit(requestID, events.Event{Stage: events.StageComplete, Content: map[string]any{
			"request_id": requestID, "total_time": round2(time.Since(start).Seconds()),
		}})
	}()

	writeJSON(w, http.StatusOK, map[string]any{
		"request_id": requestID, "status": "processing", "message": "知识延伸已启动",
	})
}

func orDefault(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}

// HandleExtendStream 知识延伸 SSE。
func (s *Server) HandleExtendStream(w http.ResponseWriter, r *http.Request) {
	s.streamEvents(w, r, r.PathValue("request_id"))
}

// ---------- AI 动画 ----------

type animateReq struct {
	Text string `json:"text"`
}

// HandleAnimateText 文字生成动画。
func (s *Server) HandleAnimateText(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	if !s.cfg.EnableAnimation {
		writeErr(w, http.StatusServiceUnavailable, "AI 动画功能已关闭")
		return
	}
	var body animateReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if strings.TrimSpace(body.Text) == "" {
		writeErr(w, http.StatusBadRequest, "内容不能为空")
		return
	}
	s.runAnimate(w, r, body.Text, "")
}

// HandleAnimateImage 图片生成动画。
func (s *Server) HandleAnimateImage(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	if !s.cfg.EnableAnimation {
		writeErr(w, http.StatusServiceUnavailable, "AI 动画功能已关闭")
		return
	}
	requestID := newID()
	imagePath := filepath.Join(s.cfg.HistDir, requestID+".jpg")
	if err := s.saveUpload(r, "file", imagePath); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}

	ocr, err := s.ai.OCR(r.Context(), imagePath, s.hOCRMode(r), s.hVisionModel(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "识别题目失败: "+err.Error())
		return
	}
	s.runAnimate(w, r, ocr.Text, requestID)
}

func (s *Server) runAnimate(w http.ResponseWriter, r *http.Request, text, sessionID string) {
	_, userID := s.currentUser(r)

	// 动画基于题目文本生成，并落 aux_records 以便历史页查看
	res, err := s.ai.Animate(r.Context(), text, "", s.hEngine(r), s.hTheme(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "动画生成失败: "+err.Error())
		return
	}

	if sessionID == "" {
		sessionID = newID()
	}
	s.db.SaveAux(store.AuxRecord{
		SessionID: sessionID, UserID: userID, Type: "animation",
		Title: "AI 动画", Content: res.HTMLURL,
	})

	writeJSON(w, http.StatusOK, map[string]any{
		"status":    "ok",
		"html_url":  res.HTMLURL,
		"session_id": sessionID,
	})
}

// ---------- AI 批注 ----------

// HandleAnnotate 生成批注图。
func (s *Server) HandleAnnotate(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	requestID := newID()
	imagePath := filepath.Join(s.cfg.HistDir, requestID+".jpg")
	if err := s.saveUpload(r, "file", imagePath); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}

	// 输出文件名带 requestID，避免并发批注互相覆盖
	outPath := filepath.Join(s.cfg.AnnotateDir, requestID+".png")
	res, err := s.ai.AnnotateTo(r.Context(), imagePath, s.hOCRMode(r), s.hVisionModel(r), outPath, "/static/annotations")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "批注生成失败: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":      "ok",
		"request_id":  requestID,
		"image_url":   res.ImageURL,
		"annotations": res.Annotations,
		"original_image_url": "/static/svgs/" + requestID + ".jpg",
	})
}

// ---------- 番茄钟 ----------

type pomodoroReq struct {
	OCRText string `json:"ocr_text"`
	Summary string `json:"summary"`
}

// HandlePomodoroRecommend 按题目难度推荐专注时长。
func (s *Server) HandlePomodoroRecommend(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	var body pomodoroReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	out, err := s.ai.PomodoroRecommend(r.Context(), body.OCRText, body.Summary, s.hEngine(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "推荐失败: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "data": out})
}

// ---------- 掌握程度 ----------

type masteryReq struct {
	RequestID    string `json:"request_id"`
	MasteryLevel string `json:"mastery_level"`
}

// HandleMastery 保存掌握程度。
func (s *Server) HandleMastery(w http.ResponseWriter, r *http.Request) {
	var body masteryReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if body.RequestID == "" {
		writeErr(w, http.StatusBadRequest, "request_id 不能为空")
		return
	}
	_, userID := s.currentUser(r)

	dir := filepath.Join(s.cfg.HistDir, "mastery_records")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		writeErr(w, http.StatusInternalServerError, "创建目录失败")
		return
	}
	payload := map[string]any{
		"request_id":    body.RequestID,
		"mastery_level": body.MasteryLevel,
		"timestamp":     nowRFC3339(),
		"user_id":       userID,
	}
	b, _ := jsonMarshal(payload)
	if err := os.WriteFile(filepath.Join(dir, body.RequestID+".json"), b, 0o644); err != nil {
		writeErr(w, http.StatusInternalServerError, "保存失败")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "message": "掌握程度已保存"})
}

// ---------- 专注度同步 ----------

type trackingReq struct {
	SessionID string  `json:"session_id"`
	FocusState string `json:"focus_state"`
	Duration   float64 `json:"duration_seconds"`
	PageNumber int     `json:"page_number"`
	Pomodoro   int     `json:"pomodoro_count"`
}

// HandleTrackingSync 同步专注度数据。
func (s *Server) HandleTrackingSync(w http.ResponseWriter, r *http.Request) {
	var body trackingReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	_, userID := s.currentUser(r)
	if err := s.db.SaveTracking(body.SessionID, userID, body.FocusState,
		body.Duration, body.PageNumber, body.Pomodoro); err != nil {
		writeErr(w, http.StatusInternalServerError, "保存失败")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok"})
}

// ---------- 排序辅助 ----------

// sortedKeys 返回 map 的键，按值降序（用于报告里"最易错的知识点"）。
func sortedKeys(m map[string]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return m[keys[i]] > m[keys[j]] })
	return keys
}