// Package handlers 实现全部 HTTP 端点。
package handlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/gyyz/lago/internal/ai"
	"github.com/gyyz/lago/internal/auth"
	"github.com/gyyz/lago/internal/config"
	"github.com/gyyz/lago/internal/events"
	"github.com/gyyz/lago/internal/store"
)

// Server 持有全部依赖。
type Server struct {
	cfg  *config.Config
	db   *store.Store
	auth *auth.Manager
	perm *auth.Permission
	bus  *events.Bus
	ai   *ai.Client
	log  *slog.Logger
}

// New 创建 Server。
func New(cfg *config.Config, db *store.Store, am *auth.Manager, perm *auth.Permission, bus *events.Bus, aic *ai.Client, log *slog.Logger) *Server {
	return &Server{cfg: cfg, db: db, auth: am, perm: perm, bus: bus, ai: aic, log: log}
}

// ---------- 通用辅助 ----------

type ctxKey string

const (
	ctxClaims ctxKey = "claims"
	ctxUserID ctxKey = "user_id"
)

// currentUser 返回当前登录者；未登录时 userID 为 nil。
func (s *Server) currentUser(r *http.Request) (*auth.Claims, *int64) {
	c, _ := r.Context().Value(ctxClaims).(*auth.Claims)
	if c == nil {
		return nil, nil
	}
	id := c.UserID
	return c, &id
}

// requireLogin 强制登录，失败写 401。
func (s *Server) requireLogin(w http.ResponseWriter, r *http.Request) (*auth.Claims, bool) {
	c, ok := r.Context().Value(ctxClaims).(*auth.Claims)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"status": "error", "code": 401, "message": "请先登录",
		})
		return nil, false
	}
	return c, true
}

// checkAIPermission 校验 AI 使用权限。返回受限时的 JSON 响应，nil 表示放行。
func (s *Server) checkAIPermission(r *http.Request) map[string]any {
	c, _ := r.Context().Value(ctxClaims).(*auth.Claims)
	if c == nil {
		return map[string]any{"status": "error", "code": 401, "message": "请先登录后再使用 AI 功能"}
	}
	if !s.perm.CanUseAI(c.Username, c.IsAdmin) {
		return map[string]any{"status": "error", "code": 403, "message": auth.RestrictedMsg}
	}
	return nil
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	if v == nil {
		return
	}
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("写 JSON 响应失败", "err", err)
	}
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{"status": "error", "message": msg, "detail": msg})
}

// decodeJSON 严格解析 JSON 请求体：拒绝未知字段，避免静默吞掉拼错的键。
func decodeJSON(r *http.Request, v any) error {
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 8<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("请求体解析失败: %w", err)
	}
	return nil
}

// ---------- 请求头参数解析 ----------
//
// 这些头由前端 api.js 统一设置，语义保持与原项目一致。

func (s *Server) hEngine(r *http.Request) string {
	switch v := strings.ToLower(r.Header.Get("X-Engine")); v {
	case "qwen", "doubao", "hunyuan":
		return v
	default:
		return "deepseek"
	}
}

func (s *Server) hLLMModel(r *http.Request) string { return strings.TrimSpace(r.Header.Get("X-LLM-Model")) }

func (s *Server) hOCRMode(r *http.Request) string {
	v := strings.ToLower(strings.TrimSpace(r.Header.Get("X-OCR-Mode")))
	if v == "paddle" || v == "qwen" {
		return v
	}
	return s.cfg.OCRDefaultMode
}

func (s *Server) hVisionModel(r *http.Request) string { return strings.TrimSpace(r.Header.Get("X-Vision-Model")) }

func (s *Server) hStyle(r *http.Request) string {
	// 兼容旧头 X-Answer-Style
	v := strings.ToLower(strings.TrimSpace(r.Header.Get("X-Style")))
	if v == "" {
		v = strings.ToLower(strings.TrimSpace(r.Header.Get("X-Answer-Style")))
	}
	switch v {
	case "formal", "encouraging", "humorous", "plain", "concise", "lively", "dialect":
		return v
	}
	return ""
}

func (s *Server) hTheme(r *http.Request) string {
	if strings.EqualFold(strings.TrimSpace(r.Header.Get("X-Theme")), "light") {
		return "light"
	}
	return "dark"
}

// headerDecoded 读取一个 URL-encoded 的请求头。
func headerDecoded(r *http.Request, name string) string {
	v := strings.TrimSpace(r.Header.Get(name))
	if v == "" {
		return ""
	}
	if d, err := url.QueryUnescape(v); err == nil {
		return d
	}
	return v
}

func (s *Server) hPersonality(r *http.Request) string { return headerDecoded(r, "X-Personality") }
func (s *Server) hDialect(r *http.Request) string {
	if v := headerDecoded(r, "X-Dialect"); v != "" {
		return v
	}
	return "普通话"
}
func (s *Server) hGrade(r *http.Request) string    { return headerDecoded(r, "X-Grade") }
func (s *Server) hSubject(r *http.Request) string   { return headerDecoded(r, "X-Subject") }
func (s *Server) hDetail(r *http.Request) string    { return headerDecoded(r, "X-Detail") }

func headerSwitch(v string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "auto":
		return "auto", true
	case "1", "true", "yes", "on":
		return "1", true
	case "0", "false", "no", "off":
		return "0", true
	}
	return "", false
}

func (s *Server) hThinking(r *http.Request) string {
	v, ok := headerSwitch(r.Header.Get("X-Thinking"))
	if !ok {
		return ""
	}
	return v
}

func (s *Server) hLatexHelper(r *http.Request) string {
	v, ok := headerSwitch(r.Header.Get("X-Latex-Helper"))
	if !ok {
		return "auto"
	}
	return v
}

func (s *Server) hInteractiveQuiz(r *http.Request) bool {
	v, _ := headerSwitch(r.Header.Get("X-Interactive-Quiz"))
	return v == "1"
}

func (s *Server) hSearchEnabled(r *http.Request) bool {
	if !s.cfg.EnableQuestionSearch {
		return false
	}
	v, _ := headerSwitch(r.Header.Get("X-Search-Enabled"))
	return v != "0"
}

// baseHost 取请求 Host，用于构造图片绝对 URL。
func (s *Server) baseHost(r *http.Request) string { return r.Host }

// weakCount 统计未完全掌握的知识点数，用于 detail=auto 决策。
func (s *Server) weakCount(userID *int64) int {
	dir := filepath.Join(s.cfg.HistDir, "mastery_records")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var rec struct {
			UserID       *int64 `json:"user_id"`
			MasteryLevel string `json:"mastery_level"`
		}
		if json.Unmarshal(b, &rec) != nil {
			continue
		}
		if !sameID(rec.UserID, userID) {
			continue
		}
		if rec.MasteryLevel == "not_mastered" || rec.MasteryLevel == "partially_mastered" {
			n++
		}
	}
	return n
}

func sameID(a, b *int64) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

// ---------- 图片保存 ----------

// saveImage 把上传的图片压缩后落盘：最长边压到 1000px 以内，既够 OCR 识别又省空间。
func saveImage(content []byte, dst string) error {
	src, format, err := image.Decode(strings.NewReader(string(content)))
	if err != nil {
		// 解码失败时不静默丢数据，原样落盘由下游处理
		return os.WriteFile(dst, content, 0o644)
	}
	const maxSide = 1000
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	if w <= maxSide && h <= maxSide {
		return writeJPEG(dst, src)
	}
	scale := float64(maxSide) / float64(w)
	if h > w {
		scale = float64(maxSide) / float64(h)
	}
	nw, nh := int(float64(w)*scale), int(float64(h)*scale)
	dstImg := image.NewRGBA(image.Rect(0, 0, nw, nh))
	// 简易双线性缩放
	for y := 0; y < nh; y++ {
		sy := b.Min.Y + y*h/nh
		for x := 0; x < nw; x++ {
			sx := b.Min.X + x*w/nw
			dstImg.Set(x, y, src.At(sx, sy))
		}
	}
	_ = format
	return writeJPEG(dst, dstImg)
}

func writeJPEG(path string, img image.Image) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return encodeJPEG(f, img)
}

// ---------- 静态资源 ----------

// StaticDir 返回被允许通过 HTTP 暴露的目录及其 URL 前缀。
// 只暴露这几处：图片、报告、导出物、动画、网页端。其余目录（含数据库）不对外。
func (s *Server) StaticDirs() map[string]string {
	return map[string]string{
		"/static/svgs":       s.cfg.HistDir,
		"/static/reports":    s.cfg.ReportDir,
		"/static/exports":    s.cfg.ExportDir,
		"/static/annotations": s.cfg.AnnotateDir,
		"/static/animations": s.cfg.AnimDir,
	}
}

// safeJoin 拼接路径并阻断 `..` 穿越。
func safeJoin(root, rel string) (string, error) {
	cleaned := filepath.Clean("/" + strings.TrimPrefix(rel, "/"))
	full := filepath.Join(root, cleaned)
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	fullAbs, err := filepath.Abs(full)
	if err != nil {
		return "", err
	}
	if fullAbs != rootAbs && !strings.HasPrefix(fullAbs, rootAbs+string(os.PathSeparator)) {
		return "", errors.New("非法路径")
	}
	return fullAbs, nil
}

// ---------- 杂项 ----------

func newID() string { return uuid.NewString() }

func parseID(s string) (int64, error) { return strconv.ParseInt(s, 10, 64) }

func nowRFC3339() string { return time.Now().Format(time.RFC3339) }

func jsonMarshal(v any) ([]byte, error) { return json.MarshalIndent(v, "", "  ") }