// Package ai 是 Go 网关与 Python AI 服务之间的 HTTP 客户端。
//
// 分工：Go 负责路由、鉴权、并发调度、存储与 SSE；Python 只做"需要 Python 生态"的事
// （PaddleOCR、LLM SDK、LaTeX 编译、图像批注、动画）。网关不直接持有第三方 AI 凭据调用模型。
package ai

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gyyz/lago/internal/config"
)

// Client 调用 Python AI 服务。
type Client struct {
	base string
	http *http.Client
	cfg  *config.Config
}

// New 创建客户端。
func New(cfg *config.Config) *Client {
	return &Client{
		base: cfg.AIBase,
		// 长时间流式：解题流水线可能跑几分钟，超时必须宽松
		http: &http.Client{Timeout: 15 * time.Minute},
		cfg:  cfg,
	}
}

// Health 检查 Python 服务是否就绪。
func (c *Client) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("AI 服务返回 %d", resp.StatusCode)
	}
	return nil
}

func (c *Client) post(ctx context.Context, path string, payload any, out any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("调用 AI 服务 %s 失败: %w", path, err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("AI 服务 %s 返回 %d: %s", path, resp.StatusCode, truncate(string(data), 400))
	}
	if out != nil {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("解析 AI 服务 %s 响应失败: %w", path, err)
		}
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// ---------- OCR ----------

// OCRResult 是一次识别的结果。
type OCRResult struct {
	Text   string  `json:"text"`
	Time   float64 `json:"time"`
	Source string  `json:"source"`
}

// OCR 请求识别。
func (c *Client) OCR(ctx context.Context, imagePath, mode, visionModel string) (*OCRResult, error) {
	b, err := os.ReadFile(imagePath)
	if err != nil {
		return nil, fmt.Errorf("读取图片失败: %w", err)
	}
	return c.OCRBase64(ctx, base64.StdEncoding.EncodeToString(b), mode, visionModel)
}

// OCRBase64 请求识别（图片已编码为 base64）。
func (c *Client) OCRBase64(ctx context.Context, b64, mode, visionModel string) (*OCRResult, error) {
	var out OCRResult
	err := c.post(ctx, "/ocr", map[string]any{
		"image_base64": b64,
		"mode":         mode,
		"vision_model": visionModel,
	}, &out)
	return &out, err
}

// OCRWithBoxes 带文字块坐标，供批注定位。
func (c *Client) OCRWithBoxes(ctx context.Context, imagePath string) (string, []TextBlock, int, int, error) {
	b, err := os.ReadFile(imagePath)
	if err != nil {
		return "", nil, 0, 0, fmt.Errorf("读取图片失败: %w", err)
	}
	var out struct {
		Text   string      `json:"text"`
		Blocks []TextBlock `json:"blocks"`
		Width  int         `json:"width"`
		Height int         `json:"height"`
	}
	err = c.post(ctx, "/ocr/boxes", map[string]any{
		"image_base64": base64.StdEncoding.EncodeToString(b),
	}, &out)
	return out.Text, out.Blocks, out.Width, out.Height, err
}

// TextBlock 是一个文字块及其像素坐标。
type TextBlock struct {
	Text string    `json:"text"`
	BBox [4]float64 `json:"bbox"`
}

// ---------- 分题 ----------

// SplitQuestions 请求把整段文本切成多道独立题目。
func (c *Client) SplitQuestions(ctx context.Context, text, ocrSource, engine, model string) ([]string, error) {
	var out struct {
		Questions []string `json:"questions"`
	}
	err := c.post(ctx, "/split", map[string]any{
		"text":        text,
		"ocr_source":  ocrSource,
		"engine":      engine,
		"model":       model,
	}, &out)
	return out.Questions, err
}

// ---------- 解题流 ----------

// SolveEvent 是解题流水线的一条事件（与 SSE 协议同构）。
type SolveEvent struct {
	Stage   string `json:"stage"`
	Content any    `json:"content"`
	QI      *int   `json:"qi,omitempty"`
}

// SolveOptions 是一次解题请求的全部可调参数。
type SolveOptions struct {
	SessionID     string `json:"session_id"`
	UserID        *int64 `json:"user_id"`
	BaseHost      string `json:"base_host"`
	Engine        string `json:"engine"`
	Model         string `json:"model"`
	Style         string `json:"style"`
	Thinking      string `json:"thinking"` // "1"/""/"auto"
	LatexHelper   string `json:"latex_helper"`
	SearchEnabled bool   `json:"search_enabled"`
	Dialect       string `json:"dialect"`
	Grade         string `json:"grade"`
	Personality   string `json:"personality"`
	Subject       string `json:"subject"`
	Detail        string `json:"detail"`
	WeakCount     int    `json:"weak_count"`
	Interactive   bool   `json:"interactive_quiz"`
	VisionModel   string `json:"vision_model"`
	TextInput     string `json:"text_input"`
	ImageBase64   string `json:"image_base64"`
	// VideoPath 仅供网关内部使用（多页拍摄），不传给 Python 侧
	VideoPath string `json:"-"`
}

// SolveStream 发起流式解题，逐条回调事件。
//
// 用 SSE 文本流解析而非 WebSocket：与原前端协议一致，且穿透代理更稳。
func (c *Client) SolveStream(ctx context.Context, imagePath string, opt SolveOptions, onEvent func(SolveEvent) error) error {
	// 文字模式没有图片可读，不该在这里因为"文件不存在"而失败
	if opt.TextInput == "" {
		b, err := os.ReadFile(imagePath)
		if err != nil {
			return fmt.Errorf("读取图片失败: %w", err)
		}
		opt.ImageBase64 = base64.StdEncoding.EncodeToString(b)
	}

	payload := opt

	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/solve/stream", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("调用解题服务失败: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		msg, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("解题服务返回 %d: %s", resp.StatusCode, truncate(string(msg), 400))
	}
	return parseSSE(resp.Body, func(raw []byte) error {
		var ev SolveEvent
		if err := json.Unmarshal(raw, &ev); err != nil {
			return nil // 跳过坏帧，不中断整条流
		}
		return onEvent(ev)
	})
}

func parseSSE(r io.Reader, handle func([]byte) error) error {
	br := bufio.NewReaderSize(r, 64*1024)
	var data []byte
	for {
		line, err := br.ReadBytes('\n')
		if len(line) > 0 {
			trimmed := bytes.TrimRight(line, "\r\n")
			if bytes.HasPrefix(trimmed, []byte("data:")) {
				if data != nil {
					if err := handle(data); err != nil {
						return err
					}
					data = nil
				}
				data = append([]byte{}, bytes.TrimSpace(trimmed[5:])...)
			}
		}
		if err != nil {
			if err == io.EOF && data != nil {
				return handle(data)
			}
			if err == io.EOF {
				return nil
			}
			return err
		}
	}
}

// StreamSSE 把 Python 侧的 SSE 转发给客户端（/ask/stream、/report/ai/stream 等）。
func (c *Client) StreamSSE(ctx context.Context, path string, payload any, onEvent func(SolveEvent) error) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		msg, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("AI 服务 %s 返回 %d: %s", path, resp.StatusCode, truncate(string(msg), 400))
	}
	return parseSSE(resp.Body, func(raw []byte) error {
		var ev SolveEvent
		if err := json.Unmarshal(raw, &ev); err != nil {
			return nil
		}
		return onEvent(ev)
	})
}

// ---------- 追问 ----------

// Ask 是一次非流式追问。
func (c *Client) Ask(ctx context.Context, messages []Msg, question, engine, model, style, dialect, grade string) (string, error) {
	var out struct {
		Answer string `json:"answer"`
	}
	err := c.post(ctx, "/ask", map[string]any{
		"messages": messages,
		"question": question,
		"engine":   engine,
		"model":    model,
		"style":    style,
		"dialect":  dialect,
		"grade":    grade,
	}, &out)
	return out.Answer, err
}

// Msg 是一条对话消息。
type Msg struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ---------- 知识延伸 ----------

// ExtendStream 流式生成知识延伸。
func (c *Client) ExtendStream(ctx context.Context, imagePath, sessionID string, userID *int64, opt SolveOptions, onEvent func(SolveEvent) error) error {
	b, err := os.ReadFile(imagePath)
	if err != nil {
		return err
	}
	payload := map[string]any{
		"session_id":   sessionID,
		"user_id":      userID,
		"image_base64": base64.StdEncoding.EncodeToString(b),
		"engine":       opt.Engine,
		"model":        opt.Model,
		"style":        opt.Style,
		"dialect":      opt.Dialect,
		"grade":        opt.Grade,
		"vision_model": opt.VisionModel,
		"text_input":   opt.TextInput,
		"base_host":    opt.BaseHost,
	}
	return c.StreamSSE(ctx, "/extend/stream", payload, onEvent)
}

// ---------- 批注 ----------

// AnnotationResult 是一次批注的结果。
type AnnotationResult struct {
	ImageURL    string       `json:"image_url"`
	Annotations []Annotation `json:"annotations"`
}

// Annotation 是一条批注。
type Annotation struct {
	Type   string  `json:"type"`
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	X2     *float64 `json:"x2,omitempty"`
	Y2     *float64 `json:"y2,omitempty"`
	Text   string  `json:"text"`
	Color  string  `json:"color"`
	Reason string  `json:"reason"`
}

// Annotate 生成批注图。
func (c *Client) Annotate(ctx context.Context, imagePath, mode, visionModel string) (*AnnotationResult, error) {
	return c.AnnotateTo(ctx, imagePath, mode, visionModel,
		filepath.Join(c.cfg.AnnotateDir, "latest.png"), "/static/annotations")
}

// AnnotateTo 生成批注图到指定路径（并发场景下用 requestID 区分，避免互相覆盖）。
func (c *Client) AnnotateTo(ctx context.Context, imagePath, mode, visionModel, outPath, baseURL string) (*AnnotationResult, error) {
	b, err := os.ReadFile(imagePath)
	if err != nil {
		return nil, fmt.Errorf("读取图片失败: %w", err)
	}
	var out AnnotationResult
	err = c.post(ctx, "/annotate", map[string]any{
		"image_base64": base64.StdEncoding.EncodeToString(b),
		"mode":         mode,
		"vision_model": visionModel,
		"output_path":  outPath,
		"base_url":     baseURL,
	}, &out)
	return &out, err
}

// ---------- 动画 ----------

// AnimationResult 是一次动画生成的结果。
type AnimationResult struct {
	HTMLURL string `json:"html_url"`
}

// Animate 生成交互式动画。
func (c *Client) Animate(ctx context.Context, text, solution, engine, theme string) (*AnimationResult, error) {
	var out AnimationResult
	err := c.post(ctx, "/animation", map[string]any{
		"text":     text,
		"solution": solution,
		"engine":   engine,
		"theme":    theme,
		"output_dir": c.cfg.AnimDir,
		"base_url":   "/static/animations",
	}, &out)
	return &out, err
}

// ---------- 关键帧 ----------

// Keyframe 是一页关键帧。
type Keyframe struct {
	Path      string  `json:"path"`
	Name      string  `json:"name"`
	Index     int     `json:"index"`
	TimeSec   float64 `json:"time_sec"`
	Sharpness float64 `json:"sharpness"`
}

// ExtractKeyframes 从视频抽关键帧。
func (c *Client) ExtractKeyframes(ctx context.Context, videoPath, outDir string) ([]Keyframe, error) {
	b, err := os.ReadFile(videoPath)
	if err != nil {
		return nil, fmt.Errorf("读取视频失败: %w", err)
	}
	var out struct {
		Frames []Keyframe `json:"frames"`
		Error  string     `json:"error"`
	}
	err = c.post(ctx, "/keyframes", map[string]any{
		"video_base64": base64.StdEncoding.EncodeToString(b),
		"output_dir":   outDir,
	}, &out)
	if err != nil {
		return nil, err
	}
	if out.Error != "" {
		return nil, fmt.Errorf("抽帧失败: %s", out.Error)
	}
	return out.Frames, nil
}

// ---------- LaTeX 渲染 ----------

// RenderLatex 渲染 Markdown 中的 LaTeX 块为图片并回填 URL。
func (c *Client) RenderLatex(ctx context.Context, md, outDir, baseURL, engine, model, visionModel string, review bool) (string, error) {
	var out struct {
		Markdown string `json:"markdown"`
	}
	err := c.post(ctx, "/latex/render", map[string]any{
		"markdown":      md,
		"output_dir":    outDir,
		"base_url":      baseURL,
		"engine":        engine,
		"model":         model,
		"vision_model":  visionModel,
		"enable_review": review,
	}, &out)
	return out.Markdown, err
}

// ---------- 学情报告 ----------

// ReportMistakes 汇总易错点。
func (c *Client) ReportMistakes(ctx context.Context, stats map[string]int, limit int) (string, error) {
	var out struct {
		Text string `json:"text"`
	}
	err := c.post(ctx, "/report/mistakes", map[string]any{
		"stats": stats,
		"limit": limit,
	}, &out)
	return out.Text, err
}

// AIReport 生成 AI 版学情报告。
func (c *Client) AIReport(ctx context.Context, stats map[string]any, subject, grade, theme, engine, model string) (string, error) {
	var out struct {
		Report string `json:"report"`
	}
	err := c.post(ctx, "/report/ai", map[string]any{
		"stats":   stats,
		"subject": subject,
		"grade":   grade,
		"theme":   theme,
		"engine":  engine,
		"model":   model,
	}, &out)
	return out.Report, err
}

// PomodoroRecommend 依据题目难度推荐专注时长。
func (c *Client) PomodoroRecommend(ctx context.Context, ocrText, summary, engine string) (map[string]any, error) {
	var out map[string]any
	err := c.post(ctx, "/pomodoro/recommend", map[string]any{
		"ocr_text": ocrText,
		"summary":  summary,
		"engine":   engine,
	}, &out)
	return out, err
}

// ---------- 导出 ----------

// Export 把 Markdown 导出为 Word/PDF，返回生成文件的绝对路径。
func (c *Client) Export(ctx context.Context, title, content, format, host string) (string, error) {
	var out struct {
		Path string `json:"path"`
		Name string `json:"name"`
	}
	err := c.post(ctx, "/export", map[string]any{
		"title":   title,
		"content": content,
		"format":  format,
		"host":    host,
	}, &out)
	return out.Path, err
}

// LooksAlive 探测 AI 服务是否在监听（不打印错误）。
func (c *Client) LooksAlive() bool {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return c.Health(ctx) == nil
}

// TrimBase 去掉 URL 末尾斜杠。
func TrimBase(s string) string { return strings.TrimRight(s, "/") }