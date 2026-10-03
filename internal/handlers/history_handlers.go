package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gyyz/lago/internal/store"
)

type historyReq struct {
	StartDate  string   `json:"start_date"`
	EndDate    string   `json:"end_date"`
	Subjects   []string `json:"subject"`
	Grades     []string `json:"grade"`
	Difficulty []string `json:"difficulty"`
	Mastery    []string `json:"mastery"`
	Types      []string `json:"record_type"`
	Limit      int      `json:"limit"`
	Offset     int      `json:"offset"`
}

// HandleHistory 查询历史记录。
//
// 未登录时只返回自己的（NULL user_id 的）旧数据以兼容历史库；登录后严格按用户隔离。
func (s *Server) HandleHistory(w http.ResponseWriter, r *http.Request) {
	var body historyReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	_, userID := s.currentUser(r)

	rows, err := s.db.History(store.HistoryFilter{
		UserID:     userID,
		StartDate:  strings.TrimSpace(body.StartDate),
		EndDate:    strings.TrimSpace(body.EndDate),
		Subjects:   body.Subjects,
		Grades:     body.Grades,
		Difficulty: body.Difficulty,
		Mastery:    body.Mastery,
		Types:      body.Types,
		Limit:      body.Limit,
		Offset:     body.Offset,
	})
	if err != nil {
		s.log.Error("查询历史记录失败", "err", err)
		writeErr(w, http.StatusInternalServerError, "查询历史记录失败")
		return
	}

	// 附带每条记录的掌握程度与图片 URL（前端直接可用）
	for _, row := range rows {
		sid, _ := row["session_id"].(string)
		if img, _ := row["original_image_path"].(string); img != "" {
			row["image_url"] = "/static/svgs/" + filepath.Base(img)
		}
		if lvl, ok := s.readMastery(sid); ok {
			row["mastery_level"] = lvl
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"records": rows,
		"total":   len(rows),
	})
}

func (s *Server) readMastery(sessionID string) (string, bool) {
	b, err := os.ReadFile(filepath.Join(s.cfg.HistDir, "mastery_records", sessionID+".json"))
	if err != nil {
		return "", false
	}
	var rec struct {
		MasteryLevel string `json:"mastery_level"`
	}
	if json.Unmarshal(b, &rec) != nil {
		return "", false
	}
	return rec.MasteryLevel, true
}

type idsReq struct {
	RecordIDs []int64 `json:"record_ids"`
}

// HandleDeleteHistory 批量删除记录（单条也走这里）。
func (s *Server) HandleDeleteHistory(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodDelete && r.ContentLength == 0 {
		s.handleDeleteOne(w, r)
		return
	}
	var body idsReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if len(body.RecordIDs) == 0 {
		writeErr(w, http.StatusBadRequest, "未指定要删除的记录")
		return
	}
	_, userID := s.currentUser(r)

	solveIDs, auxIDs, err := s.db.SplitIDs(body.RecordIDs, userID)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "删除失败")
		return
	}
	n1, err := s.db.DeleteSubmissions(solveIDs, userID)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "删除失败")
		return
	}
	n2, err := s.db.DeleteAux(auxIDs, userID)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "删除失败")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok", "deleted": n1 + n2,
	})
}

func (s *Server) handleDeleteOne(w http.ResponseWriter, r *http.Request) {
	id, err := parseID(r.PathValue("record_id"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "记录 ID 无效")
		return
	}
	_, userID := s.currentUser(r)
	n, err := s.db.DeleteSubmissions([]int64{id}, userID)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "删除失败")
		return
	}
	if n == 0 {
		if m, err := s.db.DeleteAux([]int64{id}, userID); err == nil {
			n = m
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "deleted": n})
}

// HandleDeleteOne 按 ID 删除单条记录（DELETE /history/{record_id}）。
func (s *Server) HandleDeleteOne(w http.ResponseWriter, r *http.Request) {
	s.handleDeleteOne(w, r)
}

// HandleBatchDeleteHistory 批量删除（POST 显式路径）。
func (s *Server) HandleBatchDeleteHistory(w http.ResponseWriter, r *http.Request) {
	s.HandleDeleteHistory(w, r)
}

// ---------- 学情报告 ----------

type reportReq struct {
	Days    int    `json:"days"`
	Theme   string `json:"theme"`
	Grade   string `json:"grade"`
	Subject string `json:"subject"`
}

func (s *Server) parseReportReq(r *http.Request) (reportReq, error) {
	var body reportReq
	if err := decodeJSON(r, &body); err != nil {
		return body, err
	}
	if body.Days <= 0 || body.Days > 365 {
		body.Days = 30
	}
	if body.Theme != "light" {
		body.Theme = "dark"
	}
	if body.Grade == "" {
		body.Grade = s.hGrade(r)
	}
	if body.Subject == "" {
		body.Subject = s.hSubject(r)
	}
	return body, nil
}

// HandleReportData 数据版学情报告（图表用，无需调模型）。
func (s *Server) HandleReportData(w http.ResponseWriter, r *http.Request) {
	body, err := s.parseReportReq(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	_, userID := s.currentUser(r)

	stats, err := s.db.Stats(userID, body.Days)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "统计失败")
		return
	}

	subjects := sortedKeys(stats.Subjects)
	difficulties := sortedKeys(stats.Difficulty)
	knowledge := sortedKeys(stats.KnowledgePt)
	if len(subjects) > 8 {
		subjects = subjects[:8]
	}
	if len(difficulties) > 6 {
		difficulties = difficulties[:6]
	}
	if len(knowledge) > 10 {
		knowledge = knowledge[:10]
	}

	accuracy := 0.0
	if stats.Total > 0 {
		accuracy = float64(stats.Correct) / float64(stats.Total) * 100
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"days":    body.Days,
		"theme":   body.Theme,
		"grade":   body.Grade,
		"subject": body.Subject,
		"summary": map[string]any{
			"total":     stats.Total,
			"correct":   stats.Correct,
			"wrong":     stats.Wrong,
			"accuracy":  round2(accuracy),
			"avg_per_day": round2(float64(stats.Total) / float64(body.Days)),
		},
		"subjects":    toPairs(stats.Subjects),
		"difficulties": toPairs(stats.Difficulty),
		"knowledge":   toPairsOf(knowledge, stats.KnowledgePt),
		"trend":       stats.Trend,
	})
}

func toPairs(m map[string]int) []map[string]any {
	keys := sortedKeys(m)
	out := make([]map[string]any, 0, len(keys))
	for _, k := range keys {
		out = append(out, map[string]any{"name": k, "value": m[k]})
	}
	return out
}

func toPairsOf(keys []string, m map[string]int) []map[string]any {
	out := make([]map[string]any, 0, len(keys))
	for _, k := range keys {
		out = append(out, map[string]any{"name": k, "value": m[k]})
	}
	return out
}

type mistakesReq struct {
	Limit int `json:"limit"`
}

// HandleReportMistakes 汇总易错点。
func (s *Server) HandleReportMistakes(w http.ResponseWriter, r *http.Request) {
	var body mistakesReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if body.Limit <= 0 || body.Limit > 50 {
		body.Limit = 10
	}
	_, userID := s.currentUser(r)

	raw, err := s.db.Mistakes(userID, body.Limit)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "统计失败")
		return
	}
	text, err := s.ai.ReportMistakes(r.Context(), raw, body.Limit)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "生成失败: "+err.Error())
		return
	}

	keys := sortedKeys(raw)
	items := make([]map[string]any, 0, len(keys))
	for _, k := range keys {
		items = append(items, map[string]any{"name": k, "count": raw[k]})
	}
	writeJSON(w, http.StatusOK, map[string]any{"text": text, "items": items})
}

// HandleReportAI 生成 AI 版学情报告。
func (s *Server) HandleReportAI(w http.ResponseWriter, r *http.Request) {
	if resp := s.checkAIPermission(r); resp != nil {
		writeJSON(w, http.StatusOK, resp)
		return
	}
	body, err := s.parseReportReq(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	_, userID := s.currentUser(r)

	stats, err := s.db.Stats(userID, body.Days)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "统计失败")
		return
	}
	if stats.Total == 0 {
		// 题量不足时返回普通 JSON（HTTP 200），前端会识别非 SSE 响应并展示原因
		writeJSON(w, http.StatusOK, map[string]any{
			"message": fmt.Sprintf("最近 %d 天还没有作答记录，至少需要 5 道题才能生成学情报告", body.Days),
		})
		return
	}

	report, err := s.ai.AIReport(r.Context(), statsToMap(stats), body.Subject, body.Grade,
		body.Theme, s.hEngine(r), s.hLLMModel(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "生成失败: "+err.Error())
		return
	}

	// 报告落盘，便于前端按 URL 引用
	name := fmt.Sprintf("report_%d_%s.md", time.Now().Unix(), s.nextReportSuffix())
	if err := os.WriteFile(filepath.Join(s.cfg.ReportDir, name), []byte(report), 0o644); err != nil {
		s.log.Warn("报告落盘失败", "err", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"report":     report,
		"report_url": "/static/reports/" + name,
	})
}

var reportSeq int

func (s *Server) nextReportSuffix() string {
	reportSeq++
	return fmt.Sprintf("%d", reportSeq)
}

func statsToMap(st *store.HistoryStats) map[string]any {
	accuracy := 0.0
	if st.Total > 0 {
		accuracy = float64(st.Correct) / float64(st.Total) * 100
	}
	return map[string]any{
		"total":       st.Total,
		"correct":     st.Correct,
		"wrong":       st.Wrong,
		"accuracy":    round2(accuracy),
		"subjects":    st.Subjects,
		"grades":      st.Grades,
		"difficulty":  st.Difficulty,
		"knowledge":   st.KnowledgePt,
		"trend_count": len(st.Trend),
	}
}

// ---------- 导出 ----------

type exportReq struct {
	Title   string `json:"title"`
	Content string `json:"content"`
	Format  string `json:"format"`
}

// HandleExport 导出历史记录为 Word / PDF。
func (s *Server) HandleExport(w http.ResponseWriter, r *http.Request) {
	var body exportReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if strings.TrimSpace(body.Content) == "" {
		writeErr(w, http.StatusBadRequest, "导出内容为空")
		return
	}
	if body.Format != "word" && body.Format != "docx" {
		body.Format = "pdf"
	}
	if body.Title == "" {
		body.Title = "学习记录导出"
	}

	path, err := s.ai.Export(r.Context(), body.Title, body.Content, body.Format, s.baseHost(r))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "导出失败: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":    "ok",
		"file_url":  "/static/exports/" + filepath.Base(path),
		"file_name": filepath.Base(path),
	})
}