// Package store 是 SQLite 数据访问层。
//
// 与原 Python 版保持表结构兼容，以便直接复用既有 learning_assistant.db。
// 连接池开启 WAL 并限制为单写者：多题并发解题时每个 goroutine 各自写库，
// SQLite 在默认 journal 模式下跌撞会返回 SQLITE_BUSY。
package store

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// Store 持有数据库连接。
type Store struct {
	db *sql.DB
}

// Open 打开（并在必要时创建）数据库。
func Open(dataDir, dbName string) (*Store, error) {
	dsn := fmt.Sprintf("file:%s?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)&_pragma=foreign_keys(1)",
		filepath.ToSlash(filepath.Join(dataDir, dbName)))
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// SQLite 单写者模型：限制连接数，配合 busy_timeout 避免写冲突直接报错
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(time.Hour)

	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("连接数据库失败: %w", err)
	}
	s := &Store{db: db}
	if err := s.migrate(); err != nil {
		return nil, err
	}
	return s, nil
}

// Close 关闭连接。
func (s *Store) Close() error { return s.db.Close() }

// DB 暴露底层连接，供少数需要自定义查询的调用方使用。
func (s *Store) DB() *sql.DB { return s.db }

const schema = `
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMP,
  is_admin      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS submission_records (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id             TEXT NOT NULL,
  timestamp              TIMESTAMP,
  user_id                INTEGER,
  ocr_text               TEXT,
  ocr_time_seconds       REAL,
  question_info          TEXT,
  solution_steps         TEXT,
  full_solution          TEXT,
  mind_map               TEXT,
  suggested_questions    TEXT,
  search_result          TEXT,
  search_time_seconds    REAL,
  ai_process_time_seconds REAL,
  original_image_path    TEXT,
  rendered_svg_dir       TEXT,
  user_rating            INTEGER DEFAULT 0,
  is_correct             INTEGER
);

CREATE TABLE IF NOT EXISTS tracking_records (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id       TEXT NOT NULL,
  timestamp        TIMESTAMP,
  user_id          INTEGER,
  focus_state      TEXT,
  duration_seconds REAL,
  page_number      INTEGER DEFAULT 0,
  pomodoro_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS aux_records (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  timestamp   TIMESTAMP,
  user_id     INTEGER,
  record_type TEXT NOT NULL,
  title       TEXT,
  content     TEXT,
  extra_json  TEXT
);

CREATE TABLE IF NOT EXISTS conversation_history (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT NOT NULL,
  timestamp      TIMESTAMP,
  user_id        INTEGER,
  role           TEXT,
  content        TEXT,
  metadata_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sub_session  ON submission_records(session_id);
CREATE INDEX IF NOT EXISTS idx_sub_user     ON submission_records(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_time     ON submission_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation_history(session_id);
CREATE INDEX IF NOT EXISTS idx_aux_session  ON aux_records(session_id);
CREATE INDEX IF NOT EXISTS idx_aux_user     ON aux_records(user_id);
CREATE INDEX IF NOT EXISTS idx_track_user   ON tracking_records(user_id);
`

func (s *Store) migrate() error {
	if _, err := s.db.Exec(schema); err != nil {
		return fmt.Errorf("初始化表结构失败: %w", err)
	}
	return nil
}

// ---------- 用户 ----------

// User 对应 users 表。
type User struct {
	ID        int64
	Username  string
	Password  string // 仅用于注册写入，查询时为空
	CreatedAt time.Time
	IsAdmin   bool
}

// CreateUser 新建用户，用户名冲突返回错误。
func (s *Store) CreateUser(username, passwordHash string, isAdmin bool) (*User, error) {
	now := time.Now().UTC()
	res, err := s.db.Exec(
		`INSERT INTO users (username, password_hash, created_at, is_admin) VALUES (?,?,?,?)`,
		username, passwordHash, now, boolToInt(isAdmin))
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			return nil, ErrUserExists
		}
		return nil, err
	}
	id, _ := res.LastInsertId()
	return &User{ID: id, Username: username, CreatedAt: now, IsAdmin: isAdmin}, nil
}

// ErrUserExists 用户名已存在。
var ErrUserExists = fmt.Errorf("用户名已存在")

// UserByName 按用户名查询，未找到返回 nil。
func (s *Store) UserByName(username string) (*User, error) {
	var u User
	var admin int
	err := s.db.QueryRow(
		`SELECT id, username, password_hash, created_at, is_admin FROM users WHERE username = ?`, username).
		Scan(&u.ID, &u.Username, &u.Password, &u.CreatedAt, &admin)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	u.IsAdmin = admin != 0
	return &u, nil
}

// UserByID 按主键查询，未找到返回 nil。
func (s *Store) UserByID(id int64) (*User, error) {
	var u User
	var admin int
	err := s.db.QueryRow(
		`SELECT id, username, password_hash, created_at, is_admin FROM users WHERE id = ?`, id).
		Scan(&u.ID, &u.Username, &u.Password, &u.CreatedAt, &admin)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	u.IsAdmin = admin != 0
	return &u, nil
}

// CountUsers 返回用户总数，用于判断是否为首次部署。
func (s *Store) CountUsers() (int, error) {
	var n int
	err := s.db.QueryRow(`SELECT COUNT(*) FROM users`).Scan(&n)
	return n, err
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// ---------- 作答记录 ----------

// Submission 对应 submission_records 表。
type Submission struct {
	ID              int64
	SessionID       string
	Timestamp       time.Time
	UserID          *int64
	OCRText         string
	OCRTime         float64
	QuestionInfo    map[string]any
	SolutionSteps   string
	FullSolution    string
	MindMap         string
	SuggestedQs     []string
	SearchResult    string
	SearchTime      float64
	AITime          float64
	OriginalImgPath string
	RenderedSVGDir  string
	UserRating      int
	IsCorrect       *bool
}

const subCols = `id, session_id, timestamp, user_id, ocr_text, ocr_time_seconds, question_info,
	solution_steps, full_solution, mind_map, suggested_questions, search_result,
	search_time_seconds, ai_process_time_seconds, original_image_path, rendered_svg_dir,
	user_rating, is_correct`

func scanSub(rows interface{ Scan(...any) error }) (*Submission, error) {
	var (
		r       Submission
		uid     sql.NullInt64
		info    sql.NullString
		sug     sql.NullString
		correct sql.NullInt64
	)
	err := rows.Scan(&r.ID, &r.SessionID, &r.Timestamp, &uid, &r.OCRText, &r.OCRTime,
		&info, &r.SolutionSteps, &r.FullSolution, &r.MindMap, &sug, &r.SearchResult,
		&r.SearchTime, &r.AITime, &r.OriginalImgPath, &r.RenderedSVGDir, &r.UserRating, &correct)
	if err != nil {
		return nil, err
	}
	if uid.Valid {
		v := uid.Int64
		r.UserID = &v
	}
	if correct.Valid {
		v := correct.Int64 != 0
		r.IsCorrect = &v
	}
	if info.Valid && info.String != "" {
		_ = json.Unmarshal([]byte(info.String), &r.QuestionInfo)
	}
	if sug.Valid && sug.String != "" {
		_ = json.Unmarshal([]byte(sug.String), &r.SuggestedQs)
	}
	return &r, nil
}

// SaveSubmission 写入一条解题记录，返回主键。
func (s *Store) SaveSubmission(r *Submission) (int64, error) {
	infoJSON := "{}"
	if len(r.QuestionInfo) > 0 {
		if b, err := json.Marshal(r.QuestionInfo); err == nil {
			infoJSON = string(b)
		}
	}
	sugJSON := "[]"
	if len(r.SuggestedQs) > 0 {
		if b, err := json.Marshal(r.SuggestedQs); err == nil {
			sugJSON = string(b)
		}
	}
	ts := r.Timestamp
	if ts.IsZero() {
		ts = time.Now().UTC()
	}
	res, err := s.db.Exec(`INSERT INTO submission_records
		(session_id, timestamp, user_id, ocr_text, ocr_time_seconds, question_info,
		 solution_steps, full_solution, mind_map, suggested_questions, search_result,
		 search_time_seconds, ai_process_time_seconds, original_image_path, rendered_svg_dir,
		 user_rating, is_correct)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		r.SessionID, ts, r.UserID, r.OCRText, r.OCRTime, infoJSON,
		r.SolutionSteps, r.FullSolution, r.MindMap, sugJSON, r.SearchResult,
		r.SearchTime, r.AITime, r.OriginalImgPath, r.RenderedSVGDir, r.UserRating, r.IsCorrect)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// HistoryFilter 是历史记录查询条件。
type HistoryFilter struct {
	UserID     *int64
	StartDate  string // YYYY-MM-DD，空=不限
	EndDate    string
	Subjects   []string
	Grades     []string
	Difficulty []string
	Mastery    []string
	Types      []string // solve/extension/animation
	Limit      int
	Offset     int
}

// History 查询历史记录（联表类型标记：解题记录 + 知识延伸 + AI动画）。
func (s *Store) History(f HistoryFilter) ([]map[string]any, error) {
	var where []string
	var args []any

	if f.UserID != nil {
		where = append(where, "u.user_id = ?")
		args = append(args, *f.UserID)
	}
	if f.StartDate != "" {
		where = append(where, "u.timestamp >= ?")
		args = append(args, f.StartDate+" 00:00:00")
	}
	if f.EndDate != "" {
		where = append(where, "u.timestamp <= ?")
		args = append(args, f.EndDate+" 23:59:59")
	}

	// 学科/年级/难度筛选作用在 question_info JSON 上（SQLite json_extract）
	if len(f.Subjects) > 0 {
		where = append(where, "json_extract(u.question_info,'$.subject') IN ("+placeholders(len(f.Subjects))+")")
		for _, v := range f.Subjects {
			args = append(args, v)
		}
	}
	if len(f.Grades) > 0 {
		where = append(where, "json_extract(u.question_info,'$.grade') IN ("+placeholders(len(f.Grades))+")")
		for _, v := range f.Grades {
			args = append(args, v)
		}
	}
	if len(f.Difficulty) > 0 {
		where = append(where, "json_extract(u.question_info,'$.difficulty') IN ("+placeholders(len(f.Difficulty))+")")
		for _, v := range f.Difficulty {
			args = append(args, v)
		}
	}
	if len(f.Types) > 0 {
		where = append(where, "u.rec_type IN ("+placeholders(len(f.Types))+")")
		for _, v := range f.Types {
			args = append(args, v)
		}
	}

	cond := ""
	if len(where) > 0 {
		cond = " WHERE " + strings.Join(where, " AND ")
	}

	limit := f.Limit
	if limit <= 0 || limit > 500 {
		limit = 200
	}
	offset := f.Offset
	if offset < 0 {
		offset = 0
	}

	// rec_type 统一标记记录来源，供前端按类型筛选。
	// 两个子查询必须逐列对齐（列数与顺序），否则 UNION ALL 会错位、
	// 外层按 user_id 过滤时报 "no such column"。
	sqlText := `
	SELECT u.id, u.rec_type, u.session_id, u.timestamp, u.title, u.content,
	       u.question_info, u.solution_steps, u.full_solution, u.mind_map,
	       u.ocr_text, u.original_image_path, u.suggested_questions,
	       u.user_rating, u.is_correct
	FROM (
	  SELECT id AS id, 'solve' AS rec_type, session_id, timestamp,
	         NULL AS title, NULL AS content, question_info, solution_steps,
	         full_solution, mind_map, ocr_text, original_image_path,
	         suggested_questions, user_rating, is_correct, user_id
	  FROM submission_records
	  UNION ALL
	  SELECT id, record_type, session_id, timestamp, title, content,
	         NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, user_id
	  FROM aux_records
	) u` + cond + ` ORDER BY u.timestamp DESC LIMIT ? OFFSET ?`

	args = append(args, limit, offset)
	rows, err := s.db.Query(sqlText, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]map[string]any, 0, limit)
	for rows.Next() {
		var (
			id                          int64
			recType, sid                string
			ts                          time.Time
			title, content              sql.NullString
			info, steps, full, mind     sql.NullString
			ocr, imgPath, sug           sql.NullString
			rating                      int
			correct                     sql.NullInt64
		)
		if err := rows.Scan(&id, &recType, &sid, &ts, &title, &content, &info, &steps,
			&full, &mind, &ocr, &imgPath, &sug, &rating, &correct); err != nil {
			return nil, err
		}
		m := map[string]any{
			"id":            id,
			"record_type":   recType,
			"session_id":    sid,
			"timestamp":     ts.Format(time.RFC3339),
			"user_rating":   rating,
			"title":         title.String,
			"content":       content.String,
			"question_info": json.RawMessage(orEmptyObj(info.String)),
			"solution_steps": steps.String,
			"full_solution": full.String,
			"mind_map":      mind.String,
			"ocr_text":      ocr.String,
			"original_image_path": imgPath.String,
			"suggested_questions": json.RawMessage(orEmptyArr(sug.String)),
		}
		if correct.Valid {
			m["is_correct"] = correct.Int64 != 0
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

func placeholders(n int) string {
	if n <= 0 {
		return "NULL"
	}
	return strings.TrimSuffix(strings.Repeat("?,", n), ",")
}

func orEmptyObj(s string) string {
	if strings.TrimSpace(s) == "" {
		return "{}"
	}
	return s
}

func orEmptyArr(s string) string {
	if strings.TrimSpace(s) == "" {
		return "[]"
	}
	return s
}

// HistoryStats 汇总历史统计，供学情报告使用。
type HistoryStats struct {
	Total       int
	Correct     int
	Wrong       int
	Subjects    map[string]int
	Grades      map[string]int
	Difficulty  map[string]int
	KnowledgePt map[string]int
	Trend       []TrendPoint
}

// TrendPoint 是趋势图上的一个时间桶。
type TrendPoint struct {
	Date  string `json:"date"`
	Total int    `json:"total"`
	Right int    `json:"right"`
}

// Stats 聚合统计。
func (s *Store) Stats(userID *int64, days int) (*HistoryStats, error) {
	st := &HistoryStats{
		Subjects:    map[string]int{},
		Grades:      map[string]int{},
		Difficulty:  map[string]int{},
		KnowledgePt: map[string]int{},
	}
	from := time.Now().AddDate(0, 0, -days)

	cond := "WHERE timestamp >= ?"
	args := []any{from.UTC().Format("2006-01-02 15:04:05")}
	if userID != nil {
		cond += " AND user_id = ?"
		args = append(args, *userID)
	}

	rows, err := s.db.Query(`SELECT question_info, is_correct, timestamp FROM submission_records `+cond, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	trend := map[string]*TrendPoint{}
	for rows.Next() {
		var info sql.NullString
		var correct sql.NullInt64
		var ts time.Time
		if err := rows.Scan(&info, &correct, &ts); err != nil {
			continue
		}
		st.Total++
		day := ts.Format("2006-01-02")
		if trend[day] == nil {
			trend[day] = &TrendPoint{Date: day}
		}
		trend[day].Total++
		if correct.Valid && correct.Int64 != 0 {
			st.Correct++
			trend[day].Right++
		} else if correct.Valid {
			st.Wrong++
		}

		if info.Valid && info.String != "" {
			var qi struct {
				Subject        string   `json:"subject"`
				Grade          string   `json:"grade"`
				Difficulty     string   `json:"difficulty"`
				KnowledgePoint []string `json:"knowledge_points"`
			}
			if json.Unmarshal([]byte(info.String), &qi) == nil {
				if qi.Subject != "" && qi.Subject != "未知" {
					st.Subjects[qi.Subject]++
				}
				if qi.Grade != "" && qi.Grade != "未知" {
					st.Grades[qi.Grade]++
				}
				if qi.Difficulty != "" && qi.Difficulty != "未知" {
					st.Difficulty[qi.Difficulty]++
				}
				for _, k := range qi.KnowledgePoint {
					if k != "" {
						st.KnowledgePt[k]++
					}
				}
			}
		}
	}

	// 补齐缺失日期，保证趋势图 X 轴连续
	for i := days; i >= 0; i-- {
		d := time.Now().AddDate(0, 0, -i).Format("2006-01-02")
		if trend[d] == nil {
			trend[d] = &TrendPoint{Date: d}
		}
	}
	for d := range trend {
		st.Trend = append(st.Trend, *trend[d])
	}
	// 按日期升序
	for i := 1; i < len(st.Trend); i++ {
		for j := i; j > 0 && st.Trend[j].Date < st.Trend[j-1].Date; j-- {
			st.Trend[j], st.Trend[j-1] = st.Trend[j-1], st.Trend[j]
		}
	}
	return st, rows.Err()
}

// Mistakes 汇总易错知识点（来自最近记录的 easy_mistakes）。
func (s *Store) Mistakes(userID *int64, limit int) (map[string]int, error) {
	cond := "WHERE question_info IS NOT NULL AND question_info != '{}'"
	var args []any
	if userID != nil {
		cond += " AND user_id = ?"
		args = append(args, *userID)
	}
	rows, err := s.db.Query(`SELECT question_info FROM submission_records `+cond+` ORDER BY timestamp DESC LIMIT 200`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := map[string]int{}
	for rows.Next() {
		var info string
		if err := rows.Scan(&info); err != nil {
			continue
		}
		var qi struct {
			EasyMistakes []string `json:"easy_mistakes"`
		}
		if json.Unmarshal([]byte(info), &qi) == nil {
			for _, m := range qi.EasyMistakes {
				if m != "" {
					out[m]++
				}
			}
		}
	}
	return out, nil
}

// ---------- 对话历史 ----------

// Msg 是一条对话消息。
type Msg struct {
	Role    string
	Content string
}

// ConversationBySession 按时间序返回该会话的对话。
func (s *Store) ConversationBySession(sessionID string) ([]Msg, error) {
	rows, err := s.db.Query(
		`SELECT role, content FROM conversation_history WHERE session_id = ? ORDER BY id ASC`, sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Msg
	for rows.Next() {
		var m Msg
		if err := rows.Scan(&m.Role, &m.Content); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// AppendConversation 追加一条对话消息。
func (s *Store) AppendConversation(sessionID string, userID *int64, role, content string) error {
	_, err := s.db.Exec(
		`INSERT INTO conversation_history (session_id, timestamp, user_id, role, content) VALUES (?,?,?,?,?)`,
		sessionID, time.Now().UTC(), userID, role, content)
	return err
}

// ---------- 辅助记录（知识延伸 / AI动画） ----------

// AuxRecord 是知识延伸或动画记录。
type AuxRecord struct {
	SessionID string
	UserID    *int64
	Type      string
	Title     string
	Content   string
	ExtraJSON string
}

// SaveAux 写入一条辅助记录。
func (s *Store) SaveAux(r AuxRecord) (int64, error) {
	res, err := s.db.Exec(
		`INSERT INTO aux_records (session_id, timestamp, user_id, record_type, title, content, extra_json)
		 VALUES (?,?,?,?,?,?,?)`,
		r.SessionID, time.Now().UTC(), r.UserID, r.Type, r.Title, r.Content, r.ExtraJSON)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// AuxBySession 查询某会话的辅助记录。
func (s *Store) AuxBySession(sessionID string) ([]AuxRecord, error) {
	rows, err := s.db.Query(
		`SELECT session_id, user_id, record_type, title, content, extra_json FROM aux_records WHERE session_id = ? ORDER BY id ASC`,
		sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []AuxRecord
	for rows.Next() {
		var r AuxRecord
		var uid sql.NullInt64
		if err := rows.Scan(&r.SessionID, &uid, &r.Type, &r.Title, &r.Content, &r.ExtraJSON); err != nil {
			return nil, err
		}
		if uid.Valid {
			v := uid.Int64
			r.UserID = &v
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ---------- 专注度记录 ----------

// SaveTracking 写入一条专注度记录。
func (s *Store) SaveTracking(sessionID string, userID *int64, focusState string, duration float64, page, pomo int) error {
	_, err := s.db.Exec(
		`INSERT INTO tracking_records (session_id, timestamp, user_id, focus_state, duration_seconds, page_number, pomodoro_count)
		 VALUES (?,?,?,?,?,?,?)`,
		sessionID, time.Now().UTC(), userID, focusState, duration, page, pomo)
	return err
}

// ---------- 删除 ----------

// DeleteSubmissions 按主键删除解题记录（用户隔离）。
func (s *Store) DeleteSubmissions(ids []int64, userID *int64) (int64, error) {
	return s._delete("submission_records", ids, userID)
}

// DeleteAux 按主键删除辅助记录（用户隔离）。
func (s *Store) DeleteAux(ids []int64, userID *int64) (int64, error) {
	return s._delete("aux_records", ids, userID)
}

// _delete 按主键删除，userID 非 nil 时强制用户隔离。
func (s *Store) _delete(table string, ids []int64, userID *int64) (int64, error) {
	if len(ids) == 0 {
		return 0, nil
	}
	// table 只由本文件的固定常量传入，不来自请求
	marks := placeholders(len(ids))
	args := make([]any, 0, len(ids)+1)
	for _, id := range ids {
		args = append(args, id)
	}
	cond := " WHERE id IN (" + marks + ")"
	if userID != nil {
		cond += " AND user_id = ?"
		args = append(args, *userID)
	}
	res, err := s.db.Exec("DELETE FROM "+table+cond, args...)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// SplitIDs 把前端给的 id 拆成解题记录与辅助记录两组。
//
// submission_records 与 aux_records 各自自增，id 会重复（同为1），
// 因此不能只看 rec_type 就假定 id 归属——先按 rec_type 精确删，
// 剩余未命中的 id 再到另一张表试一次。
func (s *Store) SplitIDs(ids []int64, userID *int64) (solveIDs, auxIDs []int64, err error) {
	if len(ids) == 0 {
		return nil, nil, nil
	}
	marks := placeholders(len(ids))
	args := make([]any, 0, len(ids))
	for _, id := range ids {
		args = append(args, id)
	}

	// 先按 rec_type 精确分流
	rows, err := s.db.Query(
		`SELECT id, rec_type FROM (
		   SELECT id, 'solve' AS rec_type FROM submission_records
		   WHERE id IN (`+marks+`)
		   UNION ALL
		   SELECT id, record_type AS rec_type FROM aux_records WHERE id IN (`+marks+`)
		 )`, append(args, args...)...)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()

	seen := map[int64]bool{}
	for rows.Next() {
		var id int64
		var rt string
		if err := rows.Scan(&id, &rt); err != nil {
			continue
		}
		// 同一 id 若同时出现在两张表（自增撞号），两边都要删，否则用户删不干净
		seen[id] = true
		if rt == "solve" {
			solveIDs = append(solveIDs, id)
		} else {
			auxIDs = append(auxIDs, id)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, nil, err
	}

	// 没被分流命中的 id：两张表都试一遍（可能是别人无权删除的记录）
	var missing []int64
	for _, id := range ids {
		if !seen[id] {
			missing = append(missing, id)
		}
	}
	if len(missing) > 0 {
		solveIDs = append(solveIDs, missing...)
		auxIDs = append(auxIDs, missing...)
	}
	return solveIDs, auxIDs, nil
}