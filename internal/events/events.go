// Package events 实现解题流水线的异步事件总线，为 SSE 端点提供背压安全的流式推送。
//
// 设计要点：
//   - 一次性等待（confirm / select_questions）用 sync.Cond 而非轮询，避免忙等。
//   - 每个订阅者独立缓冲，慢客户端只丢自己的消息，不会阻塞解题线程。
//   - 队列在 complete 后保留一段时间再回收，避免客户端刚连上就扑空。
package events

import (
	"sync"
	"time"
)

// Stage 是 SSE 事件阶段名（与原前端协议保持一致）。
type Stage string

const (
	StageInfo           Stage = "info"
	StageOCRComplete    Stage = "ocr_complete"
	StageQuestionSplit  Stage = "question_split"
	StageQuestionInfo   Stage = "question_info"
	StageThinkingStart  Stage = "thinking_start"
	StageThinkingChunk  Stage = "thinking_chunk"
	StageSolutionSteps  Stage = "solution_steps"
	StageSolution       Stage = "solution"
	StageSolutionChunk  Stage = "solution_chunk"
	StageSolutionRendered Stage = "solution_rendered"
	StageLatexExtras    Stage = "latex_extras_rendered"
	StageMindMap        Stage = "mindmap"
	StageSuggestedQs    Stage = "suggested_questions"
	StageExtension      Stage = "extension"
	StageReport         Stage = "report"
	StageError          Stage = "error"
	StageComplete       Stage = "complete"
)

// Event 是一条推送给客户端的事件。
type Event struct {
	Stage   Stage  `json:"stage"`
	Content any    `json:"content"`
	QI      *int   `json:"qi,omitempty"` // 题目索引（多题模式）
}

type waiter struct {
	mu    sync.Mutex
	cond  *sync.Cond
	done  bool
	value any
}

func newWaiter() *waiter {
	w := &waiter{}
	w.cond = sync.NewCond(&w.mu)
	return w
}

// wait 阻塞直到被 signal 或 ctx 取消。ctx 取消靠后台 goroutine 广播唤醒。
func (w *waiter) wait(timeout time.Duration) bool {
	finished := make(chan struct{})
	go func() {
		t := time.NewTimer(timeout)
		defer t.Stop()
		select {
		case <-t.C:
			w.mu.Lock()
			if !w.done {
				w.done = true
			}
			w.mu.Unlock()
			w.cond.Broadcast()
		case <-finished:
		}
	}()
	w.mu.Lock()
	for !w.done {
		w.cond.Wait()
	}
	d := w.done
	w.mu.Unlock()
	close(finished)
	return d
}

func (w *waiter) signal(v any) {
	w.mu.Lock()
	if w.done {
		w.mu.Unlock()
		return
	}
	w.value = v
	w.done = true
	w.mu.Unlock()
	w.cond.Broadcast()
}

type bus struct {
	mu       sync.RWMutex
	queues   map[string]chan Event
	confirms map[string]*waiter
	selects map[string]*waiter
	selVals map[string][]int
	cancels map[string]chan struct{}
	live    map[string]bool
}

// Bus 是全局事件总线。
type Bus struct {
	b           bus
	queueSize   int
	keepAlive   time.Duration
	confirmWait time.Duration
}

// New 创建事件总线。queueSize 是每个订阅者的缓冲深度。
func New(queueSize int) *Bus {
	if queueSize <= 0 {
		queueSize = 256
	}
	b := &Bus{
		queueSize:   queueSize,
		keepAlive:   10 * time.Minute,
		confirmWait: 5 * time.Minute,
	}
	b.b.queues = make(map[string]chan Event)
	b.b.confirms = make(map[string]*waiter)
	b.b.selects = make(map[string]*waiter)
	b.b.selVals = make(map[string][]int)
	b.b.cancels = make(map[string]chan struct{})
	b.b.live = make(map[string]bool)
	return b
}

// Start 登记一个新的解题任务并预建事件队列。
//
// 队列必须在这里创建，而不是等第一个订阅者：客户端是"先 POST 拿 id、
// 再连 SSE"，而流水线可能在订阅之前就产出了事件。若队列不存在，
// Emit 会静默丢弃全部事件，表现为"刚发起就报无效 request_id"。
func (b *Bus) Start(requestID string) {
	b.b.mu.Lock()
	defer b.b.mu.Unlock()
	b.b.live[requestID] = true
	b.b.queues[requestID] = make(chan Event, b.queueSize)
	b.b.confirms[requestID] = newWaiter()
	b.b.selects[requestID] = newWaiter()
	b.b.cancels[requestID] = make(chan struct{})
}

// Emit 向该任务的所有订阅者广播事件。任务不存在时静默丢弃。
func (b *Bus) Emit(requestID string, e Event) {
	b.b.mu.Lock()
	defer b.b.mu.Unlock()
	if !b.b.live[requestID] {
		return
	}
	q := b.b.queues[requestID]
	if q == nil {
		return
	}
	select {
	case q <- e:
	default:
		// 缓冲满：丢最旧的一条给新事件腾位置，保证最新进度不卡住。
		// 解题流水线的价值在最终结果（complete 事件），不是每一帧增量。
		select {
		case <-q:
		default:
		}
		select {
		case q <- e:
		default:
		}
	}
}

// Subscribe 订阅某任务的事件流。
//
// 任务已结束但队列仍在缓冲期内时同样允许订阅：客户端是"先 POST 拿 id、
// 再连 SSE"，流水线可能在订阅之前就跑完了。此时必须让客户端把缓冲事件读完。
// 队列不关闭、也不因订阅者离开而删除——它由 Finish 在缓冲期结束后统一回收，
// 否则晚到的客户端会连"已产生的事件"都读不到。
func (b *Bus) Subscribe(requestID string) (<-chan Event, func(), bool) {
	b.b.mu.Lock()
	defer b.b.mu.Unlock()
	q := b.b.queues[requestID]
	if q == nil && !b.b.live[requestID] {
		return nil, nil, false
	}
	if q == nil {
		q = make(chan Event, b.queueSize)
		b.b.queues[requestID] = q
	}
	return q, func() {}, true
}

// Done 返回任务的取消通道。
func (b *Bus) Done(requestID string) <-chan struct{} {
	b.b.mu.Lock()
	defer b.b.mu.Unlock()
	if c, ok := b.b.cancels[requestID]; ok {
		return c
	}
	return nil
}

// IsCancelled 判断任务是否已被取消。
func (b *Bus) IsCancelled(requestID string) bool {
	select {
	case <-b.Done(requestID):
		return true
	default:
		return false
	}
}

// Confirm 唤醒等待确认的解题线程。
func (b *Bus) Confirm(requestID string) bool {
	b.b.mu.Lock()
	w := b.b.confirms[requestID]
	b.b.mu.Unlock()
	if w == nil {
		return false
	}
	w.signal(true)
	return true
}

// WaitConfirm 阻塞等待用户确认 OCR 结果，返回是否继续。
func (b *Bus) WaitConfirm(requestID string) bool {
	b.b.mu.Lock()
	w := b.b.confirms[requestID]
	c := b.b.cancels[requestID]
	b.b.mu.Unlock()
	if w == nil {
		return false
	}
	go func() {
		if c != nil {
			<-c
			w.signal(false)
		}
	}()
	return w.wait(b.confirmWait)
}

// SelectQuestions 回传用户选中的题目索引并唤醒流水线。
func (b *Bus) SelectQuestions(requestID string, indices []int) bool {
	b.b.mu.Lock()
	w := b.b.selects[requestID]
	if w != nil {
		b.b.selVals[requestID] = indices
	}
	b.b.mu.Unlock()
	if w == nil {
		return false
	}
	w.signal(indices)
	return true
}

// WaitSelectQuestions 阻塞等待用户选题；超时视为放弃选题（按原样处理全部题）。
func (b *Bus) WaitSelectQuestions(requestID string, all []int) []int {
	b.b.mu.Lock()
	w := b.b.selects[requestID]
	c := b.b.cancels[requestID]
	b.b.mu.Unlock()
	if w == nil {
		return all
	}
	go func() {
		if c != nil {
			<-c
			w.signal(all)
		}
	}()
	w.wait(b.confirmWait)

	b.b.mu.Lock()
	v := b.b.selVals[requestID]
	b.b.mu.Unlock()
	if v == nil {
		return all
	}
	return v
}

// Cancel 取消任务。
func (b *Bus) Cancel(requestID string) {
	b.b.mu.Lock()
	c := b.b.cancels[requestID]
	already := !b.b.live[requestID]
	b.b.mu.Unlock()
	if already {
		return
	}
	if c != nil {
		close(c)
	}
}

// Finish 标记任务结束，并在 keepAlive 后回收其资源。
func (b *Bus) Finish(requestID string) {
	b.b.mu.Lock()
	b.b.live[requestID] = false
	delete(b.b.confirms, requestID)
	delete(b.b.selects, requestID)
	delete(b.b.selVals, requestID)
	delete(b.b.cancels, requestID)
	b.b.mu.Unlock()

	// 队列延迟回收：客户端是"先拿 id 再连 SSE"，可能晚于任务结束才订阅。
	// 缓冲期内允许补读已产生的事件（含 complete），之后才真正回收。
	time.AfterFunc(b.keepAlive, func() {
		b.b.mu.Lock()
		defer b.b.mu.Unlock()
		if !b.b.live[requestID] {
			delete(b.b.queues, requestID)
		}
	})
}

// Alive 报告该任务是否仍在处理中（供 SSE 端点区分"还没开始"与"已过期"）。
func (b *Bus) Alive(requestID string) bool {
	b.b.mu.RLock()
	defer b.b.mu.RUnlock()
	return b.b.live[requestID]
}

// Drained 报告该任务的事件缓冲是否已读空。
func (b *Bus) Drained(requestID string) bool {
	b.b.mu.RLock()
	q := b.b.queues[requestID]
	b.b.mu.RUnlock()
	if q == nil {
		return true
	}
	return len(q) == 0
}