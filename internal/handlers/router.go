package handlers

import (
	"context"
	"log/slog"
	"net/http"
	"runtime/debug"
	"time"

	"github.com/gyyz/lago/internal/auth"
)

// Middleware 链式中间件。
type Middleware func(http.Handler) http.Handler

// chain 按声明顺序自外向内包裹。
func chain(h http.Handler, mws ...Middleware) http.Handler {
	for i := len(mws) - 1; i >= 0; i-- {
		h = mws[i](h)
	}
	return h
}

// withAuth 解析 Bearer token 并注入上下文。解析失败不拒绝，交由各端点自行判定。
func (s *Server) withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if tok := auth.BearerToken(r.Header.Get("Authorization")); tok != "" {
			if claims, err := s.auth.Parse(tok); err == nil {
				r = r.WithContext(context.WithValue(r.Context(), ctxClaims, claims))
			}
		}
		next.ServeHTTP(w, r)
	})
}

// withRecover 捕获 panic，避免单个请求打挂整个进程。
func withRecover(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rec := recover(); rec != nil {
					log.Error("请求处理 panic", "err", rec, "stack", string(debug.Stack()))
					writeErr(w, http.StatusInternalServerError, "服务器内部错误")
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// withLogging 记录访问日志。
func (s *Server) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		s.log.Info("请求", "method", r.Method, "path", r.URL.Path,
			"status", rec.status, "ms", time.Since(start).Milliseconds())
	})
}

// withCORS 允许跨域访问。
func withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "*")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// Routes 注册全部端点。
//
// 路径与原 FastAPI 版本保持一致，网页端 api.js 与安卓端无需任何改动即可对接。
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	// 认证
	mux.HandleFunc("POST /auth/register", s.HandleRegister)
	mux.HandleFunc("POST /auth/login", s.HandleLogin)
	mux.HandleFunc("POST /auth/verify", s.HandleVerify)

	// 解题
	mux.HandleFunc("POST /solve", s.HandleSolveImage)
	mux.HandleFunc("POST /solve/text", s.HandleSolveText)
	mux.HandleFunc("POST /solve/multipage", s.HandleSolveMultipage)
	mux.HandleFunc("GET /solve/stream/{request_id}", s.HandleSolveStream)
	mux.HandleFunc("POST /solve/confirm/{request_id}", s.HandleSolveConfirm)
	mux.HandleFunc("POST /solve/select_questions/{request_id}", s.HandleSelectQuestions)
	mux.HandleFunc("POST /solve/cancel/{request_id}", s.HandleCancelSolve)

	// 追问
	mux.HandleFunc("POST /ask", s.HandleAsk)
	mux.HandleFunc("POST /ask/stream", s.HandleAskStream)

	// 知识延伸
	mux.HandleFunc("POST /extend", s.HandleExtendImage)
	mux.HandleFunc("POST /extend/text", s.HandleExtendText)
	mux.HandleFunc("GET /extend/stream/{request_id}", s.HandleExtendStream)

	// 动画 / 批注
	mux.HandleFunc("POST /animation", s.HandleAnimateImage)
	mux.HandleFunc("POST /animation/text", s.HandleAnimateText)
	mux.HandleFunc("POST /annotate", s.HandleAnnotate)

	// 报告
	mux.HandleFunc("POST /report/data", s.HandleReportData)
	mux.HandleFunc("POST /report/ai", s.HandleReportAI)
	mux.HandleFunc("POST /report/mistakes", s.HandleReportMistakes)

	// 历史
	mux.HandleFunc("POST /history", s.HandleHistory)
	mux.HandleFunc("DELETE /history", s.HandleDeleteHistory)
	mux.HandleFunc("DELETE /history/{record_id}", s.HandleDeleteOne)
	mux.HandleFunc("POST /history/batch-delete", s.HandleBatchDeleteHistory)

	// 其它
	mux.HandleFunc("POST /export", s.HandleExport)
	mux.HandleFunc("POST /mastery", s.HandleMastery)
	mux.HandleFunc("POST /pomodoro/recommend", s.HandlePomodoroRecommend)
	mux.HandleFunc("POST /tracking/sync", s.HandleTrackingSync)
	mux.HandleFunc("GET /health", s.HandleHealth)
	mux.HandleFunc("GET /favicon.ico", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})

	// 静态资源
	s.mountStatic(mux)

	return chain(mux, withRecover(s.log), s.withLogging, withCORS, s.withAuth)
}

// mountStatic 挂载静态目录。只暴露白名单内的目录。
func (s *Server) mountStatic(mux *http.ServeMux) {
	for prefix, dir := range s.StaticDirs() {
		d := dir
		p := prefix
		mux.Handle("GET "+p+"/", http.StripPrefix(p+"/", http.FileServer(http.Dir(d))))
	}

	// 网页端
	mux.Handle("GET /web/", http.StripPrefix("/web/", http.FileServer(http.Dir(s.cfg.StaticDir))))
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			writeErr(w, http.StatusNotFound, "Not Found")
			return
		}
		http.Redirect(w, r, "/web/", http.StatusFound)
	})
}

// HandleHealth 健康检查：同时探测 Python AI 服务，如实反映降级状态。
func (s *Server) HandleHealth(w http.ResponseWriter, r *http.Request) {
	aiOK := s.ai.LooksAlive()
	status := "ok"
	if !aiOK {
		status = "degraded"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     status,
		"version":    "3.0.0-go",
		"ai_service": aiOK,
		"time":       nowRFC3339(),
	})
}

// statusRecorder 记录响应状态码，供访问日志使用。
type statusRecorder struct {
	http.ResponseWriter
	status int
	wrote  bool
}

func (s *statusRecorder) WriteHeader(code int) {
	if !s.wrote {
		s.status = code
		s.wrote = true
	}
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusRecorder) Write(b []byte) (int, error) {
	s.wrote = true
	return s.ResponseWriter.Write(b)
}

// Flush 透传，保证 SSE 可用。
func (s *statusRecorder) Flush() {
	if f, ok := s.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}