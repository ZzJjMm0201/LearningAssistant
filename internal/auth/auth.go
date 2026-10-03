// Package auth 提供 JWT 签发/校验、密码哈希与 AI 使用权限判定。
package auth

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"

	"github.com/gyyz/lago/internal/store"
)

// ErrUnauthorized 未登录或 token 无效。
var ErrUnauthorized = errors.New("未登录或登录已过期")

// Claims 是签发的 JWT 载荷。
type Claims struct {
	UserID   int64  `json:"user_id"`
	Username string `json:"username"`
	IsAdmin  bool   `json:"is_admin"`
	jwt.RegisteredClaims
}

// Manager 管理令牌签发与校验。
type Manager struct {
	secret []byte
	hours  int
}

// NewManager 创建鉴权管理器。
func NewManager(secret string, hours int) *Manager {
	return &Manager{secret: []byte(secret), hours: hours}
}

// Issue 签发令牌。
func (m *Manager) Issue(u *store.User) (string, error) {
	now := time.Now()
	claims := Claims{
		UserID:   u.ID,
		Username: u.Username,
		IsAdmin:  u.IsAdmin,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   strconv.FormatInt(u.ID, 10),
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(time.Duration(m.hours) * time.Hour)),
			NotBefore: jwt.NewNumericDate(now.Add(-time.Minute)),
			Issuer:    "lago",
			ID:        randToken(8),
		},
	}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(m.secret)
}

// Parse 校验令牌并返回载荷。
func (m *Manager) Parse(tokenStr string) (*Claims, error) {
	var claims Claims
	_, err := jwt.ParseWithClaims(tokenStr, &claims, func(t *jwt.Token) (any, error) {
		// 固定算法校验：防止 alg=none 或 RS256→HS256 混淆攻击
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("非预期的签名算法: %v", t.Header["alg"])
		}
		return m.secret, nil
	},
		jwt.WithValidMethods([]string{"HS256"}),
		jwt.WithIssuer("lago"),
	)
	if err != nil {
		return nil, ErrUnauthorized
	}
	return &claims, nil
}

// BearerToken 从 "Bearer xxx" 中提取令牌。
func BearerToken(authorization string) string {
	const p = "bearer "
	if len(authorization) > len(p) && strings.EqualFold(authorization[:len(p)], p) {
		return strings.TrimSpace(authorization[len(p):])
	}
	return ""
}

func randToken(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 36)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

// ---------- 密码 ----------

// HashPassword 生成 bcrypt 哈希。
func HashPassword(pw string) (string, error) {
	h, err := bcrypt.GenerateFromPassword([]byte(pw), bcrypt.DefaultCost)
	return string(h), err
}

// VerifyPassword 校验密码。
func VerifyPassword(pw, hash string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(pw)) == nil
}

// ---------- AI 使用权限 ----------

// Permission 按 permissions.txt 判定某用户能否使用 AI 资源。
//
// 规则：管理员始终放行；文件不存在视为不限制；文件存在时只有显式 =True 放行。
// 缓存按文件 mtime 失效，改完即时生效、无需重启。
type Permission struct {
	path string

	mu     sync.RWMutex
	mtime int64
	cache  map[string]bool
}

// RestrictedMsg 是受限时的提示文案。
const RestrictedMsg = "因AI资源有限，当前账号已暂停AI功能使用权限；如需使用请联系管理员开通。"

// NewPermission 创建权限判定器。
func NewPermission(baseDir string) *Permission {
	return &Permission{path: filepath.Join(baseDir, "permissions.txt")}
}

func (p *Permission) load() (map[string]bool, bool) {
	st, err := os.Stat(p.path)
	if err != nil {
		return nil, false // 文件不存在 → 不限制
	}
	mt := st.ModTime().UnixNano()

	p.mu.RLock()
	if p.cache != nil && p.mtime == mt {
		m := p.cache
		p.mu.RUnlock()
		return m, true
	}
	p.mu.RUnlock()

	m := map[string]bool{}
	data, err := os.ReadFile(p.path)
	if err == nil {
		for _, raw := range strings.Split(string(data), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
				continue
			}
			k, v, ok := strings.Cut(line, "=")
			if !ok {
				continue
			}
			name := strings.TrimSpace(k)
			if name == "" {
				continue
			}
			switch strings.ToLower(strings.TrimSpace(v)) {
			case "true", "1", "yes", "on":
				m[name] = true
			default:
				m[name] = false
			}
		}
	}
	p.mu.Lock()
	p.cache, p.mtime = m, mt
	p.mu.Unlock()
	return m, true
}

// CanUseAI 判断该用户能否使用 AI 资源。
func (p *Permission) CanUseAI(username string, isAdmin bool) bool {
	if isAdmin {
		return true
	}
	m, loaded := p.load()
	if !loaded {
		return true
	}
	return m[username]
}