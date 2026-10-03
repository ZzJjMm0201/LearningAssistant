// Command gateway 是学习助手的 Go 网关服务。
//
// 职责：HTTP/SSE 路由、JWT 鉴权、SQLite 存储、并发调度、静态资源。
// AI 相关的重活（OCR / LLM / LaTeX / 图像批注 / 动画）转交 Python 服务。
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/gyyz/lago/internal/ai"
	"github.com/gyyz/lago/internal/auth"
	"github.com/gyyz/lago/internal/config"
	"github.com/gyyz/lago/internal/events"
	"github.com/gyyz/lago/internal/handlers"
	"github.com/gyyz/lago/internal/store"
)

func main() {
	var baseDir string
	var showVersion bool
	flag.StringVar(&baseDir, "dir", defaultBaseDir(), "项目根目录")
	flag.BoolVar(&showVersion, "v", false, "打印版本后退出")
	flag.Parse()

	if showVersion {
		fmt.Println("lago gateway v3.0.0-go")
		return
	}

	abs, err := filepath.Abs(baseDir)
	if err != nil {
		fatal("解析目录失败: %v", err)
	}

	log := newLogger(filepath.Join(abs, "logs"))

	cfg, err := config.Load(abs)
	if err != nil {
		log.Error("配置加载失败", "err", err)
		fmt.Fprintf(os.Stderr, "\n[启动失败] %v\n\n", err)
		fmt.Fprintln(os.Stderr, "请在项目根目录的 .env 中设置 JWT_SECRET（至少 32 字符）。")
		fmt.Fprintln(os.Stderr, "生成方式：openssl rand -hex 32")
		os.Exit(1)
	}
	if err := cfg.EnsureDirs(); err != nil {
		log.Error("创建目录失败", "err", err)
		os.Exit(1)
	}

	db, err := store.Open(cfg.DataDir, "learning_assistant.db")
	if err != nil {
		log.Error("打开数据库失败", "err", err)
		os.Exit(1)
	}
	defer db.Close()

	am := auth.NewManager(cfg.JWTSecret, cfg.JWTHours)
	perm := auth.NewPermission(abs)
	bus := events.New(512)
	aic := ai.New(cfg)

	srv := handlers.New(cfg, db, am, perm, bus, aic, log)

	// 启动时探测 AI 服务：不可用不阻断启动，但要如实告警
	if aic.LooksAlive() {
		log.Info("AI 服务就绪", "addr", cfg.AIBase)
	} else {
		log.Warn("AI 服务未就绪，AI 相关功能将返回错误（不影响登录与历史记录）", "addr", cfg.AIBase)
	}

	httpSrv := &http.Server{
		Addr:    cfg.Listen,
		Handler: srv.Routes(),
		// 不设 WriteTimeout：SSE 是长连接，由各自心跳维持
		ReadHeaderTimeout: 15 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	go func() {
		log.Info("网关已启动", "addr", cfg.Listen, "网页端", fmt.Sprintf("http://127.0.0.1:%s/web/", portOf(cfg.Listen)))
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("HTTP 服务异常退出", "err", err)
			os.Exit(1)
		}
	}()

	<-stop
	log.Info("收到退出信号，正在关闭")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(ctx); err != nil {
		log.Error("关闭超时", "err", err)
	}
	log.Info("已退出")
}

func defaultBaseDir() string {
	// 优先用可执行文件所在目录的上级（lago/bin/gateway → lago/）
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		if filepath.Base(dir) == "bin" {
			return filepath.Dir(dir)
		}
		return dir
	}
	wd, _ := os.Getwd()
	return wd
}

func newLogger(logDir string) *slog.Logger {
	level := slog.LevelInfo
	if v := os.Getenv("LOG_LEVEL"); v == "debug" {
		level = slog.LevelDebug
	}
	var writers []any
	if err := os.MkdirAll(logDir, 0o755); err == nil {
		if f, err := os.OpenFile(filepath.Join(logDir, "gateway.log"),
			os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
			// 5MB × 3 轮转，避免日志无限增长撑爆磁盘
			writers = append(writers, &rotatingWriter{f: f, maxSize: 5 << 20, maxFiles: 3})
		}
	}
	writers = append(writers, os.Stdout)
	h := slog.NewTextHandler(newMultiWriter(writers...), &slog.HandlerOptions{Level: level})
	return slog.New(h)
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func portOf(addr string) string {
	for i := len(addr) - 1; i >= 0; i-- {
		if addr[i] == ':' {
			return addr[i+1:]
		}
	}
	return "8000"
}