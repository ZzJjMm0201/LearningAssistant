/* ==================== 学生端主应用 ==================== */
(() => {
'use strict';

/* ---------- 全局状态 ---------- */
const S = {
  view: 'home',
  user: API.getUser(),
  activeRequestId: '',
  currentSolve: null,
  settings: JSON.parse(localStorage.getItem('la_settings') || '{}'),
  defaults: {
    engine: 'deepseek', llmModel: '',
    ocrMode: 'qwen', visionModel: '',
    style: 'formal', dialect: '普通话', grade: '',
    thinking: 'auto', searchEnabled: true, latexHelper: 'auto',
    personality: 'auto', detail: 'auto', subject: '',
    fontSize: 16, theme: 'dark',
    showModules: { solution_steps: true, full_solution: true, mind_map: true,
                   suggested_questions: true, mistakes: true, extension: true },
    interactiveQuiz: false,   // ⑯ 边解答边设问（默认关闭，需在设置里开）
    pomodoroWork: 25, pomodoroRest: 5, pomodoroMode: 'countdown',
    historyStartDate: '', historyEndDate: ''
  }
};
function settings() { return Object.assign({}, S.defaults, S.settings); }
function saveSettings(k, v) { S.settings[k] = v; localStorage.setItem('la_settings', JSON.stringify(S.settings)); }

/* ---------- DOM 帮助 ---------- */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function toast(msg, ms = 2200) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), ms);
}
function overlay(text) {
  const o = $('#overlay');
  if (text === false) { o.classList.add('hidden'); return; }
  $('#overlayText').textContent = text || '处理中…';
  o.classList.remove('hidden');
}
/* ⑥ 颜色标记 [[#RRGGBB]文字[[#RRGGBB] → <span style="color:#RRGGBB">文字</span>
   服务端会下发 [[#1E88E5]]牛顿第二定律[[#1E88E5]] 这类标记（易错/突破/结论/答案配色），
   安卓端用 Spannable 上色；网页端用 span 包裹，长度不限、不与 Markdown 冲突。 */
const COLOR_MARK_RE = /\[\[#([0-9A-Fa-f]{6})\]\]([\s\S]*?)\[\[#[0-9A-Fa-f]{6}\]\]/g;
function applyColorMarkers(text) {
  if (!text || text.indexOf('[[') < 0) return text;
  return String(text).replace(COLOR_MARK_RE, (m, hex, inner) => {
    // 若内层还有标记则递归处理（支持嵌套）
    return '<span style="color:#' + hex + '">' + inner + '</span>';
  });
}
/* LaTeX 兜底：模型偶尔输出 \\( ... \\) 或未闭合 $，统一成 MathJax 认识的行内定界符 */
function normalizeLatex(text) {
  if (!text) return text;
  let s = String(text);
  s = s.replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$');
  s = s.replace(/\\\[([\s\S]*?)\\\]/g, '$$$$$1$$$$');
  return s;
}
function md(text) {
  if (!text) return '';
  let t = normalizeLatex(applyColorMarkers(String(text)));
  return (window.marked ? marked.parse(t) : esc(t));
}
function renderMarkdownInto(el, text) {
  el.innerHTML = md(text);
  if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise([el]).catch(() => {});
  }
}
function applyFontSize(px) {
  // 根字号驱动 rem 缩放：CSS 中阅读类字号已改用 rem，改根字号即可整体生效
  document.documentElement.style.setProperty('--font-size', px + 'px');
  document.documentElement.style.fontSize = px + 'px';
  document.body.style.fontSize = px + 'px';
}
function applyTheme(theme) {
  document.body.setAttribute('data-theme', theme);
}

/* 10.1 流式输出节流器：chunk 是逐字符到达的，如果每个 chunk 都重建 DOM + 重跑 MathJax，
   页面会卡到连滚动都不跟手。这里统一按毫秒节流，收尾时用 flush() 保证最终内容不丢。 */
function makeThrottle(fn, ms) {
  let last = 0, timer = null, args = null;
  function run() { timer = null; last = Date.now(); fn.apply(null, args || []); }
  const wrapped = function () {
    args = Array.prototype.slice.call(arguments);
    const now = Date.now();
    if (now - last >= ms) { if (timer) { clearTimeout(timer); timer = null; } run(); }
    else if (!timer) { timer = setTimeout(run, Math.max(16, ms - (now - last))); }
  };
  wrapped.flush = function () {
    if (timer) { clearTimeout(timer); timer = null; }
    last = Date.now();
    fn.apply(null, args || []);
  };
  return wrapped;
}

/* 9.1 思维导图 UI 渲染：把 AI 的缩进/树形文本转成嵌套 <ul>，不再丢一坨 <pre> 文本进去 */
function mindmapTree(text) {
  let raw = String(text == null ? '' : text);
  raw = raw.replace(/```[a-zA-Z]*\s*\n?/g, '').replace(/```/g, '');
  const BRANCH = /[\u2500-\u257F]/g;
  const nodes = [];
  raw.split('\n').forEach((line) => {
    if (!line.trim()) return;
    const l = line.replace(/\t/g, '    ').replace(/\u3000/g, '  ');
    const prefix = (l.match(/^[\s\u2500-\u257F]+/) || [''])[0];
    const depth = Math.floor(prefix.replace(BRANCH, '  ').length / 2);
    let body = l.slice(prefix.length)
      .replace(/^[-*+•·]\s*/, '')
      .replace(/^#{1,6}\s*/, '')
      .replace(BRANCH, '')
      .trim();
    if (!body) return;
    let title = body, desc = '';
    const m = body.match(/^(.{1,40}?)\s*[：:]\s*(.+)$/);
    if (m) { title = m[1]; desc = m[2]; }
    nodes.push({ depth: depth, title: title, desc: desc });
  });
  if (!nodes.length) return '';
  const min = Math.min.apply(null, nodes.map((n) => n.depth));
  const roots = [], stack = [];
  nodes.forEach((n) => {
    const node = { title: n.title, desc: n.desc, children: [] };
    const depth = n.depth - min;
    while (stack.length > depth) stack.pop();
    if (!stack.length) roots.push(node); else stack[stack.length - 1].children.push(node);
    stack.push(node);
  });
  const li = (node) => '<li><span class="mm-t">' + esc(node.title) + '</span>'
    + (node.desc ? '<div class="mm-d">' + esc(node.desc) + '</div>' : '')
    + (node.children.length ? '<ul>' + node.children.map(li).join('') + '</ul>' : '')
    + '</li>';
  return '<ul class="mmap">' + roots.map(li).join('') + '</ul>';
}

/* 掌握程度（服务端 /mastery 按 session_id 落盘，会写进学情报告） */
const MASTERY_OPTS = [['completely_mastered', '✅ 完全掌握'], ['partially_mastered', '🤔 部分掌握'], ['not_mastered', '❌ 完全没掌握']];
function masteryBoxHtml(current) {
  const hit = MASTERY_OPTS.filter((x) => x[1] === current)[0];
  const cur = hit ? hit[0] : '';
  return `<div class="card">
      <div class="card-title">🎯 掌握程度</div>
      <div class="mastery-row" data-mastery-row>
        ${MASTERY_OPTS.map(([id, label]) => `<button class="chip ${id === cur ? 'active' : ''}" data-m="${id}">${label}</button>`).join('')}
      </div>
      <div class="muted" style="margin-top:6px">记录后会写入学情报告；“详细度=自动”时也会参考它调整讲解深度。</div>
    </div>`;
}
function bindMastery(scope, sessionId) {
  if (!scope || !sessionId) return;
  scope.querySelectorAll('[data-mastery-row] .chip').forEach((c) => {
    c.onclick = async () => {
      try {
        await API.mastery(sessionId, c.dataset.m);
        scope.querySelectorAll('[data-mastery-row] .chip').forEach((x) => x.classList.toggle('active', x === c));
        toast('掌握程度已记录');
      } catch (e) { toast('记录失败：' + e.message); }
    };
  });
}

/* 追问框（解题页和历史记录详情共用：7.3 历史里的 AI解题/拓展延伸也要能追问） */
function askBoxHtml() {
  return `<div class="card ask-box">
      <div class="card-title">💬 继续追问</div>
      <div id="qaList"></div>
      <textarea id="askInput" placeholder="针对这道题继续提问…"></textarea>
      <button class="btn btn-primary btn-block" id="askSend" style="margin-top:10px">发送</button>
    </div>`;
}
function bindAskBox(sessionId, contextText) {
  const box = $('#qaList');
  const send = $('#askSend');
  if (!box || !send) return;
  send.onclick = async () => {
    const q = ($('#askInput').value || '').trim();
    if (!q) return;
    $('#askInput').value = '';
    box.insertAdjacentHTML('beforeend', `<div class="bubble q">${esc(q)}</div>`);
    const bid = 'ans' + Date.now();
    box.insertAdjacentHTML('beforeend', `<div class="bubble a" id="${bid}"><span class="dim">思考中…</span></div>`);
    box.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'end' });
    const el = document.getElementById(bid);
    try {
      // 知识延伸/动画记录服务端没有对话上下文，用正文兜底（服务端 AskRequest.context）
      const ctx = contextText ? [{ role: 'assistant', content: String(contextText).slice(0, 6000) }] : null;
      const r = await API.ask(sessionId, q, opts(), ctx);
      const txt = r.answer || r.content || (r.status === 'error' ? ('错误：' + r.message) : '（无回答）');
      renderMarkdownInto(el, txt);
    } catch (e) {
      el.innerHTML = `<span style="color:#F44336">追问失败：${esc(e.message)}</span>`;
    }
  };
}

/* 问答列表渲染（相似题/预判问题） */
function renderQAList(list, qIcon, aIcon) {
  let b = '';
  (list || []).forEach((x) => {
    b += `<div class="bubble q"><b>${qIcon} ${esc(x.q)}</b>${x.a ? `<div class="muted" style="margin-top:6px">${aIcon} ${esc(x.a)}</div>` : ''}</div>`;
  });
  return b;
}

/* 8.1/8.2 知识延伸记录以“## 知识点总结 / ## 知识拓展”合并存在一条 content 里，
   历史详情需要拆开成并列模块，而不是塞进一个“内容”卡片 */
function splitExtension(md) {
  const s = String(md == null ? '' : md);
  const pick = (name) => {
    const m = s.match(new RegExp('##\\s*' + name + '\\s*\\n([\\s\\S]*?)(?=\\n##\\s|$)'));
    return m ? m[1].trim() : '';
  };
  const out = { summary: pick('知识点总结'), extension: pick('知识拓展') };
  if (!out.summary && !out.extension && s && s.indexOf('##') < 0) out.summary = s.trim();
  return out;
}

/* ---------- 登录流程 ---------- */
let authMode = 'login';
document.querySelectorAll('.auth-tab').forEach((b) => {
  b.onclick = () => {
    authMode = b.dataset.mode;
    document.querySelectorAll('.auth-tab').forEach((x) => x.classList.toggle('active', x === b));
    $('#authBtn').textContent = authMode === 'login' ? '登录' : '注册';
    $('#authMsg').textContent = '';
  };
});
$('#authBtn').onclick = async () => {
  const u = $('#authUser').value.trim(), p = $('#authPass').value;
  if (!u || !p) { $('#authMsg').textContent = '请输入用户名和密码'; return; }
  $('#authBtn').disabled = true; $('#authMsg').textContent = '';
  try {
    const user = authMode === 'login' ? await API.login(u, p) : await API.register(u, p);
    S.user = user; enterApp();
  } catch (e) {
    $('#authMsg').textContent = e.message || '操作失败';
  } finally { $('#authBtn').disabled = false; }
};
$('#authPass').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#authBtn').click(); });
$('#skipLogin').onclick = (e) => { e.preventDefault(); S.user = null; enterApp(); };

function enterApp() {
  $('#authView').classList.add('hidden');
  $('#app').classList.remove('hidden');
  const st = settings();
  applyTheme(st.theme); applyFontSize(st.fontSize);
  pomoInit();
  render();
}

/* ---------- 导航 ---------- */
document.querySelectorAll('.tab').forEach((b) => {
  b.onclick = () => { S.view = b.dataset.view; render(); };
});
$('#themeBtn').onclick = () => {
  const st = settings();
  const next = st.theme === 'dark' ? 'light' : 'dark';
  saveSettings('theme', next); applyTheme(next);
  toast(next === 'dark' ? '已切换到深色' : '已切换到浅色');
};
const pomoBtnEl = $('#pomoBtn');
if (pomoBtnEl) pomoBtnEl.onclick = () => pomoDialog();
$('#meBtn').onclick = () => {
  const u = S.user;
  const name = u ? (u.username + (u.is_admin ? '（管理员）' : '') + (u.ai_permission ? '' : '｜⚠️无AI权限')) : '未登录';
  if (confirm(`当前账号：${name}\n\n是否退出登录？`)) { API.logout(); location.reload(); }
};

/* ---------- 视图路由 ---------- */
function render() {
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.view === S.view));
  const v = $('#view');
  // 上一个视图可能开了全屏模式（no-pad），回到常规视图时清掉
  v.classList.remove('no-pad');
  v.scrollTop = 0;
  ({ home: renderHome, history: renderHistory, report: renderReport, settings: renderSettings }[S.view] || renderHome)(v);
}

/* ==================== 首页 ==================== */
function renderHome(v) {
  const u = S.user;
  const permWarn = (u && u.ai_permission === false)
    ? `<div class="card" style="border-color:#F44336">
         <div class="card-title" style="color:#F44336">🔒 此账号暂无 AI 使用权限</div>
         <div class="muted">请联系管理员开通后再使用解题功能。</div>
       </div>` : '';
  v.innerHTML = `
    ${permWarn}
    <div class="fn-grid">
      <div class="fn-card" data-fn="solve"><span class="ico">📷</span><span class="nm">拍照解题</span><div class="ds">OCR → AI 分步解析</div></div>
      <div class="fn-card" data-fn="extend"><span class="ico">📎</span><span class="nm">知识延伸</span><div class="ds">总结 · 拓展 · 相似题</div></div>
      <div class="fn-card" data-fn="animate"><span class="ico">🎬</span><span class="nm">AI 动画</span><div class="ds">交互式动态演示</div></div>
      <div class="fn-card" data-fn="annotate"><span class="ico">🖍️</span><span class="nm">AI 批注</span><div class="ds">像老师一样批改</div></div>
      <div class="fn-card" data-fn="report"><span class="ico">📊</span><span class="nm">学情报告</span><div class="ds">数据版 / AI 版</div></div>
    </div>
    <div class="card">
      <div class="card-title">✍️ 直接输入题目（免拍照）</div>
      <textarea id="quickText" placeholder="把题目文字粘贴到这里，例如：一枚炮弹以 20m/s 的速度……"></textarea>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-primary" id="quickSolve">AI 解题</button>
        <button class="btn btn-ghost" id="quickExtend">知识延伸</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title">📖 手势/功能说明</div>
      <div class="muted">📷 拍照解题 ｜ 📎 知识延伸 ｜ 🎬 AI 动画 ｜ 🖍️ AI 批注 ｜ 📊 数据报告 ｜ 🤖 AI 报告</div>
    </div>`;
  v.querySelectorAll('.fn-card').forEach((c) => c.onclick = () => openFn(c.dataset.fn));

  // 无 AI 权限：禁用输入，避免"看起来能用、点了没反应"
  const _noAI = !!(u && u.ai_permission === false);
  const qt = $('#quickText'), qs = $('#quickSolve'), qe = $('#quickExtend');
  if (_noAI) {
    if (qt) { qt.disabled = true; qt.placeholder = '当前账号无 AI 使用权限，请联系管理员开通'; }
    [qs, qe].forEach((b) => { if (b) { b.disabled = true; b.style.opacity = '.5'; b.style.cursor = 'not-allowed'; } });
    v.querySelectorAll('.fn-card').forEach((c) => {
      c.style.opacity = '.5'; c.style.cursor = 'not-allowed';
      c.onclick = () => toast('当前账号无 AI 使用权限');
    });
    return;
  }

  $('#quickSolve').onclick = () => {
    const t = $('#quickText').value.trim();
    if (!t) { toast('请先输入题目'); return; }
    startSolveText(t);
  };
  $('#quickExtend').onclick = () => {
    const t = $('#quickText').value.trim();
    if (!t) { toast('请先输入题目'); return; }
    startExtendText(t);
  };
}

/* ---------- 设备识别（PC / 移动） ---------- */
/** 判断当前是手机/平板（移动端）还是桌面（PC） */
function isMobileDevice() {
  const ua = navigator.userAgent || '';
  // 触摸点数 + UA 双判据，避免桌面浏览器开移动调试时误判
  const touch = (navigator.maxTouchPoints || 0) > 1;
  const mobileUA = /Android|iPhone|iPad|iPod|Mobile|HarmonyOS|Windows Phone/i.test(ua);
  return mobileUA && touch;
}
/** 设备支持实时相机（getUserMedia 需要 https 或 localhost） */
function cameraApiSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) &&
         (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1');
}

/* ---------- 📸 相机界面（网页端拍照） ---------- */
const CAM = { stream: null, facing: 'environment' };

function camStop() {
  if (CAM.stream) {
    CAM.stream.getTracks().forEach((t) => t.stop());
    CAM.stream = null;
  }
}

/**
 * 打开相机取景界面：draw 到 canvas 后转 Blob 交给回调。
 * 移动端优先后置摄像头；PC 用默认摄像头。
 */
async function openCameraInterface(onCapture) {
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="cBk">← 返回</button><div class="ttl">📸 拍照</div></div>
    <div class="card" style="padding:8px">
      <video id="camV" playsinline autoplay muted style="width:100%;border-radius:10px;background:#000"></video>
      <div class="row" style="margin-top:10px;justify-content:center">
        <button class="btn btn-ghost" id="cFlip">🔄 翻转</button>
        <button class="btn btn-primary" id="cShot">📸 拍照</button>
      </div>
      <div class="muted" id="cHint" style="text-align:center;margin-top:6px">对准题目，保持清晰</div>
    </div>`;

  const hint = (msg) => { const h = $('#cHint'); if (h) h.textContent = msg; };

  const start = async () => {
    camStop();
    try {
      CAM.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: CAM.facing, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      const vd = $('#camV');
      if (vd) { vd.srcObject = CAM.stream; await vd.play().catch(() => {}); }
      hint('对准题目，保持清晰');
    } catch (e) {
      hint('无法打开摄像头：' + e.message + '（可改用“选择图片”）');
    }
  };

  $('#cBk').onclick = () => { camStop(); render(); };
  $('#cFlip').onclick = () => { CAM.facing = CAM.facing === 'environment' ? 'user' : 'environment'; start(); };
  $('#cShot').onclick = () => {
    const vd = $('#camV');
    if (!vd || !vd.videoWidth) { hint('相机还没就绪，请稍候'); return; }
    const cv = document.createElement('canvas');
    cv.width = vd.videoWidth; cv.height = vd.videoHeight;
    cv.getContext('2d').drawImage(vd, 0, 0, cv.width, cv.height);
    cv.toBlob((blob) => {
      if (!blob) { hint('拍照失败，请重试'); return; }
      const f = new File([blob], 'camera_' + Date.now() + '.jpg', { type: 'image/jpeg' });
      camStop();
      onCapture(f);
    }, 'image/jpeg', 0.92);
  };
  await start();
}

/* ---------- 📹 长按多页拍摄（连续拍摄 → 服务端抽关键帧） ---------- */
const REC = { stream: null, mr: null, chunks: [], t0: 0, timer: null, maxMs: 60000 };

function recStopTracks() {
  if (REC.stream) {
    REC.stream.getTracks().forEach((t) => t.stop());
    REC.stream = null;
  }
  if (REC.timer) { clearInterval(REC.timer); REC.timer = null; }
}

/**
 * 多页拍摄界面：按住「长按拍摄」按钮期间持续录像，松开结束并上传。
 * 按住过程中逐页翻动题目，服务端按画面变化抽关键帧（每页一帧）。
 */
async function openMultiPageRecorder() {
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="mBk">← 返回</button><div class="ttl">📹 长按多页拍摄</div></div>
    <div class="card" style="padding:8px">
      <video id="mPrev" playsinline autoplay muted style="width:100%;border-radius:10px;background:#000"></video>
      <div class="muted" id="mHint" style="text-align:center;margin-top:8px">
        按住下方按钮开始，逐页翻动题目，松开结束。最多 60 秒。
      </div>
      <button class="btn btn-primary btn-block" id="mRec" style="margin-top:10px;user-select:none;-webkit-user-select:none;touch-action:none">
        ⏺ 长按拍摄
      </button>
      <div id="mStatus" class="muted" style="text-align:center;margin-top:6px"></div>
    </div>`;

  const hint = (s) => { const h = $('#mHint'); if (h) h.textContent = s; };
  const status = (s) => { const h = $('#mStatus'); if (h) h.textContent = s; };

  // 打开摄像头（优先后置）
  try {
    REC.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 960 } },
      audio: false,
    });
    const pv = $('#mPrev');
    if (pv) { pv.srcObject = REC.stream; await pv.play().catch(() => {}); }
  } catch (e) {
    hint('无法打开摄像头：' + e.message);
    return;
  }

  const btn = $('#mRec');
  if (!btn) return;

  const pickMime = () => {
    const cands = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9', 'video/webm'];
    for (const t of cands) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
  };

  const startRec = () => {
    if (REC.mr) return;
    if (!window.MediaRecorder) { hint('当前浏览器不支持录像，请改用「拍照解题」逐张拍'); return; }
    REC.chunks = [];
    const mime = pickMime();
    try {
      REC.mr = mime ? new MediaRecorder(REC.stream, { mimeType: mime }) : new MediaRecorder(REC.stream);
    } catch (e) { hint('录像启动失败：' + e.message); return; }
    REC.mr.ondataavailable = (e) => { if (e.data && e.data.size) REC.chunks.push(e.data); };
    REC.mr.onstop = () => { uploadRecording(); };
    REC.t0 = Date.now();
    REC.mr.start(250);
    btn.textContent = '⏺ 拍摄中…（松开结束）';
    btn.classList.add('recording');
    REC.timer = setInterval(() => {
      const sec = ((Date.now() - REC.t0) / 1000).toFixed(1);
      status('已录 ' + sec + ' 秒');
      if (Date.now() - REC.t0 >= REC.maxMs) stopRec();
    }, 200);
  };

  const stopRec = () => {
    if (!REC.mr) return;
    try { REC.mr.stop(); } catch (e) { /* 已停 */ }
    REC.mr = null;
    if (REC.timer) { clearInterval(REC.timer); REC.timer = null; }
    btn.textContent = '⏺ 长按拍摄';
    btn.classList.remove('recording');
  };

  const uploadRecording = async () => {
    const blob = new Blob(REC.chunks, { type: 'video/webm' });
    if (!blob.size) { status('没有录到内容，请长按久一点'); return; }
    const ext = blob.type.indexOf('mp4') >= 0 ? 'mp4' : 'webm';
    const f = new File([blob], 'multipage_' + Date.now() + '.' + ext, { type: blob.type || 'video/webm' });
    recStopTracks();
    overlay('正在逐页识别（可能需要十几秒）…');
    try {
      const r = await API.solveMultipage(f, opts());
      overlay(false);
      if (!r || (r.status !== 'processing' && r.status !== 'ok')) {
        toast((r && r.message) || '多页识别失败');
        return;
      }
      toast('识别到 ' + (r.pages || '?') + ' 页，开始解题');
      S.activeRequestId = r.request_id;
      S.currentSolve = { request_id: r.request_id, kind: 'solve' };
      S.view = 'solve';
      render();
    } catch (e) {
      overlay(false);
      toast('上传失败：' + e.message);
    }
  };

  // 长按：鼠标与触摸都要支持
  const onDown = (e) => { e.preventDefault(); startRec(); };
  const onUp = (e) => { if (e) e.preventDefault(); stopRec(); };
  btn.addEventListener('mousedown', onDown);
  btn.addEventListener('touchstart', onDown, { passive: false });
  btn.addEventListener('mouseup', onUp);
  btn.addEventListener('mouseleave', onUp);
  btn.addEventListener('touchend', onUp, { passive: false });
  btn.addEventListener('touchcancel', onUp, { passive: false });

  const bk = $('#mBk');
  if (bk) bk.onclick = () => { stopRec(); recStopTracks(); render(); };
}

/* ---------- 上传弹层（拍照/选图/文字） ---------- */
function openFn(fn) {
  const cfg = {
    solve:  { t: '📷 拍照解题', text: 'startSolveText' },
    extend: { t: '📎 知识延伸', text: 'startExtendText' },
    animate:{ t: '🎬 AI 动画', text: 'startAnimateText' },
    annotate:{ t: '🖍️ AI 批注', text: '' }
  }[fn];
  const v = $('#view');
  // AI批注只支持图片（没有“文字批注”这回事），所以不显示文字输入区
  const isAnnotate = (fn === 'annotate');
  // 设备识别：移动端且支持相机 API → 提供“打开相机”；否则引导用相册/文件选择
  const canCam = cameraApiSupported();
  const dropHint = canCam
    ? (isMobileDevice() ? '支持拍照 / 相册' : '支持摄像头 / 本地文件')
    : (isMobileDevice() ? '支持拍照 / 相册' : '本机无法调起摄像头，请选择本地图片');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">${cfg.t}</div></div>
    <div class="drop" id="drop"><span class="big">🖼️</span><div>点击选择图片</div><div class="dim">${dropHint}</div></div>
    ${canCam ? '<button class="btn btn-ghost btn-block" id="camGo" style="margin-top:10px">📸 打开相机拍照</button>' : ''}
    <button class="btn btn-ghost btn-block" id="mpGo" style="margin-top:8px">📹 长按多页拍摄（多题一次拍）</button>
    <input type="file" id="file" accept="image/*" ${isMobileDevice() ? 'capture="environment"' : ''} class="hidden">
    <div id="prevWrap"></div>
    ${isAnnotate ? '<div class="card"><div class="muted">AI 会像老师一样在图上圈错、划线、加荧光、写批注，生成后可直接下载带批注的图片。</div></div>' : `<div class="card">
      <div class="card-title">或直接输入文字</div>
      <textarea id="fnText" placeholder="输入题目文字…"></textarea>
      <button class="btn btn-primary btn-block" id="fnGo" style="margin-top:10px">开始</button>
    </div>`}`;
  $('#bk').onclick = render;
  const fileInput = $('#file');
  // 选中/拍到图片后的统一入口（相机与文件选择共用）
  const useFile = (f) => {
    if (!f) return;
    // 相机界面会重写 #view，#prevWrap 已不存在 → 先退回上传页再填预览
    if (!$('#prevWrap')) openFn(fn);
    const url = URL.createObjectURL(f);
    const wrap = $('#prevWrap');
    if (!wrap) { toast('预览失败，请重试'); return; }
    wrap.innerHTML = `<img class="preview-img" src="${url}">
      <button class="btn btn-primary btn-block" id="sendImg">上传并开始</button>`;
    $('#sendImg').onclick = () => {
      if (fn === 'solve') startSolveImage(f);
      else if (fn === 'extend') startExtendImage(f);
      else if (fn === 'annotate') startAnnotateImage(f);
      else startAnimateImage(f);
    };
  };
  $('#drop').onclick = () => fileInput.click();
  fileInput.onchange = () => { useFile(fileInput.files[0]); };
  // 📸 打开相机（实时取景 → 拍下 → 复用同一“上传并开始”流程）
  const camBtn = $('#camGo');
  if (camBtn) camBtn.onclick = () => openCameraInterface(useFile);
  // 📹 长按多页拍摄：连续录制，服务端抽关键帧分页
  const mpBtn = $('#mpGo');
  if (mpBtn) mpBtn.onclick = () => openMultiPageRecorder();
  const goBtn = $('#fnGo');
  if (goBtn) goBtn.onclick = () => {
    const t = $('#fnText').value.trim();
    if (!t) { toast('请输入文字'); return; }
    if (fn === 'solve') startSolveText(t);
    else if (fn === 'extend') startExtendText(t);
    else startAnimateText(t);
  };
}

/* ==================== 解题流程 ==================== */
function opts() {
  const s = settings();
  return {
    engine: s.engine, llmModel: s.llmModel, ocrMode: s.ocrMode, visionModel: s.visionModel,
    style: s.style, dialect: s.dialect, grade: s.grade, thinking: s.thinking,
    searchEnabled: s.searchEnabled, latexHelper: s.latexHelper,
    personality: s.personality, detail: s.detail, subject: s.subject,
    theme: s.theme,  // 11.1 动画等生成类接口需要知道当前主题
    interactiveQuiz: s.interactiveQuiz  // ⑯ 边解答边设问开关
  };
}

function startSolveImage(file) {
  overlay('正在上传图片…');
  API.solveImage(file, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'processing' && r.status !== 'ok') { toast(r.message || '启动失败'); return; }
    S.activeRequestId = r.request_id; S.currentSolve = null;
    showSolveView(r.request_id);
    streamSolve(r.request_id);
  }).catch((e) => { overlay(false); toast('上传失败：' + e.message); });
}
/* 启动失败：权限类错误给持久卡片提醒（toast 一闪而过会被误认为"点了没反应"） */
function showBlocked(r) {
  const msg = (r && (r.message || r.detail)) || '启动失败';
  const isPerm = (r && r.code === 403) || /权限|暂停|管理员/.test(msg);
  if (!isPerm) { toast(msg); return; }
  const v = $('#view');
  const tip = document.createElement('div');
  tip.className = 'card';
  tip.style.borderColor = '#F44336';
  tip.innerHTML = '<div class="card-title" style="color:#F44336">🔒 无法使用 AI 功能</div>'
    + '<div class="muted">' + esc(msg) + '</div>';
  v.insertBefore(tip, v.firstChild);
  v.scrollTop = 0;
}

function startSolveText(text) {
  overlay('正在启动解题…');
  API.solveText(text, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'processing' && r.status !== 'ok') { showBlocked(r); return; }
    S.activeRequestId = r.request_id; S.currentSolve = null;
    showSolveView(r.request_id);
    streamSolve(r.request_id);
  }).catch((e) => { overlay(false); toast('启动失败：' + e.message); });
}

function showSolveView(rid) {
  S.view = 'solve';
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">📷 解题结果</div></div>
    <div class="progress" id="prog"><span class="dot"></span><span id="progText">正在准备…</span></div>
    <div id="infoTags"></div>
    <div id="solveBody"></div>
    <div id="quizBox"></div>
    <div id="masteryBox"></div>
    <div id="askBox"></div>
    <button class="fab-down hidden" id="fabDown" title="回到底部">↓</button>`;
  $('#bk').onclick = () => { API.cancelSolve(rid).catch(() => {}); S.view = 'home'; render(); };
  bindScrollFab();
}

let solveAcc = { steps: '', full: '', extras: '', mindmap: '', info: {}, similar: [], qa: [], quiz: [], quizIdx: 0, quizAnswered: 0, quizCorrect: 0 };

function streamSolve(rid) {
  solveAcc = { steps: '', full: '', extras: '', mindmap: '', info: {}, similar: [], qa: [], quiz: [], quizIdx: 0, quizAnswered: 0, quizCorrect: 0 };
  API.sse('/solve/stream/' + rid, {
    onEvent: (m) => handleSolveEvent(m),
    onDone: () => {
      renderSolveBodyThrottled.flush();
      const p = $('#prog'); if (p) p.innerHTML = '<span class="dot" style="background:#4CAF50"></span><span>解答完成</span>';
      // ⑯ 边解答边设问：有题就渲染答题卡
      renderQuizBox();
      // 7.4 掌握程度：解答完成后才能评价（历史记录详情里也可以补记）
      const mb = $('#masteryBox');
      if (mb) { mb.innerHTML = masteryBoxHtml(''); bindMastery(mb, rid); }
      renderAskBox(rid);
    },
    onError: () => { const p = $('#prog'); if (p) p.innerHTML = '<span class="dot" style="background:#F44336"></span><span>连接中断</span>'; }
  });
}

function handleSolveEvent(m) {
  const stage = m.stage, content = m.content;
  const progText = $('#progText');
  if (stage === 'info') { if (progText) progText.textContent = content || '处理中…'; return; }
  if (stage === 'blurred') { toast(content || '图片模糊，请重拍'); return; }
  if (stage === 'error') { toast('错误：' + content); if (progText) progText.textContent = '出错：' + content; return; }
  if (stage === 'ocr_complete') {
    const o = (typeof content === 'object' && content) ? content : {};
    solveAcc.ocrText = o.text || '';
    if (progText) progText.textContent = 'OCR 完成，正在分析…';
    return;
  }
  if (stage === 'question_info') {
    solveAcc.info = (typeof content === 'object' && content) ? content : {};
    renderInfoTags(solveAcc.info);
    if (progText) progText.textContent = '正在生成解题思路…';
    return;
  }
  if (stage === 'solution_steps_chunk') { solveAcc.steps = content || ''; renderSolveBodyThrottled(); if (progText) progText.textContent = '阶段 2/6 · 解题思路'; return; }
  if (stage === 'solution_steps') { solveAcc.steps = content || ''; renderSolveBodyThrottled(); return; }
  if (stage === 'solution_chunk') { solveAcc.full = content || ''; renderSolveBodyThrottled(); if (progText) progText.textContent = '阶段 3/6 · 完整解析'; return; }
  if (stage === 'solution' || stage === 'solution_rendered') { solveAcc.full = content || ''; renderSolveBodyThrottled(); if (progText) progText.textContent = '阶段 3/6 · 完整解析'; return; }
  if (stage === 'latex_extras_rendered') { solveAcc.extras = content || ''; renderSolveBodyThrottled(); return; }
  if (stage === 'mindmap_chunk') { solveAcc.mindmap = content || ''; renderSolveBodyThrottled(); if (progText) progText.textContent = '阶段 5/6 · 思维导图'; return; }
  if (stage === 'mindmap') { solveAcc.mindmap = content || ''; renderSolveBodyThrottled(); return; }
  if (stage === 'suggested_questions') {
    solveAcc.similar = parseQA(content);
    renderSolveBodyThrottled();
    if (progText) progText.textContent = '阶段 6/6 · 预判问题';
    return;
  }
  if (stage === 'ai_usage') { solveAcc.usage = content || {}; renderSolveBodyThrottled(); return; }
  // ⑯ 边解答边设问：服务端在 steps 之后下发 quiz 事件
  if (stage === 'quiz') {
    const o = (typeof content === 'object' && content) ? content : {};
    const qs = Array.isArray(o.questions) ? o.questions : [];
    solveAcc.quiz = qs.map((q) => ({
      question: String(q.question || ''),
      options: Array.isArray(q.options) ? q.options.map(String) : [],
      answer_index: Number(q.answer_index || 0),
      explanation: String(q.explanation || ''),
      picked: -1
    })).filter((q) => q.question && q.options.length >= 2);
    solveAcc.quizIdx = 0;
    if (solveAcc.quiz.length) {
      toast('已生成 ' + solveAcc.quiz.length + ' 个思考题，可在下方作答');
      renderQuizBox();
    }
    return;
  }
}

function parseQA(content) {
  if (!Array.isArray(content)) return [];
  return content.map((x) => (typeof x === 'object' && x) ? { q: x.question || '', a: x.answer || '' } : { q: String(x), a: '' })
    .filter((x) => x.q);
}

function renderInfoTags(info) {
  const box = $('#infoTags'); if (!box) return;
  const kp = Array.isArray(info.knowledge_points) ? info.knowledge_points.join('、') : (info.knowledge_points || '');
  const em = Array.isArray(info.easy_mistakes) ? info.easy_mistakes.join('、') : (info.easy_mistakes || '');
  const dp = Array.isArray(info.difficult_points) ? info.difficult_points.join('、') : (info.difficult_points || '');
  const diffColor = { '易': '#4CAF50', '较易': '#4CAF50', '中': '#FF9800', '较难': '#F44336', '难': '#F44336' }[info.difficulty] || '#607D8B';
  let html = '<div class="card"><div class="tags">';
  if (info.grade) html += `<span class="tag" style="background:#7B2FBE">🎓 ${esc(info.grade)}</span>`;
  if (info.subject) html += `<span class="tag" style="background:#2196F3">📖 ${esc(info.subject)}</span>`;
  if (info.difficulty) html += `<span class="tag" style="background:${diffColor}">📊 难度：${esc(info.difficulty)}</span>`;
  html += '</div>';
  if (kp) html += `<div class="muted" style="color:#00D2FF">🔖 知识点：${esc(kp)}</div>`;
  if (em) html += `<div class="muted" style="color:#E65100">⚠️ 易错点：${esc(em)}</div>`;
  if (dp) html += `<div class="muted" style="color:#C62828">🚧 难点：${esc(dp)}</div>`;
  html += '</div>';
  box.innerHTML = html;
}

function foldCard(title, color, bodyHtml, id, collapsed) {
  return `<div class="card">
    <div class="card-title" style="color:${color}">${title}
      <span class="fold" data-fold="${id}">${collapsed ? '▼ 展开' : '▲ 收起'}</span></div>
    <div id="${id}" class="${collapsed ? 'hidden' : ''}">${bodyHtml}</div>
  </div>`;
}

function renderSolveBody() {
  const box = $('#solveBody'); if (!box) return;
  let html = '';
  const sm = Object.assign({}, settings().showModules || {});
  const on = (k) => sm[k] !== false;
  if (solveAcc.steps && on('solution_steps')) html += foldCard('📝 解题思路', '#00D2FF', `<div class="md" data-md></div>`, 'foldSteps', false);
  if (solveAcc.full && on('full_solution')) html += foldCard('📝 完整解析', '#7B2FBE', `<div class="md" data-md></div>`, 'foldFull', false);
  if (solveAcc.extras && on('full_solution')) html += foldCard('📐 图解辅助', '#00BCD4', `<div class="md" data-md></div>`, 'foldExtras', false);
  if (solveAcc.mindmap && on('mind_map')) html += foldCard('🗺️ 思维导图', '#00C853', (mindmapTree(solveAcc.mindmap) || '<div class="muted">思维导图格式无法解析，已按原文显示：</div><pre style="white-space:pre-wrap;font-size:13px">' + esc(solveAcc.mindmap) + '</pre>'), 'foldMind', false);
  if (solveAcc.similar && solveAcc.similar.length && on('suggested_questions')) {
    html += foldCard('💬 预判问题', '#FFB74D', renderQAList(solveAcc.similar, '❓', '💡'), 'foldSim', false);
  }
  if (solveAcc.usage && solveAcc.usage.total_tokens) {
    html += usageFooter(solveAcc.usage);
  }
  if (!html) html = '<div class="card"><div class="muted">正在等待 AI 输出…</div></div>';
  box.innerHTML = html;
  // 填充 Markdown 内容（避免转义问题）
  const map = { foldSteps: solveAcc.steps, foldFull: solveAcc.full, foldExtras: solveAcc.extras };
  Object.keys(map).forEach((id) => {
    const el = box.querySelector('#' + id + ' [data-md]');
    if (el) renderMarkdownInto(el, map[id]);
  });
  box.querySelectorAll('[data-fold]').forEach((s) => {
    s.onclick = () => {
      const t = box.querySelector('#' + s.dataset.fold);
      t.classList.toggle('hidden');
      s.textContent = t.classList.contains('hidden') ? '▼ 展开' : '▲ 收起';
    };
  });
}

/* 10.1 解题流式渲染节流：每约 180ms 重绘一次，收尾 flush，兼顾“看得见进度”与“不卡” */
const renderSolveBodyThrottled = makeThrottle(() => renderSolveBody(), 180);

function renderAskBox(rid) {
  const box = $('#askBox'); if (!box) return;
  box.innerHTML = askBoxHtml();
  bindAskBox(rid, solveAcc.full || solveAcc.steps || '');
}

/* ⑯ 边解答边设问：一次展示一题，答完即时判正误 + 简短解析，再进下一题 */
function renderQuizBox() {
  const box = $('#quizBox'); if (!box) return;
  const qs = solveAcc.quiz || [];
  if (!qs.length) { box.innerHTML = ''; return; }
  const i = Math.min(solveAcc.quizIdx, qs.length - 1);
  const q = qs[i];
  const done = q.picked >= 0;
  const opts = q.options.map((o, k) => {
    let cls = 'chip';
    if (done) {
      if (k === q.answer_index) cls += ' active';
      else if (k === q.picked) cls += ' wrong';
    }
    return `<button class="${cls}" data-opt="${k}" ${done ? 'disabled' : ''}>${String.fromCharCode(65 + k)}. ${esc(o)}</button>`;
  }).join('');
  const correct = qs.filter((x) => x.picked === x.answer_index).length;
  const answered = qs.filter((x) => x.picked >= 0).length;
  box.innerHTML = `<div class="card" style="border-color:var(--accent)">
      <div class="row-between"><div class="card-title" style="margin:0">✋ 边学边问</div>
        <span class="dim">${i + 1}/${qs.length} · 已答对 ${correct}</span></div>
      <div class="muted" style="margin:8px 0 10px">${esc(q.question)}</div>
      <div class="chips" id="quizOpts">${opts}</div>
      ${done ? `<div class="bubble ${q.picked === q.answer_index ? 'a' : 'q'}" style="margin-top:10px">
          <b>${q.picked === q.answer_index ? '✅ 答对了！' : '❌ 再想想～ 正确答案是 ' + String.fromCharCode(65 + q.answer_index) + '. ' + esc(q.options[q.answer_index])}</b>
          ${q.explanation ? `<div class="muted" style="margin-top:6px">💡 ${esc(q.explanation)}</div>` : ''}
        </div>` : ''}
      <div class="row" style="margin-top:10px">
        ${(done && i < qs.length - 1) ? '<button class="btn btn-primary" id="quizNext">下一个思考题 →</button>' : ''}
        ${(answered === qs.length) ? `<button class="btn btn-ghost" id="quizRestart">重新作答</button>` : ''}
        <span class="spacer"></span>
        <span class="dim">${answered}/${qs.length} 已作答</span>
      </div>
    </div>`;
  box.querySelectorAll('#quizOpts .chip').forEach((b) => {
    b.onclick = () => {
      if (q.picked >= 0) return;
      q.picked = Number(b.dataset.opt);
      renderQuizBox();
    };
  });
  const nx = $('#quizNext');
  if (nx) nx.onclick = () => { solveAcc.quizIdx = Math.min(solveAcc.quizIdx + 1, qs.length - 1); renderQuizBox(); };
  const rs = $('#quizRestart');
  if (rs) rs.onclick = () => {
    solveAcc.quiz.forEach((x) => { x.picked = -1; });
    solveAcc.quizIdx = 0;
    renderQuizBox();
  };
}

/* ==================== 知识延伸 ==================== */
function startExtendImage(file) {
  overlay('正在上传图片…');
  API.extendImage(file, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'processing' && r.status !== 'ok') { toast(r.message || '启动失败'); return; }
    showExtendView(r.request_id); streamExtend(r.request_id);
  }).catch((e) => { overlay(false); toast('上传失败：' + e.message); });
}
function startExtendText(text) {
  overlay('正在启动…');
  API.extendText(text, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'processing' && r.status !== 'ok') { showBlocked(r); return; }
    showExtendView(r.request_id); streamExtend(r.request_id);
  }).catch((e) => { overlay(false); toast('启动失败：' + e.message); });
}
function showExtendView(rid) {
  S.view = 'solve';
  $('#view').innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">📎 知识延伸</div></div>
    <div class="progress"><span class="dot"></span><span id="progText">正在分析内容…</span></div>
    <div id="extImg"></div>
    <div id="extBody"></div>
    <div id="askBox"></div>
    <button class="fab-down hidden" id="fabDown" title="回到底部">↓</button>`;
  $('#bk').onclick = () => { S.view = 'home'; render(); };
  bindScrollFab();
}
function streamExtend(rid) {
  let acc = { summary: '', extension: '', similar: [], questions: [], image: '' };
  const paintExtend = makeThrottle(() => renderExtend(acc), 180);
  API.sse('/extend/stream/' + rid, {
    onEvent: (m) => {
      const st = m.stage, c = m.content, pt = $('#progText');
      if (st === 'info') { if (pt) pt.textContent = c || '处理中…'; }
      else if (st === 'ocr_complete') {
        const o = (typeof c === 'object' && c) ? c : {};
        acc.image = o.image_url || '';
        if (acc.image) $('#extImg').innerHTML = `<div class="card"><div class="card-title" style="color:#00D2FF">📷 原题</div><img class="preview-img" src="${acc.image}"></div>`;
      }
      else if (st === 'summary_chunk') { acc.summary = c || ''; paintExtend(); }
      else if (st === 'summary') { acc.summary = c || ''; paintExtend(); if (pt) pt.textContent = '知识点已总结'; }
      else if (st === 'similar_questions') { acc.similar = parseQA(c); paintExtend(); }
      else if (st === 'extension_chunk') { acc.extension = c || ''; paintExtend(); if (pt) pt.textContent = '正在生成知识拓展…'; }
      else if (st === 'extension') { acc.extension = c || ''; paintExtend(); if (pt) pt.textContent = '知识拓展已生成'; }
      else if (st === 'suggested_questions') { acc.questions = parseQA(c); paintExtend(); }
      else if (st === 'error') { toast('错误：' + c); if (pt) pt.textContent = '出错：' + c; }
    },
    onDone: () => {
      paintExtend.flush();
      const p = $('.progress'); if (p) p.innerHTML = '<span class="dot" style="background:#4CAF50"></span><span>延伸完成</span>';
      // 7.3 知识延伸同样要有追问框（服务端无对话上下文，用延伸正文兜底）
      const box = $('#askBox');
      if (box) { box.innerHTML = askBoxHtml(); bindAskBox(rid, [acc.summary, acc.extension].filter(Boolean).join('\n\n')); }
    },
    onError: () => {}
  });
}
function renderExtend(acc) {
  const box = $('#extBody'); if (!box) return;
  let html = '';
  const smx = Object.assign({}, settings().showModules || {});
  const onx = (k) => smx[k] !== false;
  if (acc.summary && onx('extension')) html += foldCard('📝 知识点总结', '#00D2FF', '<div class="md" data-md></div>', 'exSum', false);
  if (acc.extension && onx('extension')) html += foldCard('🚀 知识拓展', '#7B2FBE', '<div class="md" data-md></div>', 'exExt', false);
  if (acc.similar.length) {
    let b = ''; acc.similar.forEach((x) => { b += `<div class="bubble q"><b>🔗 ${esc(x.q)}</b>${x.a ? `<div class="muted" style="margin-top:6px">📝 ${esc(x.a)}</div>` : ''}</div>`; });
    html += foldCard('🔗 相似题推荐', '#2196F3', b, 'exSim', false);
  }
  if (acc.questions.length) {
    let b = ''; acc.questions.forEach((x) => { b += `<div class="bubble q"><b>❓ ${esc(x.q)}</b>${x.a ? `<div class="muted" style="margin-top:6px">💡 ${esc(x.a)}</div>` : ''}</div>`; });
    html += foldCard('💬 延伸思考', '#FFB74D', b, 'exQ', false);
  }
  if (!html) html = '<div class="card"><div class="muted">正在等待 AI 输出…</div></div>';
  box.innerHTML = html;
  const m = { exSum: acc.summary, exExt: acc.extension };
  Object.keys(m).forEach((id) => {
    const el = box.querySelector('#' + id + ' [data-md]');
    if (el) renderMarkdownInto(el, m[id]);
  });
  box.querySelectorAll('[data-fold]').forEach((s) => {
    s.onclick = () => { const t = box.querySelector('#' + s.dataset.fold); t.classList.toggle('hidden'); s.textContent = t.classList.contains('hidden') ? '▼ 展开' : '▲ 收起'; };
  });
}

/* ==================== AI 动画 ==================== */
function startAnimateImage(file) {
  overlay('正在生成动画（可能需要 1-2 分钟）…');
  API.animateImage(file, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'ok') { toast(r.message || '生成失败'); return; }
    showAnimation(r.url, r.usage);
  }).catch((e) => { overlay(false); toast('生成失败：' + e.message); });
}
function startAnimateText(text) {
  overlay('正在生成动画（可能需要 1-2 分钟）…');
  API.animateText(text, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'ok') { toast(r.message || '生成失败'); return; }
    showAnimation(r.url, r.usage);
  }).catch((e) => { overlay(false); toast('生成失败：' + e.message); });
}
/* ⑫ tokens：统一的用量页脚（各模块均显示在最底部） */
function usageFooter(u) {
  if (!u || !u.total_tokens) return '';
  const engine = u.engine || 'DeepSeek';
  const model = u.model || '';
  return `<div class="card" style="margin-top:10px"><div class="dim">⚙️ AI引擎：${esc(engine)} ${esc(model)}<br>
    Tokens：输入 ${esc(u.prompt_tokens || 0)} · 输出 ${esc(u.completion_tokens || 0)} · 总计 ${esc(u.total_tokens || 0)}<br>
    <span style="color:#FB8C00">⚠️ 内容由 AI 生成，请仔细甄别</span></div></div>`;
}

function showAnimation(url, usage) {
  S.view = 'solve';
  // 11.1 触发时告诉动画页当前主题（服务端生成的 HTML 会按 ?theme= 运行时切配色）
  const withTheme = url + (url.indexOf('?') < 0 ? '?' : '&') + 'theme=' + encodeURIComponent(settings().theme);
  const v = $('#view');
  // 全屏自适应：去掉 view 的 padding，用 flex 让 iframe 自己填满剩余高度
  v.classList.add('no-pad');
  v.innerHTML = `
    <div class="fill-page">
      <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">🎬 AI 动画</div></div>
      <iframe src="${esc(withTheme)}" allowfullscreen></iframe>
      ${usageFooter(usage)}
    </div>`;
  $('#bk').onclick = () => { v.classList.remove('no-pad'); S.view = 'home'; render(); };
}

/* ==================== ⑰ AI 批注 ==================== */
const ANNO_LABEL = { circle: '⭕ 画圈', line: '📏 划线', highlight: '🖍️ 荧光', text: '📝 批注' };
function startAnnotateImage(file) {
  overlay('正在生成批注（可能需要 20~60 秒）…');
  API.annotateImage(file, opts()).then((r) => {
    overlay(false);
    if (!r || r.status !== 'ok') { toast((r && r.message) || '批注生成失败'); return; }
    showAnnotation(r);
  }).catch((e) => { overlay(false); toast('批注失败：' + e.message); });
}
function showAnnotation(r) {
  S.view = 'solve';
  const v = $('#view');
  const list = Array.isArray(r.annotations) ? r.annotations : [];
  const chan = r.channel === 'vision' ? '视觉模型直出（带像素刻度尺）' : 'PaddleOCR 坐标 + 大模型定位';
  const items = list.map((a, i) => {
    const t = ANNO_LABEL[a.type] || a.type;
    const pos = (a.x2 != null && a.y2 != null)
      ? `(${Math.round(a.x)}, ${Math.round(a.y)}) → (${Math.round(a.x2)}, ${Math.round(a.y2)})`
      : `(${Math.round(a.x)}, ${Math.round(a.y)})`;
    return `<div class="list-item"><div class="t1">${i + 1}. ${t} <span class="dim">${esc(pos)}</span></div>`
      + (a.text ? `<div class="muted">💬 ${esc(a.text)}</div>` : '')
      + (a.reason ? `<div class="dim">${esc(a.reason)}</div>` : '') + `</div>`;
  }).join('');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">🖍️ AI 批注</div></div>
    <div class="card">
      <div class="row-between"><div class="card-title" style="margin:0">🖍️ 批注结果</div>
        <button class="btn btn-ghost" id="dlAnno">⬇️ 下载图片</button></div>
      <img class="preview-img" src="${esc(r.url)}" style="margin-top:10px" alt="批注结果">
      <div class="dim">生成通道：${esc(chan)} · 共 ${list.length} 条批注</div>
    </div>
    <div class="card"><div class="card-title">📋 批注明细</div>${items || '<div class="muted">无</div>'}</div>
    ${usageFooter(r.usage)}`;
  $('#bk').onclick = () => { S.view = 'home'; render(); };
  $('#dlAnno').onclick = () => {
    const a = document.createElement('a');
    a.href = r.url; a.download = (r.url || '').split('/').pop() || 'annotation.png';
    document.body.appendChild(a); a.click(); a.remove();
  };
}

/* ==================== 历史 ==================== */
/* 历史记录：类型筛选状态保存在模块级，切换筛选不用重新请求 */
const HIST = { all: [], type: 'all' };
const HIST_TYPES = [['all', '全部'], ['solve', '📷 AI解题'], ['animation', '🎬 AI动画'], ['extension', '📎 拓展延伸'], ['annotation', '🖍️ AI批注']];
function histTypeLabel(t) {
  return t === 'animation' ? '🎬 AI动画'
    : (t === 'extension' ? '📎 知识延伸'
    : (t === 'annotation' ? '🖍️ AI批注' : '📷 解题'));
}

async function renderHistory(v) {
  v.innerHTML = `<div class="card"><div class="muted">正在加载历史记录…</div></div>`;
  try {
    // 7.2 日期范围取自“设置 → 历史与数据”
    const st = settings();
    const r = await API.history({ start_date: st.historyStartDate || '', end_date: st.historyEndDate || '' });
    HIST.all = r.records || [];
    paintHistory(v);
  } catch (e) {
    v.innerHTML = `<div class="card"><div class="muted" style="color:#F44336">加载失败：${esc(e.message)}</div></div>`;
  }
}

function paintHistory(v) {
  const st = settings();
  const list = HIST.all.filter((rec) => HIST.type === 'all' || rec.record_type === HIST.type);
  const dateTip = (st.historyStartDate || st.historyEndDate)
    ? ` · ${st.historyStartDate || '不限'} ~ ${st.historyEndDate || '不限'}` : '';
  let html = `<div class="card">
      <div class="row-between"><div class="card-title" style="margin:0">📚 历史记录</div>
      <span class="dim">${list.length} / ${HIST.all.length} 条${esc(dateTip)}</span></div>
      <div class="filter-bar" id="histFilter">
        ${HIST_TYPES.map(([id, label]) => `<button class="chip ${HIST.type === id ? 'active' : ''}" data-t="${id}">${label}</button>`).join('')}
      </div>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-ghost" id="histExport">📤 导出数据</button>
        <button class="btn btn-ghost" id="histRange">📅 日期筛选</button>
      </div>
    </div>`;
  if (!list.length) {
    html += `<div class="card"><div class="muted">没有符合条件的记录。先用首页拍一道题，或在“设置 → 历史与数据”里调整日期范围。</div></div>`;
    v.innerHTML = html;
  } else {
    list.forEach((rec, i) => {
      const t = (rec.timestamp || '').replace('T', ' ').slice(0, 16);
      html += `<div class="list-item" data-idx="${i}">
        <div class="t1">${histTypeLabel(rec.record_type)}${rec.subject ? ' · ' + esc(rec.subject) : ''}${(rec.record_type !== 'solve' && rec.title) ? ' · ' + esc(rec.title) : ''}</div>
        <div class="t2"><span>${esc(t)}</span>${rec.difficulty ? `<span>难度 ${esc(rec.difficulty)}</span>` : ''}${rec.mastery_level ? `<span>🎯 ${esc(rec.mastery_level)}</span>` : ''}</div>
      </div>`;
    });
    v.innerHTML = html;
    v.querySelectorAll('.list-item').forEach((el) => {
      el.onclick = () => renderHistoryDetail(list[+el.dataset.idx]);
    });
  }
  v.querySelectorAll('#histFilter .chip').forEach((c) => {
    c.onclick = () => { HIST.type = c.dataset.t; paintHistory(v); };
  });
  const be = v.querySelector('#histExport');
  if (be) be.onclick = () => exportHistory(list);
  const br = v.querySelector('#histRange');
  if (br) br.onclick = () => { S.view = 'settings'; render(); toast('在“历史与数据”里设置起止日期后返回历史页'); };
}

/* 7.1 导出数据：历史页直接导出（原来按钮藏在设置里且点了只是跳页，历史页看不到按钮） */
async function exportHistory(list) {
  const items = (list || []).filter((r) => r.ocr_text || r.solution_steps || r.full_solution);
  if (!items.length) { toast('没有可导出的内容'); return; }
  overlay('正在导出（可能需要十几秒）…');
  try {
    let md = '# 学习助手 · 历史记录导出\n';
    items.slice(0, 100).forEach((r) => {
      const t = (r.timestamp || '').replace('T', ' ').slice(0, 16);
      md += `\n\n---\n\n## ${histTypeLabel(r.record_type)} ${t}${r.subject ? ' · ' + r.subject : ''}\n\n`;
      if (r.ocr_text) md += `**题目**\n\n${r.ocr_text}\n\n`;
      if (r.solution_steps) md += `### 解题思路\n\n${r.solution_steps}\n\n`;
      if (r.full_solution) md += `### ${r.record_type === 'solve' ? '完整解析' : (r.record_type === 'animation' ? '动画地址' : '内容')}\n\n${r.full_solution}\n\n`;
      if (r.latex_extras) md += `### 图解辅助\n\n${r.latex_extras}\n\n`;
      if (r.mastery_level) md += `**掌握程度**：${r.mastery_level}\n\n`;
    });
    const r = await API.exportHistory('学习助手·历史记录导出', md, 'word');
    overlay(false);
    if (!r || r.status !== 'ok') { toast((r && r.message) || '导出失败'); return; }
    const a = document.createElement('a');
    a.href = r.url; a.download = r.filename || 'history.docx';
    document.body.appendChild(a); a.click(); a.remove();
    toast('导出完成，已开始下载');
  } catch (e) { overlay(false); toast('导出失败：' + e.message); }
}

function renderHistoryDetail(rec) {
  S.view = 'solve';
  const t = (rec.timestamp || '').replace('T', ' ').slice(0, 16);
  const st = settings();
  const isAnim = rec.record_type === 'animation';
  const isExt = rec.record_type === 'extension';
  const extra = (rec.extra_json && typeof rec.extra_json === 'object') ? rec.extra_json : {};
  const parts = isExt ? splitExtension(rec.full_solution || '') : { summary: '', extension: '' };
  let body = '';
  if (rec.image_url) body += `<div class="card"><div class="card-title" style="color:#00D2FF">📷 原题</div><img class="preview-img" src="${rec.image_url}"></div>`;
  if (rec.ocr_text) body += foldCard('🔍 识别文本', '#8A94A0', `<div class="md" data-md></div>`, 'hOcr', true);

  if (isAnim) {
    // 7.6 动画历史要直接播放动画，而不是把 /static/animations/figure.html 当文本显示
    const url = String(rec.full_solution || '').trim();
    body += `<div class="card"><div class="card-title" style="color:#00C853">🎬 动画内容</div>`
      + (url
          ? `<div class="fill-page" style="height:70vh"><iframe src="${esc(url)}${url.indexOf('?') < 0 ? '?' : '&'}theme=${esc(st.theme)}"></iframe></div>`
          : `<div class="muted">这条记录没有可播放的动画地址</div>`)
      + `</div>`;
    if (url) body += `<div class="card"><div class="dim">动画地址：${esc(url)}</div></div>`;
  } else if (isExt) {
    // 8.1/8.2 知识点总结 / 知识拓展 / 相似题推荐 三者并列，不再塞进一个“内容”卡片
    if (parts.summary) body += foldCard('📝 知识点总结', '#00D2FF', `<div class="md" data-md></div>`, 'hSum', false);
    if (parts.extension) body += foldCard('🚀 知识拓展', '#7B2FBE', `<div class="md" data-md></div>`, 'hExt', false);
    const sim = Array.isArray(extra.similar) ? extra.similar : [];
    if (sim.length) body += foldCard('🔗 相似题推荐', '#2196F3', renderQAList(sim, '🔗', '📝'), 'hSim', false);
    const qs = Array.isArray(extra.questions) ? extra.questions : [];
    if (qs.length) body += foldCard('💬 延伸思考', '#FFB74D', renderQAList(qs, '❓', '💡'), 'hQ', false);
    if (!parts.summary && !parts.extension && rec.full_solution) body += foldCard('📎 内容', '#7B2FBE', `<div class="md" data-md></div>`, 'hFull', false);
  } else {
    if (rec.solution_steps) body += foldCard('📝 解题思路', '#00D2FF', `<div class="md" data-md></div>`, 'hSteps', true);
    if (rec.full_solution) body += foldCard('📝 完整解析', '#7B2FBE', `<div class="md" data-md></div>`, 'hFull', false);
    // 7.5 图解辅助（LaTeX 图形）作为独立模块，历史详情也要有
    if (rec.latex_extras) body += foldCard('📐 图解辅助', '#00BCD4', `<div class="md" data-md></div>`, 'hExtra', false);
    // 9.1 思维导图用 UI 树形渲染
    if (rec.mind_map) body += foldCard('🗺️ 思维导图', '#00C853', (mindmapTree(rec.mind_map) || `<div class="muted">思维导图格式无法解析，已按原文显示：</div><pre style="white-space:pre-wrap;font-size:13px">${esc(rec.mind_map)}</pre>`), 'hMind', false);
    // 7.4 掌握程度：历史里也要能看到并补记
    body += masteryBoxHtml(rec.mastery_level || '');
  }

  const askContext = isExt
    ? [parts.summary, parts.extension].filter(Boolean).join('\n\n')
    : (rec.full_solution || rec.solution_steps || rec.ocr_text || '');
  $('#view').innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">${histTypeLabel(rec.record_type)} · ${esc(t)}</div></div>
    <div id="hdBody">${body}</div>
    <div id="hdAsk">${isAnim ? '' : askBoxHtml()}</div>
    <button class="fab-down hidden" id="fabDown" title="回到底部">↓</button>`;
  $('#bk').onclick = () => { S.view = 'history'; render(); };
  bindScrollFab();
  const m = {
    hOcr: rec.ocr_text, hSteps: rec.solution_steps, hFull: rec.full_solution,
    hExtra: rec.latex_extras, hSum: parts.summary, hExt: parts.extension
  };
  Object.keys(m).forEach((id) => {
    const el = $('#hdBody').querySelector('#' + id + ' [data-md]');
    if (el && m[id]) renderMarkdownInto(el, m[id]);
  });
  // 掌握程度按 session_id 落盘；服务端 /history 的 mastery_level 也是按它读的
  const hd = $('#hdBody');
  if (hd.querySelector('[data-mastery-row]')) bindMastery(hd, rec.session_id || '');
  if (!isAnim) bindAskBox(rec.session_id || '', askContext || '');
  hd.querySelectorAll('[data-fold]').forEach((s) => {
    s.onclick = () => { const tt = hd.querySelector('#' + s.dataset.fold); tt.classList.toggle('hidden'); s.textContent = tt.classList.contains('hidden') ? '▼ 展开' : '▲ 收起'; };
  });
}

/* ==================== 报告 ==================== */
function renderReport(v) {
  // 未登录（随便看看）：报告是基于个人历史生成的，游客没有任何记录，
  // 以前会照常显示按钮，点了只弹一个 toast —— 看起来就像“按钮点不动”。
  // 这里直接把原因说清楚，并引导去登录。
  if (!S.user) {
    v.innerHTML = `
      <div class="card">
        <div class="card-title">📊 学情报告</div>
        <div class="muted" style="margin:8px 0 12px">学情报告是基于你在本账号下的解题记录生成的。当前处于「随便看看」模式，还没有任何记录，所以暂时无法生成报告。</div>
        <button class="btn btn-primary btn-block" id="goLogin">去登录 / 注册</button>
      </div>`;
    $('#goLogin').onclick = () => { API.logout(); location.reload(); };
    return;
  }
  v.innerHTML = `
    <div class="card">
      <div class="card-title">📊 学情报告</div>
      <div class="setting-row"><span class="lbl">统计周期</span>
        <div class="chips" id="rangeChips">
          <button class="chip active" data-d="7">近 7 天</button>
          <button class="chip" data-d="30">近 30 天</button>
          <button class="chip" data-d="90">近 90 天</button>
          <button class="chip" data-d="0">全部</button>
        </div></div>
      <div class="setting-row"><span class="lbl">筛选</span>
        <div class="chips" id="repGradeChips"></div></div>
      <div class="setting-row"><span class="lbl"></span>
        <div class="chips" id="repSubjectChips"></div></div>
      <div class="row" style="margin-top:12px">
        <button class="btn btn-primary" id="btnData">数据版报告</button>
        <button class="btn btn-ghost" id="btnAi">AI 版报告</button>
      </div>
    </div>
    <div id="reportOut"></div>`;
  let days = 7;
  // 筛选状态（年级/学科单选，空=不限）
  const flt = { grade: '', subject: '' };
  const GRADES = ['', '小学', '初中', '高中', '考研'];
  const SUBJECTS = ['', '语文', '数学', '英语', '物理', '化学', '生物', '历史', '地理', '政治'];
  const paintFilters = () => {
    const gc = $('#repGradeChips'), sc = $('#repSubjectChips');
    if (gc) gc.innerHTML = GRADES.map((g) =>
      `<button class="chip ${flt.grade === g ? 'active' : ''}" data-g="${g}">${g || '年级不限'}</button>`).join('');
    if (sc) sc.innerHTML = SUBJECTS.map((sj) =>
      `<button class="chip ${flt.subject === sj ? 'active' : ''}" data-s="${sj}">${sj || '学科不限'}</button>`).join('');
    if (gc) gc.querySelectorAll('.chip').forEach((b) => b.onclick = () => { flt.grade = b.dataset.g; paintFilters(); });
    if (sc) sc.querySelectorAll('.chip').forEach((b) => b.onclick = () => { flt.subject = b.dataset.s; paintFilters(); });
  };
  paintFilters();
  v.querySelectorAll('#rangeChips .chip').forEach((c) => {
    c.onclick = () => {
      days = +c.dataset.d;
      v.querySelectorAll('#rangeChips .chip').forEach((x) => x.classList.toggle('active', x === c));
    };
  });
  // 报告接口的额外筛选头
  const repOpts = () => Object.assign(opts(), { grade: flt.grade, subject: flt.subject });
  $('#btnData').onclick = async () => {
    overlay('正在生成数据报告…');
    try {
      const st = settings();
      const r = await API.dataReport(days, st.theme, repOpts());
      overlay(false);
      if (r.status !== 'ok') { toast(r.message || '生成失败'); return; }
      // 报告 iframe 用 flex 容器自适应，避免写死高度导致“没填满”
      $('#reportOut').innerHTML = '<div class="report-frame-wrap"><iframe src="' + r.url + '"></iframe></div>';
    } catch (e) { overlay(false); toast('生成失败：' + e.message); }
  };
  $('#btnAi').onclick = async () => {
    const out = $('#reportOut');
    const st0 = settings();
    out.innerHTML = `<div class="card"><div class="card-title">🤖 AI 学情报告</div>
      <div class="dim" style="margin-bottom:6px">已应用当前解题偏好：风格=${esc(st0.style)}·方言=${esc(st0.dialect)}${st0.grade ? '·年级=' + esc(st0.grade) : ''}·人格=${esc(st0.personality)}·详细度=${esc(st0.detail)}</div>
      <div id="aiRepStatus" class="muted">正在生成…</div><div class="md" id="aiRep"></div></div>`;
    let acc = '';
    let ended = false;
    // 6.2/10.1 报告也是流式的，同样节流重绘，避免长报告把页面拖卡
    const paintRep = makeThrottle(() => {
      const el = $('#aiRep'); if (el) renderMarkdownInto(el, acc);
      if (!ended) { const s = $('#aiRepStatus'); if (s && acc) s.textContent = ''; }
    }, 200);
    try {
      // 服务端 /report/ai/stream 为 POST 型 SSE；用 postSSE（EventSource 只能 GET）
      // 第 4 个参数 opts() 会把风格/方言/年级/人格/详细度一并传给报告接口
      const src = API.postSSE('/report/ai/stream', { days: days, theme: settings().theme }, {
        onEvent: (m) => {
          if (m.stage === 'summary') {
            const st = $('#aiRepStatus'); if (st) st.textContent = '分析完成，正在撰写…';
            return;
          }
          if (m.stage === 'report_chunk') {
            acc += (m.content || '');
            paintRep();
            return;
          }
          if (m.stage === 'error') {
            ended = true;
            if (!acc) out.innerHTML = `<div class="card"><div class="muted" style="color:#FF9800">${esc(m.content || m.message || '生成失败')}</div></div>`;
            return;
          }
        },
        onDone: () => {
          ended = true;
          paintRep.flush();
          const st = $('#aiRepStatus'); if (st) st.textContent = '';
          if (!acc) out.innerHTML = `<div class="card"><div class="muted" style="color:#FF9800">报告内容为空</div></div>`;
        },
        onError: (err) => {
          ended = true;
          const st = $('#aiRepStatus');
          if (st) st.textContent = '';
          // 把服务端给的失败原因（如“仅 4 道题，至少需 5 题”）如实展示，
          // 不要统一揽成“连接中断”——否则用户根本不知道是题量不够
          if (!acc) {
            const msg = (err && err.message) ? err.message : '连接中断';
            out.innerHTML = `<div class="card"><div class="card-title" style="color:#FF9800">⚠️ 暂时无法生成 AI 报告</div>`
              + `<div class="muted">${esc(msg)}</div></div>`;
          } else {
            paintRep.flush();
          }
        }
      }, opts());
    } catch (e) {
      if (!acc) out.innerHTML = `<div class="card"><div class="muted" style="color:#FF9800">生成失败：${esc(e.message)}</div></div>`;
    }
  };
}

/* ==================== 设置 ==================== */
function renderSettings(v) {
  const st = settings();
  const P = {
    dialects: ["普通话", "四川话", "东北话", "粤语", "上海话", "天津话", "陕西话", "河南话", "湖南话"],
    grades: [["", "不限"], ["小学", "小学"], ["初中", "初中"], ["高中", "高中"], ["考研", "考研"]],
    details: [["auto", "自动"], ["very_detailed", "非常细"], ["detailed", "较细"], ["brief", "简略"]],
    modules: [["solution_steps", "解题思路"], ["full_solution", "完整解析"], ["mind_map", "思维导图"], ["suggested_questions", "延伸问题"], ["mistakes", "易错点详解"], ["extension", "知识拓展"]],
    llm: {
      // 16.1 DeepSeek 多模型可选（空值=用服务端默认模型；名字取自本环境实际可用的模型 ID）
      deepseek: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
      qwen: ["qwen3.8-max", "qwen3.7-plus", "qwen3.8-flash", "deepseek-v4-flash-0731", "kimi-k3", "glm-5.3", "MiniMax-M3"],
      doubao: ["doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628", "doubao-seed-evolving-260628"],
      hunyuan: ["hy4-preview"]
    },
    vision: ["qwen3.8-max", "qwen3.7-plus", "qwen3.5-omni-plus", "kimi-k3", "deepseek-v4-flash-vision-exp"]
  };
  const modelList = P.llm[st.engine] || [];
  const mods = st.showModules || {};
  const chipsHtml = (items, cur, key, cast) => items.map(([id, label]) =>
    `<button class="chip ${String(cur) === String(id) ? 'active' : ''}" data-k="${key}" data-v="${id}">${label}</button>`).join('');

  v.innerHTML = `
    <div class="card">
      <div class="card-title">⚙️ 账号</div>
      <div class="setting-row"><span class="lbl">当前账号</span>
        <span class="muted">${S.user ? esc(S.user.username) + (S.user.is_admin ? '（管理员）' : '') : '未登录'}</span></div>
      <div class="setting-row"><span class="lbl">AI 使用权限</span>
        <span class="muted" style="color:${S.user && S.user.ai_permission === false ? '#F44336' : '#4CAF50'}">${S.user ? (S.user.ai_permission === false ? '未开通' : '已开通') : '—'}</span></div>
    </div>

    <div class="card">
      <div class="card-title">🎨 外观</div>
      <div class="setting-row"><span class="lbl">主题模式</span>
        <div class="chips" data-group="theme">${chipsHtml([["dark", "深色"], ["light", "浅色"]], st.theme, 'theme')}</div></div>
      <div class="setting-row"><span class="lbl">全局字号</span>
        <div class="chips" data-group="fontSize">${chipsHtml([[12, "小"], [16, "默认"], [20, "大"], [24, "特大"]], st.fontSize, 'fontSize', Number)}</div></div>
    </div>

    <div class="card">
      <div class="card-title">🧠 AI 模型</div>
      <div class="setting-row"><span class="lbl">大语言模型</span>
        <div class="chips" data-group="engine">${chipsHtml([["deepseek", "DeepSeek"], ["qwen", "千问 Qwen"], ["doubao", "豆包"], ["hunyuan", "混元"]], st.engine, 'engine')}</div></div>
      ${modelList.length ? `<div class="setting-row"><span class="lbl">具体模型</span>
        <div class="chips" data-group="llmModel">${chipsHtml(modelList.map((m) => [m, m]), st.llmModel, 'llmModel')}</div></div>` : ''}
      <div class="setting-row"><span class="lbl">OCR 模式</span>
        <div class="chips" data-group="ocrMode">${chipsHtml([["paddle", "PaddleOCR 本地"], ["qwen", "AI 视觉"]], st.ocrMode, 'ocrMode')}</div></div>
      ${st.ocrMode === 'qwen' ? `<div class="setting-row"><span class="lbl">视觉模型</span>
        <div class="chips" data-group="visionModel">${chipsHtml(P.vision.map((m) => [m, m]), st.visionModel, 'visionModel')}</div></div>` : ''}
    </div>

    <div class="card">
      <div class="card-title">🗣️ 讲解口吻</div>
      <div class="setting-row"><span class="lbl">方言</span>
        <div class="chips" data-group="dialect">${chipsHtml(P.dialects.map((d) => [d, d]), st.dialect, 'dialect')}</div></div>
      <div class="setting-row"><span class="lbl">年级</span>
        <div class="chips" data-group="grade">${chipsHtml(P.grades, st.grade, 'grade')}</div></div>
      <div class="setting-row"><span class="lbl">回答风格</span>
        <div class="chips" data-group="style">${chipsHtml([["formal", "正式"], ["encouraging", "鼓励"], ["humorous", "幽默"]], st.style, 'style')}</div></div>
    </div>

    <div class="card">
      <div class="card-title">👤 老师人格（MBTI）</div>
      <div class="setting-row"><span class="lbl">人格</span>
        <div class="chips" data-group="personalityMode">
          <button class="chip ${st.personality === 'auto' ? 'active' : ''}" data-k="personalityMode" data-v="auto">自动（按学科）</button>
          <button class="chip ${st.personality !== 'auto' ? 'active' : ''}" data-k="personalityMode" data-v="manual">手动选择</button>
        </div></div>
      ${st.personality !== 'auto' ? `<div class="setting-row"><span class="lbl">类型</span>
        <div class="chips" data-group="personality">${chipsHtml(mbtiList().map((m) => [m, m]), st.personality, 'personality')}</div></div>` : ''}
      <div class="muted">自动将按学科推荐合适类型的老师风格（如数学→INTP、语文→INFJ）</div>
    </div>

    <div class="card">
      <div class="card-title">📐 解题偏好</div>
      <div class="setting-row"><span class="lbl">详细度</span>
        <div class="chips" data-group="detail">${chipsHtml(P.details, st.detail, 'detail')}</div></div>
      <div class="setting-row"><span class="lbl">题库搜题</span>
        <div class="chips" data-group="searchEnabled">${chipsHtml([[false, "已停用"]], false, 'searchEnabled')}</div></div>
      <div class="muted">⚠️ 题库搜题已因平台额度限制全局停用，暂不可开启。</div>
      <div class="setting-row"><span class="lbl">思考模式</span>
        <div class="chips" data-group="thinking">${chipsHtml([["off", "关闭"], ["on", "开启"], ["auto", "自动"]], st.thinking, 'thinking')}</div></div>
      <div class="setting-row"><span class="lbl">图解辅助</span>
        <div class="chips" data-group="latexHelper">${chipsHtml([["off", "关闭"], ["on", "开启"], ["auto", "自动"]], st.latexHelper, 'latexHelper')}</div></div>
      <div class="setting-row"><span class="lbl">边解答边设问</span>
        <div class="chips" data-group="interactiveQuiz">${chipsHtml([[true, "开启"], [false, "关闭"]], st.interactiveQuiz, 'interactiveQuiz')}</div></div>
      <div class="muted">开启后，讲解过程中会穿插 2~5 个顺应解题思路的简单小问题，作答后即时判定正误再继续。</div>
    </div>

    <div class="card">
      <div class="card-title">📂 显示模块</div>
      <div class="chips" data-group="showModules">${P.modules.map(([k, label]) =>
        `<button class="chip ${mods[k] !== false ? 'active' : ''}" data-k="showModules" data-v="${k}">${label}</button>`).join('')}</div>
    </div>

    <div class="card">
      <div class="card-title">🍅 番茄钟</div>
      <div class="setting-row"><span class="lbl">工作时长（分钟）</span>
        <input class="ipt" id="pmWork" type="number" min="1" max="180" value="${st.pomodoroWork || 25}" style="max-width:110px"></div>
      <div class="setting-row"><span class="lbl">休息时长（分钟）</span>
        <input class="ipt" id="pmRest" type="number" min="1" max="60" value="${st.pomodoroRest || 5}" style="max-width:110px"></div>
      <div class="setting-row"><span class="lbl">计时模式</span>
        <div class="chips" data-group="pomodoroMode">${chipsHtml([["countdown", "⏳ 倒计时"], ["countup", "⏱ 正计时"]], st.pomodoroMode || 'countdown', 'pomodoroMode')}</div></div>
      <div class="muted">提示：番茄钟计时器在主界面顶部 🍅 显示</div>
    </div>

    <div class="card">
      <div class="card-title">📅 历史与数据</div>
      <div class="setting-row"><span class="lbl">开始日期</span>
        <input class="ipt" id="hStart" type="text" inputmode="numeric" placeholder="2026-09-17" value="${esc(st.historyStartDate || '')}" style="max-width:170px"></div>
      <div class="setting-row"><span class="lbl">截止日期</span>
        <input class="ipt" id="hEnd" type="text" inputmode="numeric" placeholder="2026-09-17" value="${esc(st.historyEndDate || '')}" style="max-width:170px"></div>
      <div class="muted">手输日期，格式 2026-09-17（也接受 2026/9/17、20260917）</div>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-ghost" id="btnClearHistory">🗑️ 清除历史记录</button>
        <button class="btn btn-ghost" id="btnExport">📤 导出数据</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">ℹ️ 关于</div>
      <div class="muted">学习助手 学生端（网页版）v2.1.2<br>服务端：${location.host || '本机'}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn btn-ghost" id="btnGuide">📖 使用说明</button>
        <button class="btn btn-ghost" id="btnPrivacy">🔒 隐私政策</button>
        <button class="btn btn-ghost" id="btnReport">📣 上报问题</button>
      </div>
      <div class="row" style="margin-top:10px"><button class="btn btn-primary" id="btnLogout">🚪 退出登录</button></div>
    </div>`;

  /* 统一 chip 绑定：点击切换 + 持久化；特殊键附带副作用 */
  v.querySelectorAll('.chips[data-group] .chip').forEach((c) => {
    c.onclick = () => {
      const k = c.dataset.k, val = c.dataset.v;
      let out = val;
      if (k === 'fontSize') out = Number(val);
      else if (k === 'searchEnabled') out = (val === 'true');
      else if (k === 'interactiveQuiz') out = (val === 'true');
      else if (k === 'showModules') {
        const cur = Object.assign({}, settings().showModules || {});
        cur[val] = !(cur[val] !== false);
        cur[val] = (cur[val] === true) ? true : false;
        saveSettings('showModules', cur);
        renderSettings(v);
        toast('已保存');
        return;
      } else if (k === 'personalityMode') {
        saveSettings('personality', val === 'auto' ? 'auto' : (settings().personality === 'auto' ? 'INTJ' : settings().personality));
        renderSettings(v); toast('已保存'); return;
      } else if (k === 'engine') {
        const def = { deepseek: '', qwen: 'qwen3.8-max', doubao: 'doubao-seed-2-1-pro-260628', hunyuan: 'hy4-preview' }[val] || '';
        saveSettings('engine', val); saveSettings('llmModel', def);
        renderSettings(v); toast('已保存'); return;
      } else if (k === 'ocrMode') {
        saveSettings('ocrMode', val);
        if (val === 'qwen' && !settings().visionModel) saveSettings('visionModel', 'qwen3.8-max');
        renderSettings(v); toast('已保存'); return;
      }
      saveSettings(k, out);
      renderSettings(v);
      if (k === 'theme') applyTheme(val);
      if (k === 'fontSize') applyFontSize(Number(val));
      toast('已保存');
    };
  });

  /* 番茄钟输入 */
  const bindNum = (sel, key, def) => {
    const el = $(sel); if (!el) return;
    el.onchange = () => {
      const n = parseInt(el.value, 10);
      saveSettings(key, (isNaN(n) || n <= 0) ? def : n);
      toast('已保存');
    };
  };
  bindNum('#pmWork', 'pomodoroWork', 25);
  bindNum('#pmRest', 'pomodoroRest', 5);

  /* 历史日期：文本框（date 输入框在某些环境不好用，改为手输 YYYY-MM-DD 并做格式校验） */
  const bindDate = (sel, key) => {
    const el = $(sel); if (!el) return;
    const commit = () => {
      let v = (el.value || '').trim();
      if (!v) { saveSettings(key, ''); return; }
      // 容错：2026/9/17、20260917 → 2026-09-17
      v = v.replace(/[.\/年]/g, '-').replace(/月/g, '-').replace(/日/g, '').trim();
      let m = v.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
      if (!m) m = v.match(/^(\d{4})(\d{2})(\d{2})$/);
      if (!m) { toast('日期格式应为 2026-09-17'); el.value = settings()[key] || ''; return; }
      const norm = m[1] + '-' + String(m[2]).padStart(2, '0') + '-' + String(m[3]).padStart(2, '0');
      el.value = norm;
      saveSettings(key, norm);
      toast('已保存');
    };
    el.onblur = commit;
    el.onkeydown = (e) => { if (e.key === 'Enter') commit(); };
  };
  bindDate('#hStart', 'historyStartDate');
  bindDate('#hEnd', 'historyEndDate');

  const bc = $('#btnClearHistory');
  if (bc) bc.onclick = () => {
    if (!confirm('确定要清除全部历史记录吗？此操作不可恢复。')) return;
    API.req('/history', { method: 'DELETE' })
      .then(() => toast('已清除'))
      .catch((e) => toast('清除失败：' + e.message));
  };
  const be = $('#btnExport');
  if (be) be.onclick = () => {
    S.view = 'history'; render();
    toast('已跳转“历史记录”，点右上角“📤 导出数据”即可导出');
  };
  const bl = $('#btnLogout');
  if (bl) bl.onclick = () => {
    if (!confirm('确定退出登录？')) return;
    API.logout(); S.user = null; render();
  };
  // 📖 使用说明：多页手册（网页端）
  const bg = $('#btnGuide');
  if (bg) bg.onclick = () => showGuide();
  // 🔒 隐私政策
  const bp = $('#btnPrivacy');
  if (bp) bp.onclick = () => showPrivacy();
  // 📣 上报问题：收集描述 + 当前环境信息提交服务端
  const brp = $('#btnReport');
  if (brp) brp.onclick = () => showReportIssue();
}

/* MBTI 16 型 */
function mbtiList() {
  const out = [];
  for (const a of ['E', 'I']) for (const b of ['S', 'N']) for (const c of ['T', 'F']) for (const d of ['J', 'P']) out.push(a + b + c + d);
  return out;
}

/* ==================== 悬浮回到底部 ==================== */
function bindScrollFab() {
  const fab = $('#fabDown'); if (!fab) return;
  const v = $('#view');
  v.onscroll = () => {
    const gap = v.scrollHeight - v.scrollTop - v.clientHeight;
    fab.classList.toggle('hidden', gap < 120);
  };
  fab.onclick = () => v.scrollTo({ top: v.scrollHeight, behavior: 'smooth' });
}

/* ==================== 📖 使用说明（多页） / 🔒 隐私 / 📣 上报 ==================== */
const GUIDE_PAGES = [
  {
    icon: '📷', title: '拍照解题',
    body: `把题目拍清楚，尽量让整道题都在取景框里。<br><br>
      · 光线充足、不要反光，否则识别会不准<br>
      · 一张图只拍一道题效果最好<br>
      · 也可以在首页用“图库选图”或“文字输入”`
  },
  {
    icon: '🎬', title: 'AI 动画与批注',
    body: `AI 动画会用可交互的图形演示解题过程，生成需要 1~2 分钟。<br><br>
      AI 批注会像老师一样在图上圈画、划线并写下批语，需要 20~60 秒。`
  },
  {
    icon: '📊', title: '学情报告',
    body: `学情报告基于你账号下的解题记录生成，可以按年级、学科筛选。<br><br>
      注意：「随便看看」模式下没有任何记录，所以生成不了报告。`
  },
  {
    icon: '🍅', title: '番茄钟',
    body: `首页顶部 🍅 可以开始专注计时，支持倒计时与正计时两种模式。<br><br>
      工作时长、休息时长可在「设置 → 🍅 番茄钟」里调整。`
  },
  {
    icon: '⚙️', title: '设置与账号',
    body: `在设置里可以切换主题、字号、AI 模型，以及控制各功能模块的显示。<br><br>
      「历史与数据」里可以设置日期范围、清除历史或导出数据。`
  },
];

/** 📖 使用说明：多页翻页手册 */
function showGuide() {
  S.view = 'solve';
  const v = $('#view');
  let page = 0;
  const paint = () => {
    const p = GUIDE_PAGES[page];
    const dots = GUIDE_PAGES.map((_, i) =>
      `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;margin:0 3px;background:${i === page ? 'var(--accent,#00D2FF)' : 'rgba(128,128,128,.4)'}"></span>`).join('');
    v.innerHTML = `
      <div class="subbar"><button class="back" id="gBk">← 返回</button><div class="ttl">📖 使用说明</div></div>
      <div class="card">
        <div class="card-title">${p.icon} ${esc(p.title)}</div>
        <div class="muted" style="line-height:1.9">${p.body}</div>
      </div>
      <div class="card" style="text-align:center">
        <div style="margin-bottom:10px">${dots}</div>
        <div class="row" style="justify-content:center">
          <button class="btn btn-ghost" id="gPrev" ${page === 0 ? 'disabled' : ''}>← 上一页</button>
          <span class="dim" style="margin:0 10px">${page + 1} / ${GUIDE_PAGES.length}</span>
          <button class="btn btn-primary" id="gNext" ${page === GUIDE_PAGES.length - 1 ? 'disabled' : ''}>下一页 →</button>
        </div>
      </div>`;
    $('#gBk').onclick = () => { S.view = 'settings'; render(); };
    const pv = $('#gPrev'), nx = $('#gNext');
    if (pv) pv.onclick = () => { if (page > 0) { page--; paint(); } };
    if (nx) nx.onclick = () => { if (page < GUIDE_PAGES.length - 1) { page++; paint(); } };
  };
  paint();
}

/** 🔒 隐私政策 */
function showPrivacy() {
  S.view = 'solve';
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="pBk">← 返回</button><div class="ttl">🔒 隐私政策</div></div>
    <div class="card">
      <div class="card-title">我们收集什么</div>
      <div class="muted" style="line-height:1.9">
        · 你上传的题目图片，以及由此识别出的文字<br>
        · 你在本应用内的解题历史，用于生成个人学情报告<br>
        · 账号名与加密后的密码（密码不可逆加密，我们看不到明文）
      </div>
    </div>
    <div class="card">
      <div class="card-title">我们怎么用</div>
      <div class="muted" style="line-height:1.9">
        · 图片与文字会发送给 AI 模型服务商，用于生成解答、动画与批注<br>
        · 解题记录仅保存在本服务端，用于你自己的历史与报告<br>
        · 我们不会把你的数据卖给任何第三方，也不会用于广告
      </div>
    </div>
    <div class="card">
      <div class="card-title">你可以怎么做</div>
      <div class="muted" style="line-height:1.9">
        · 在「设置 → 历史与数据」里随时清除全部历史记录<br>
        · 在「设置 → 历史与数据」里导出自己的数据<br>
        · 退出登录后，本地只保留无关隐私的界面偏好设置
      </div>
    </div>
    <div class="card"><div class="dim">⚠️ 本应用为学习辅助工具，AI 生成内容请仔细甄别，不要直接作为作业答案提交。</div></div>`;
  $('#pBk').onclick = () => { S.view = 'settings'; render(); };
}

/** 📣 上报问题：描述 + 自动附带环境信息 */
function showReportIssue() {
  S.view = 'solve';
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="rBk">← 返回</button><div class="ttl">📣 上报问题</div></div>
    <div class="card">
      <div class="card-title">问题描述</div>
      <textarea class="ipt" id="rDesc" rows="6" placeholder="请描述你遇到的问题，例如：点 AI 解题后一直转圈 / 动画打不开 / 批注位置不对…" style="width:100%;resize:vertical"></textarea>
      <div class="muted" style="margin-top:8px">提交时会自动附带当前版本与账号信息，便于排查。</div>
      <div class="row" style="margin-top:12px">
        <button class="btn btn-primary" id="rSubmit">提交</button>
        <span class="dim" id="rHint"></span>
      </div>
    </div>`;
  $('#rBk').onclick = () => { S.view = 'settings'; render(); };
  $('#rSubmit').onclick = () => {
    const desc = ($('#rDesc').value || '').trim();
    if (!desc) { toast('请先写一下问题描述'); return; }
    const hint = $('#rHint');
    if (hint) hint.textContent = '提交中…';
    API.req('/report-issue', {
      method: 'POST',
      body: JSON.stringify({
        description: desc,
        version: '2.1.2-web',
        page: location.href,
        user_agent: navigator.userAgent,
      })
    }).then((r) => {
      if (r && r.status === 'ok') {
        toast('已提交，感谢反馈！');
        S.view = 'settings'; render();
      } else {
        const msg = (r && r.message) || '提交失败';
        if (hint) hint.textContent = msg;
        toast(msg);
      }
    }).catch((e) => {
      if (hint) hint.textContent = '提交失败：' + e.message;
      toast('提交失败：' + e.message);
    });
  };
}

/* ==================== 🍅 番茄钟 ==================== */
const POMO = { running: false, remaining: 0, elapsed: 0, timer: null, show: false };

function pomoInit() {
  const st = settings();
  POMO.remaining = (st.pomodoroWork || 25) * 60;
  POMO.elapsed = 0;
  pomoPaint();
}
function pomoFmt(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}
function pomoIsCountup() { return (settings().pomodoroMode || 'countdown') === 'countup'; }
function pomoTick() {
  if (pomoIsCountup()) {
    POMO.elapsed++;
  } else {
    POMO.remaining--;
    if (POMO.remaining <= 0) {
      pomoStop();
      pomoPaint();
      toast('🍅 番茄钟结束，休息一下吧！', 4000);
      try { new Audio('data:audio/wav;base64,UklGRl9vT19XQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=').play(); } catch (e) {}
      POMO.remaining = (settings().pomodoroRest || 5) * 60;
      return;
    }
  }
  pomoPaint();
}
function pomoStart() {
  if (POMO.running) return;
  POMO.running = true;
  POMO.timer = setInterval(pomoTick, 1000);
  pomoPaint();
}
function pomoStop() {
  POMO.running = false;
  if (POMO.timer) { clearInterval(POMO.timer); POMO.timer = null; }
  pomoPaint();
}
function pomoReset() {
  pomoStop();
  const st = settings();
  POMO.remaining = (st.pomodoroWork || 25) * 60;
  POMO.elapsed = 0;
  pomoPaint();
}
function pomoPaint() {
  const btn = document.getElementById('pomoBtn');
  if (btn) {
    if (POMO.running) {
      btn.textContent = '🍅 ' + (pomoIsCountup() ? pomoFmt(POMO.elapsed) : pomoFmt(POMO.remaining));
      btn.style.fontSize = '12px';
    } else {
      btn.textContent = '🍅';
      btn.style.fontSize = '';
    }
  }
  // 弹窗开着时同步刷新（原来弹窗里的秒数不走，只在你点按钮时才刷一次）
  if (POMO._refresh) { try { POMO._refresh(); } catch (e) {} }
}
function pomoDialog() {
  const st = settings();
  const countup = pomoIsCountup();
  const cur = countup ? pomoFmt(POMO.elapsed) : pomoFmt(POMO.remaining);
  let dlg = document.getElementById('pomoDlg');
  if (dlg) dlg.remove();
  dlg = document.createElement('div');
  dlg.id = 'pomoDlg';
  dlg.className = 'overlay';
  dlg.style.background = 'rgba(0,0,0,0.55)';
  dlg.innerHTML = `
    <div class="card" style="max-width:340px;width:88%;margin:auto">
      <div class="card-title">🍅 番茄钟</div>
      <div style="text-align:center;margin:12px 0">
        <div class="muted" id="pmLabel">${countup ? '⏱ 已计时' : '⏳ 剩余时间'}</div>
        <div id="pmTime" style="font-size:38px;font-weight:bold;color:${countup ? '#00D2FF' : '#FF5722'}">${cur}</div>
      </div>
      <div class="chips" style="justify-content:center" id="pmModes">
        <button class="chip ${countup ? '' : 'active'}" data-mode="countdown">⏳ 倒计时</button>
        <button class="chip ${countup ? 'active' : ''}" data-mode="countup">⏱ 正计时</button>
      </div>
      <div class="chips" style="justify-content:center;margin-top:8px">
        <button class="chip" id="pmToggle">${POMO.running ? '⏸ 暂停' : '▶️ 开始'}</button>
        <button class="chip" id="pmReset">🔄 重置</button>
      </div>
      <div class="muted" style="margin-top:10px;text-align:center">工作时长 ${st.pomodoroWork || 25} 分 · 休息 ${st.pomodoroRest || 5} 分<br>可直接在上面切换计时模式，或去「设置 → 番茄钟」改时长</div>
      <div class="row" style="margin-top:12px"><button class="btn btn-ghost" id="pmClose">关闭</button></div>
    </div>`;
  document.body.appendChild(dlg);
  const refresh = () => {
    const up = pomoIsCountup();
    const el = dlg.querySelector('#pmTime');
    if (el) { el.textContent = up ? pomoFmt(POMO.elapsed) : pomoFmt(POMO.remaining); el.style.color = up ? '#00D2FF' : '#FF5722'; }
    const lb = dlg.querySelector('#pmLabel');
    if (lb) lb.textContent = up ? '⏱ 已计时' : '⏳ 剩余时间';
    const t = dlg.querySelector('#pmToggle');
    if (t) t.textContent = POMO.running ? '⏸ 暂停' : '▶️ 开始';
    dlg.querySelectorAll('#pmModes .chip').forEach((c) => c.classList.toggle('active', c.dataset.mode === (up ? 'countup' : 'countdown')));
  };
  POMO._refresh = refresh;
  // 4.2 弹窗内直接切换倒计时/正计时（原来必须进设置才能改）
  dlg.querySelectorAll('#pmModes .chip').forEach((c) => {
    c.onclick = () => {
      const mode = c.dataset.mode;
      if (mode === (pomoIsCountup() ? 'countup' : 'countdown')) return;
      saveSettings('pomodoroMode', mode);
      // 切换时归零，避免“剩余 12:30”直接变成“已计时 12:30”的困惑
      pomoStop();
      POMO.elapsed = 0;
      POMO.remaining = (settings().pomodoroWork || 25) * 60;
      refresh();
      toast(mode === 'countup' ? '已切换为 ⏱ 正计时' : '已切换为 ⏳ 倒计时');
    };
  });
  dlg.querySelector('#pmToggle').onclick = () => { POMO.running ? pomoStop() : pomoStart(); refresh(); };
  dlg.querySelector('#pmReset').onclick = () => { pomoReset(); refresh(); };
  dlg.querySelector('#pmClose').onclick = () => { dlg.remove(); POMO._refresh = null; };
  dlg.onclick = (e) => { if (e.target === dlg) { dlg.remove(); POMO._refresh = null; } };
}

/* ==================== 启动 ==================== */
(async function boot() {
  const st = settings();
  applyTheme(st.theme); applyFontSize(st.fontSize);
  const u = await API.verify();
  if (u) { S.user = u; enterApp(); }
  else { $('#authView').classList.remove('hidden'); }
})();

})();
