package main

import (
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

// rotatingWriter 是一个极简的按大小轮转文件写入器。
//
// 不引入 lumberjack 依赖：只需要"超过 maxSize 就改名归档、只保留 maxFiles 份"，
// 三十行足够，且避免为一个日志功能增加外部依赖面。
type rotatingWriter struct {
	mu      sync.Mutex
	f       *os.File
	maxSize int64
	maxFiles int
	size    int64
	path    string
}

func (w *rotatingWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return 0, os.ErrClosed
	}
	if w.size+int64(len(p)) > w.maxSize {
		w.rotate()
	}
	n, err := w.f.Write(p)
	w.size += int64(n)
	return n, err
}

func (w *rotatingWriter) rotate() {
	name := w.f.Name()
	w.f.Close()
	// 清掉最旧的一份
	oldest := fmt.Sprintf("%s.%d", name, w.maxFiles)
	_ = os.Remove(oldest)
	// 依次后移 .3 → .2 → .1
	for i := w.maxFiles - 1; i >= 1; i-- {
		from := fmt.Sprintf("%s.%d", name, i)
		to := fmt.Sprintf("%s.%d", name, i+1)
		if _, err := os.Stat(from); err == nil {
			_ = os.Rename(from, to)
		}
	}
	_ = os.Rename(name, name+".1")

	f, err := os.OpenFile(name, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	w.f, w.size = f, 0
}

// Close 关闭底层文件。
func (w *rotatingWriter) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f != nil {
		return w.f.Close()
	}
	return nil
}

// multiWriter 把多个 writer 组合成一个，任一失败不影响其它。
type multiWriter struct {
	writers []io.Writer
}

func newMultiWriter(w ...any) io.Writer {
	mw := &multiWriter{}
	for _, x := range w {
		if ww, ok := x.(io.Writer); ok {
			mw.writers = append(mw.writers, ww)
		}
	}
	return mw
}

func (m *multiWriter) Write(p []byte) (int, error) {
	n := len(p)
	for _, w := range m.writers {
		if _, err := w.Write(p); err != nil && n != 0 {
			// 任一目的地失败（如日志文件被删）不应影响其它目的地
			continue
		}
	}
	return n, nil
}

var _ = time.Now