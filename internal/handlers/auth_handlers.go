package handlers

import (
	"net/http"
	"strings"
	"time"

	"github.com/gyyz/lago/internal/auth"
)

type authReq struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

// HandleRegister 注册新账号。
//
// 首个注册账号自动成为管理员：单机部署场景下避免出现"没人能管权限"的死锁。
func (s *Server) HandleRegister(w http.ResponseWriter, r *http.Request) {
	var body authReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Username = strings.TrimSpace(body.Username)
	if len(body.Username) < 3 || len(body.Username) > 64 {
		writeErr(w, http.StatusBadRequest, "用户名长度需在 3-64 之间")
		return
	}
	if len(body.Password) < 6 {
		writeErr(w, http.StatusBadRequest, "密码至少 6 位")
		return
	}

	n, err := s.db.CountUsers()
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "查询用户数失败")
		return
	}

	hash, err := auth.HashPassword(body.Password)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "密码加密失败")
		return
	}

	u, err := s.db.CreateUser(body.Username, hash, n == 0)
	if err != nil {
		if strings.Contains(err.Error(), "已存在") {
			writeErr(w, http.StatusConflict, "用户名已存在")
			return
		}
		writeErr(w, http.StatusInternalServerError, "创建用户失败")
		return
	}

	token, err := s.auth.Issue(u)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "签发令牌失败")
		return
	}
	s.log.Info("新用户注册", "username", u.Username, "is_admin", u.IsAdmin)

	writeJSON(w, http.StatusOK, map[string]any{
		"data": map[string]any{
			"token": token,
			"user":  userView(u.ID, u.Username, u.CreatedAt, u.IsAdmin),
		},
	})
}

// HandleLogin 登录。
func (s *Server) HandleLogin(w http.ResponseWriter, r *http.Request) {
	var body authReq
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body.Username = strings.TrimSpace(body.Username)

	u, err := s.db.UserByName(body.Username)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "查询用户失败")
		return
	}
	// 用户不存在与密码错误返回同一提示，避免用户名枚举
	if u == nil || !auth.VerifyPassword(body.Password, u.Password) {
		writeErr(w, http.StatusUnauthorized, "用户名或密码错误")
		return
	}

	token, err := s.auth.Issue(u)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "签发令牌失败")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"data": map[string]any{
			"token": token,
			"user":  userView(u.ID, u.Username, u.CreatedAt, u.IsAdmin),
		},
	})
}

// HandleVerify 校验令牌有效性。
func (s *Server) HandleVerify(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Token string `json:"token"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	claims, err := s.auth.Parse(body.Token)
	if err != nil {
		writeErr(w, http.StatusUnauthorized, "登录已过期，请重新登录")
		return
	}
	u, err := s.db.UserByID(claims.UserID)
	if err != nil || u == nil {
		writeErr(w, http.StatusUnauthorized, "用户不存在")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"user": userView(u.ID, u.Username, u.CreatedAt, u.IsAdmin),
	})
}

func userView(id int64, username string, createdAt time.Time, isAdmin bool) map[string]any {
	return map[string]any{
		"id":         id,
		"username":   username,
		"created_at": createdAt.Format(time.RFC3339),
		"is_admin":   isAdmin,
	}
}