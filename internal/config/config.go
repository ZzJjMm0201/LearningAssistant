// Package config 负责加载配置：环境变量优先，其次项目根目录 .env，最后内置默认值。
//
// 所有密钥都不写死在代码里。JWT_SECRET 缺失时启动即拒绝——不允许用默认值兜底，
// 否则任何人都能用公开的默认值伪造任意用户的 token。
package config

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Config 是全服务的运行期配置。
type Config struct {
	BaseDir string // 项目根目录（lago/）
	DataDir string // SQLite 数据库目录
	HistDir string // 作答记录与图片
	ReportDir string
	ExportDir string
	AnnotateDir string
	AnimDir  string
	StaticDir string // 网页端静态资源

	// 服务监听
	Listen string // 网关监听地址
	AIBase string // Python AI 服务地址

	// 鉴权
	JWTSecret string
	JWTHours  int

	// 上传限制
	MaxUploadMB int

	// 功能开关
	EnableQuestionSearch bool
	EnableLocalOCR      bool
	EnableAnimation      bool

	// OCR 默认模式 paddle|qwen
	OCRDefaultMode string

	// 传给 Python 侧的密钥（网关不直接调用第三方 AI）
	DeepSeekKey   string
	DeepSeekBase  string
	VisionModel   string
	OCRToken      string
	OCRAPIURL     string
}

var envLoaded bool

// LoadEnvFile 解析项目根目录的 .env，已存在的环境变量优先，不覆盖。
func LoadEnvFile(baseDir string) {
	if envLoaded {
		return
	}
	envLoaded = true
	f, err := os.Open(filepath.Join(baseDir, ".env"))
	if err != nil {
		return
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.Trim(strings.TrimSpace(v), `"'`)
		if _, exists := os.LookupEnv(k); !exists {
			_ = os.Setenv(k, v)
		}
	}
}

func env(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return n
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	switch v {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	}
	return def
}

// Load 组装配置。jwtSecret 为空时返回错误，由调用方决定是否终止启动。
func Load(baseDir string) (*Config, error) {
	LoadEnvFile(baseDir)

	c := &Config{
		BaseDir:   baseDir,
		DataDir:   filepath.Join(baseDir, "data"),
		HistDir:   filepath.Join(baseDir, "history"),
		ReportDir: filepath.Join(baseDir, "reports"),
		ExportDir: filepath.Join(baseDir, "exports"),
		AnnotateDir: filepath.Join(baseDir, "annotations"),
		AnimDir:   filepath.Join(baseDir, "history", "animations"),
		StaticDir: filepath.Join(baseDir, "web"),

		Listen: env("LISTEN_ADDR", "0.0.0.0:8000"),
		AIBase: strings.TrimRight(env("AI_SERVICE_URL", "http://127.0.0.1:8001"), "/"),

		JWTSecret: os.Getenv("JWT_SECRET"),
		JWTHours:  envInt("JWT_EXPIRE_HOURS", 72),

		MaxUploadMB: envInt("MAX_UPLOAD_MB", 32),

		EnableQuestionSearch: envBool("ENABLE_QUESTION_SEARCH", false),
		EnableLocalOCR:       envBool("ENABLE_LOCAL_OCR", true),
		EnableAnimation:      envBool("ENABLE_ANIMATION", true),

		OCRDefaultMode: env("OCR_DEFAULT_MODE", "qwen"),

		DeepSeekKey:  os.Getenv("DEEPSEEK_API_KEY"),
		DeepSeekBase: env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
		VisionModel:  env("DEEPSEEK_DEFAULT_VISION", "deepseek-chat"),
		OCRToken:     os.Getenv("OCR_TOKEN"),
		OCRAPIURL:    env("OCR_API_URL", ""),
	}

	if c.OCRDefaultMode != "paddle" && c.OCRDefaultMode != "qwen" {
		c.OCRDefaultMode = "qwen"
	}
	if c.JWTHours <= 0 {
		c.JWTHours = 72
	}
	if c.JWTSecret == "" {
		return c, fmt.Errorf("JWT_SECRET 未设置：拒绝以空密钥启动（否则任何人都能伪造任意用户的 token）")
	}
	if len(c.JWTSecret) < 32 {
		return c, fmt.Errorf("JWT_SECRET 长度不足 32 字符：拒绝启动")
	}
	return c, nil
}

// EnsureDirs 创建全部运行期目录。
func (c *Config) EnsureDirs() error {
	dirs := []string{
		c.DataDir, c.HistDir, c.ReportDir, c.ExportDir,
		c.AnnotateDir, c.AnimDir,
		filepath.Join(c.HistDir, "keyframes"),
		filepath.Join(c.HistDir, "mastery_records"),
	}
	for _, d := range dirs {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return fmt.Errorf("创建目录 %s 失败: %w", d, err)
		}
	}
	return nil
}