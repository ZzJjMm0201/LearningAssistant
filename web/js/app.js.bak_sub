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
    fontSize: 16, theme: 'dark'
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
function md(text) {
  if (!text) return '';
  let html = (window.marked ? marked.parse(String(text)) : esc(text));
  return html;
}
function renderMarkdownInto(el, text) {
  el.innerHTML = md(text);
  if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise([el]).catch(() => {});
  }
}
function applyFontSize(px) {
  document.documentElement.style.setProperty('--font-size', px + 'px');
  document.body.style.fontSize = px + 'px';
}
function applyTheme(theme) {
  document.body.setAttribute('data-theme', theme);
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
$('#meBtn').onclick = () => {
  const u = S.user;
  const name = u ? (u.username + (u.is_admin ? '（管理员）' : '') + (u.ai_permission ? '' : '｜⚠️无AI权限')) : '未登录';
  if (confirm(`当前账号：${name}\n\n是否退出登录？`)) { API.logout(); location.reload(); }
};

/* ---------- 视图路由 ---------- */
function render() {
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.view === S.view));
  const v = $('#view');
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
      <div class="muted">📷 拍照解题 ｜ 📎 知识延伸 ｜ 🎬 AI 动画 ｜ 📊 数据报告 ｜ 🤖 AI 报告</div>
    </div>`;
  v.querySelectorAll('.fn-card').forEach((c) => c.onclick = () => openFn(c.dataset.fn));
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

/* ---------- 上传弹层（拍照/选图/文字） ---------- */
function openFn(fn) {
  const cfg = {
    solve:  { t: '📷 拍照解题', text: 'startSolveText' },
    extend: { t: '📎 知识延伸', text: 'startExtendText' },
    animate:{ t: '🎬 AI 动画', text: 'startAnimateText' }
  }[fn];
  const v = $('#view');
  v.innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">${cfg.t}</div></div>
    <div class="drop" id="drop"><span class="big">🖼️</span><div>点击选择图片</div><div class="dim">支持拍照 / 相册</div></div>
    <input type="file" id="file" accept="image/*" capture="environment" class="hidden">
    <div id="prevWrap"></div>
    <div class="card">
      <div class="card-title">或直接输入文字</div>
      <textarea id="fnText" placeholder="输入题目文字…"></textarea>
      <button class="btn btn-primary btn-block" id="fnGo" style="margin-top:10px">开始</button>
    </div>`;
  $('#bk').onclick = render;
  const fileInput = $('#file');
  $('#drop').onclick = () => fileInput.click();
  fileInput.onchange = () => {
    const f = fileInput.files[0]; if (!f) return;
    const url = URL.createObjectURL(f);
    $('#prevWrap').innerHTML = `<img class="preview-img" src="${url}">
      <button class="btn btn-primary btn-block" id="sendImg">上传并开始</button>`;
    $('#sendImg').onclick = () => {
      if (fn === 'solve') startSolveImage(f);
      else if (fn === 'extend') startExtendImage(f);
      else startAnimateImage(f);
    };
  };
  $('#fnGo').onclick = () => {
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
    personality: s.personality, detail: s.detail, subject: s.subject
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
function startSolveText(text) {
  overlay('正在启动解题…');
  API.solveText(text, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'processing' && r.status !== 'ok') { toast(r.message || '启动失败'); return; }
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
    <div id="askBox"></div>
    <button class="fab-down hidden" id="fabDown" title="回到底部">↓</button>`;
  $('#bk').onclick = () => { API.cancelSolve(rid).catch(() => {}); S.view = 'home'; render(); };
  bindScrollFab();
}

let solveAcc = { steps: '', full: '', extras: '', mindmap: '', info: {}, similar: [], qa: [] };

function streamSolve(rid) {
  solveAcc = { steps: '', full: '', extras: '', mindmap: '', info: {}, similar: [], qa: [] };
  API.sse('/solve/stream/' + rid, {
    onEvent: (m) => handleSolveEvent(m),
    onDone: () => { const p = $('#prog'); if (p) p.innerHTML = '<span class="dot" style="background:#4CAF50"></span><span>解答完成</span>'; renderAskBox(rid); },
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
  if (stage === 'solution_steps_chunk') { solveAcc.steps = content || ''; renderSolveBody(); if (progText) progText.textContent = '阶段 2/6 · 解题思路'; return; }
  if (stage === 'solution_steps') { solveAcc.steps = content || ''; renderSolveBody(); return; }
  if (stage === 'solution_chunk') { solveAcc.full = content || ''; renderSolveBody(); if (progText) progText.textContent = '阶段 3/6 · 完整解析'; return; }
  if (stage === 'solution' || stage === 'solution_rendered') { solveAcc.full = content || ''; renderSolveBody(); if (progText) progText.textContent = '阶段 3/6 · 完整解析'; return; }
  if (stage === 'latex_extras_rendered') { solveAcc.extras = content || ''; renderSolveBody(); return; }
  if (stage === 'mindmap_chunk') { solveAcc.mindmap = content || ''; renderSolveBody(); if (progText) progText.textContent = '阶段 5/6 · 思维导图'; return; }
  if (stage === 'mindmap') { solveAcc.mindmap = content || ''; renderSolveBody(); return; }
  if (stage === 'suggested_questions') {
    solveAcc.similar = parseQA(content);
    if (progText) progText.textContent = '阶段 6/6 · 预判问题';
    return;
  }
  if (stage === 'ai_usage') { solveAcc.usage = content || {}; renderSolveBody(); return; }
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
  if (solveAcc.steps) html += foldCard('📝 解题思路', '#00D2FF', `<div class="md" data-md></div>`, 'foldSteps', false);
  if (solveAcc.full) html += foldCard('📝 完整解析', '#7B2FBE', `<div class="md" data-md></div>`, 'foldFull', false);
  if (solveAcc.extras) html += foldCard('📐 图解辅助', '#00BCD4', `<div class="md" data-md></div>`, 'foldExtras', false);
  if (solveAcc.mindmap) html += foldCard('🗺️ 思维导图', '#00C853', `<pre style="white-space:pre-wrap;font-size:13px;line-height:1.8;font-family:inherit">${esc(solveAcc.mindmap)}</pre>`, 'foldMind', false);
  if (solveAcc.similar && solveAcc.similar.length) {
    let b = '';
    solveAcc.similar.forEach((x) => {
      b += `<div class="bubble q"><b>❓ ${esc(x.q)}</b>${x.a ? `<div class="muted" style="margin-top:6px">💡 ${esc(x.a)}</div>` : ''}</div>`;
    });
    html += foldCard('💬 预判问题', '#FFB74D', b, 'foldSim', false);
  }
  if (solveAcc.usage && solveAcc.usage.total_tokens) {
    const u = solveAcc.usage;
    html += `<div class="card"><div class="dim">⚙️ AI引擎：${esc(u.engine || 'DeepSeek')} ${esc(u.model || '')}<br>
      Tokens：输入 ${esc(u.prompt_tokens || 0)} · 输出 ${esc(u.completion_tokens || 0)} · 总计 ${esc(u.total_tokens || 0)}<br>
      <span style="color:#FB8C00">⚠️ 内容由 AI 生成，请仔细甄别</span></div></div>`;
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

function renderAskBox(rid) {
  const box = $('#askBox'); if (!box) return;
  box.innerHTML = `<div class="card">
      <div class="card-title">💬 继续追问</div>
      <div id="qaList"></div>
      <textarea id="askInput" placeholder="针对这道题继续提问…"></textarea>
      <button class="btn btn-primary btn-block" id="askSend" style="margin-top:10px">发送</button>
    </div>`;
  $('#askSend').onclick = async () => {
    const q = $('#askInput').value.trim(); if (!q) return;
    $('#askInput').value = '';
    const list = $('#qaList');
    list.insertAdjacentHTML('beforeend', `<div class="bubble q">${esc(q)}</div>`);
    const bid = 'ans' + Date.now();
    list.insertAdjacentHTML('beforeend', `<div class="bubble a" id="${bid}"><span class="dim">思考中…</span></div>`);
    list.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'end' });
    try {
      const r = await API.ask(rid, q, opts());
      const txt = r.answer || r.content || (r.status === 'error' ? ('错误：' + r.message) : '（无回答）');
      const el = document.getElementById(bid);
      renderMarkdownInto(el, txt);
      if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([el]).catch(() => {});
    } catch (e) {
      document.getElementById(bid).innerHTML = `<span style="color:#F44336">追问失败：${esc(e.message)}</span>`;
    }
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
    if (r.status !== 'processing' && r.status !== 'ok') { toast(r.message || '启动失败'); return; }
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
    <button class="fab-down hidden" id="fabDown" title="回到底部">↓</button>`;
  $('#bk').onclick = () => { S.view = 'home'; render(); };
  bindScrollFab();
}
function streamExtend(rid) {
  let acc = { summary: '', extension: '', similar: [], questions: [], image: '' };
  API.sse('/extend/stream/' + rid, {
    onEvent: (m) => {
      const st = m.stage, c = m.content, pt = $('#progText');
      if (st === 'info') { if (pt) pt.textContent = c || '处理中…'; }
      else if (st === 'ocr_complete') {
        const o = (typeof c === 'object' && c) ? c : {};
        acc.image = o.image_url || '';
        if (acc.image) $('#extImg').innerHTML = `<div class="card"><div class="card-title" style="color:#00D2FF">📷 原题</div><img class="preview-img" src="${acc.image}"></div>`;
      }
      else if (st === 'summary_chunk') { acc.summary = c || ''; renderExtend(acc); }
      else if (st === 'summary') { acc.summary = c || ''; renderExtend(acc); if (pt) pt.textContent = '知识点已总结'; }
      else if (st === 'similar_questions') { acc.similar = parseQA(c); renderExtend(acc); }
      else if (st === 'extension_chunk') { acc.extension = c || ''; renderExtend(acc); if (pt) pt.textContent = '正在生成知识拓展…'; }
      else if (st === 'extension') { acc.extension = c || ''; renderExtend(acc); if (pt) pt.textContent = '知识拓展已生成'; }
      else if (st === 'suggested_questions') { acc.questions = parseQA(c); renderExtend(acc); }
      else if (st === 'error') { toast('错误：' + c); if (pt) pt.textContent = '出错：' + c; }
    },
    onDone: () => { const p = $('.progress'); if (p) p.innerHTML = '<span class="dot" style="background:#4CAF50"></span><span>延伸完成</span>'; },
    onError: () => {}
  });
}
function renderExtend(acc) {
  const box = $('#extBody'); if (!box) return;
  let html = '';
  if (acc.summary) html += foldCard('📝 知识点总结', '#00D2FF', '<div class="md" data-md></div>', 'exSum', false);
  if (acc.extension) html += foldCard('🚀 知识拓展', '#7B2FBE', '<div class="md" data-md></div>', 'exExt', false);
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
    showAnimation(r.url);
  }).catch((e) => { overlay(false); toast('生成失败：' + e.message); });
}
function startAnimateText(text) {
  overlay('正在生成动画（可能需要 1-2 分钟）…');
  API.animateText(text, opts()).then((r) => {
    overlay(false);
    if (r.status !== 'ok') { toast(r.message || '生成失败'); return; }
    showAnimation(r.url);
  }).catch((e) => { overlay(false); toast('生成失败：' + e.message); });
}
function showAnimation(url) {
  S.view = 'solve';
  $('#view').innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">🎬 AI 动画</div></div>
    <iframe class="report-frame" src="${url}" style="height:calc(100vh - 52px - var(--tab-h) - 80px)"></iframe>`;
  $('#bk').onclick = () => { S.view = 'home'; render(); };
}

/* ==================== 历史 ==================== */
async function renderHistory(v) {
  v.innerHTML = `<div class="card"><div class="muted">正在加载历史记录…</div></div>`;
  try {
    const r = await API.history({});
    const list = r.records || [];
    if (!list.length) { v.innerHTML = `<div class="card"><div class="muted">还没有历史记录，先去首页拍一道题吧 📷</div></div>`; return; }
    let html = `<div class="card"><div class="row-between"><div class="card-title" style="margin:0">📚 历史记录</div>
      <span class="dim">共 ${list.length} 条</span></div></div>`;
    list.forEach((rec, i) => {
      const t = (rec.timestamp || '').replace('T', ' ').slice(0, 16);
      const typeLabel = rec.record_type === 'animation' ? '🎬 AI动画' : (rec.record_type === 'extension' ? '📎 知识延伸' : '📷 解题');
      html += `<div class="list-item" data-idx="${i}">
        <div class="t1">${typeLabel} ${rec.subject ? '· ' + esc(rec.subject) : ''}</div>
        <div class="t2"><span>${esc(t)}</span>${rec.difficulty ? `<span>难度 ${esc(rec.difficulty)}</span>` : ''}${rec.mastery_level ? `<span>${esc(rec.mastery_level)}</span>` : ''}</div>
      </div>`;
    });
    v.innerHTML = html;
    v.querySelectorAll('.list-item').forEach((el) => {
      el.onclick = () => renderHistoryDetail(list[+el.dataset.idx]);
    });
  } catch (e) {
    v.innerHTML = `<div class="card"><div class="muted" style="color:#F44336">加载失败：${esc(e.message)}</div></div>`;
  }
}
function renderHistoryDetail(rec) {
  S.view = 'solve';
  const t = (rec.timestamp || '').replace('T', ' ').slice(0, 16);
  let body = '';
  if (rec.image_url) body += `<div class="card"><div class="card-title" style="color:#00D2FF">📷 原题</div><img class="preview-img" src="${rec.image_url}"></div>`;
  if (rec.ocr_text) body += foldCard('🔍 识别文本', '#8A94A0', `<div class="md" data-md></div>`, 'hOcr', true);
  if (rec.solution_steps) body += foldCard('📝 解题思路', '#00D2FF', `<div class="md" data-md></div>`, 'hSteps', true);
  if (rec.full_solution) body += foldCard(rec.record_type === 'solve' ? '📝 完整解析' : '📎 内容', '#7B2FBE', `<div class="md" data-md></div>`, 'hFull', false);
  $('#view').innerHTML = `
    <div class="subbar"><button class="back" id="bk">← 返回</button><div class="ttl">${esc(t)}</div></div>
    <div id="hdBody">${body}</div>`;
  $('#bk').onclick = () => { S.view = 'history'; render(); };
  const m = { hOcr: rec.ocr_text, hSteps: rec.solution_steps, hFull: rec.full_solution };
  Object.keys(m).forEach((id) => {
    const el = $('#hdBody').querySelector('#' + id + ' [data-md]');
    if (el && m[id]) renderMarkdownInto(el, m[id]);
  });
  $('#hdBody').querySelectorAll('[data-fold]').forEach((s) => {
    s.onclick = () => { const tt = $('#hdBody').querySelector('#' + s.dataset.fold); tt.classList.toggle('hidden'); s.textContent = tt.classList.contains('hidden') ? '▼ 展开' : '▲ 收起'; };
  });
}

/* ==================== 报告 ==================== */
function renderReport(v) {
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
      <div class="row" style="margin-top:12px">
        <button class="btn btn-primary" id="btnData">数据版报告</button>
        <button class="btn btn-ghost" id="btnAi">AI 版报告</button>
      </div>
    </div>
    <div id="reportOut"></div>`;
  let days = 7;
  v.querySelectorAll('#rangeChips .chip').forEach((c) => {
    c.onclick = () => {
      days = +c.dataset.d;
      v.querySelectorAll('#rangeChips .chip').forEach((x) => x.classList.toggle('active', x === c));
    };
  });
  $('#btnData').onclick = async () => {
    overlay('正在生成数据报告…');
    try {
      const st = settings();
      const r = await API.dataReport(days, st.theme);
      overlay(false);
      if (r.status !== 'ok') { toast(r.message || '生成失败'); return; }
      $('#reportOut').innerHTML = `<iframe class="report-frame" src="${r.url}"></iframe>`;
    } catch (e) { overlay(false); toast('生成失败：' + e.message); }
  };
  $('#btnAi').onclick = async () => {
    overlay('AI 正在撰写学情报告…');
    try {
      const r = await API.aiReport(days, opts());
      overlay(false);
      if (r.status !== 'ok') { $('#reportOut').innerHTML = `<div class="card"><div class="muted" style="color:#FF9800">${esc(r.message || '生成失败')}</div></div>`; return; }
      $('#reportOut').innerHTML = `<div class="card"><div class="card-title">🤖 AI 学情报告</div><div class="md" id="aiRep"></div></div>`;
      renderMarkdownInto($('#aiRep'), r.report || '');
    } catch (e) { overlay(false); toast('生成失败：' + e.message); }
  };
}

/* ==================== 设置 ==================== */
function renderSettings(v) {
  const st = settings();
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
        <div class="chips" id="themeChips">
          <button class="chip ${st.theme === 'dark' ? 'active' : ''}" data-v="dark">深色</button>
          <button class="chip ${st.theme === 'light' ? 'active' : ''}" data-v="light">浅色</button>
        </div></div>
      <div class="setting-row"><span class="lbl">字号（全局）</span>
        <div class="chips" id="fontChips">
          ${[[12, '小'], [16, '默认'], [20, '大'], [24, '特大']].map(([v2, l]) => `<button class="chip ${st.fontSize === v2 ? 'active' : ''}" data-v="${v2}">${l}</button>`).join('')}
        </div></div>
    </div>
    <div class="card">
      <div class="card-title">🤖 AI 模型</div>
      <div class="setting-row"><span class="lbl">大语言模型</span>
        <select class="sel" id="selEngine">
          <option value="deepseek" ${st.engine === 'deepseek' ? 'selected' : ''}>DeepSeek</option>
          <option value="qwen" ${st.engine === 'qwen' ? 'selected' : ''}>千问 Qwen</option>
          <option value="doubao" ${st.engine === 'doubao' ? 'selected' : ''}>豆包 Doubao</option>
        </select></div>
      <div class="setting-row"><span class="lbl">OCR 模式</span>
        <select class="sel" id="selOcr">
          <option value="qwen" ${st.ocrMode === 'qwen' ? 'selected' : ''}>视觉模型</option>
          <option value="paddle" ${st.ocrMode === 'paddle' ? 'selected' : ''}>PaddleOCR 本地</option>
        </select></div>
    </div>
    <div class="card">
      <div class="card-title">📐 解题偏好</div>
      <div class="setting-row"><span class="lbl">思考模式</span>
        <div class="chips" id="thinkChips">
          <button class="chip ${st.thinking === 'off' ? 'active' : ''}" data-v="off">关闭</button>
          <button class="chip ${st.thinking === 'on' ? 'active' : ''}" data-v="on">开启</button>
          <button class="chip ${st.thinking === 'auto' ? 'active' : ''}" data-v="auto">自动</button>
        </div></div>
      <div class="setting-row"><span class="lbl">图解辅助</span>
        <div class="chips" id="latexChips">
          <button class="chip ${st.latexHelper === 'off' ? 'active' : ''}" data-v="off">关闭</button>
          <button class="chip ${st.latexHelper === 'on' ? 'active' : ''}" data-v="on">开启</button>
          <button class="chip ${st.latexHelper === 'auto' ? 'active' : ''}" data-v="auto">自动</button>
        </div></div>
      <div class="setting-row"><span class="lbl">题库搜题</span>
        <div class="chips" id="searchChips">
          <button class="chip ${st.searchEnabled ? 'active' : ''}" data-v="1">开启</button>
          <button class="chip ${!st.searchEnabled ? 'active' : ''}" data-v="0">关闭</button>
        </div></div>
      <div class="setting-row"><span class="lbl">回答风格</span>
        <select class="sel" id="selStyle">
          <option value="formal" ${st.style === 'formal' ? 'selected' : ''}>正式</option>
          <option value="encouraging" ${st.style === 'encouraging' ? 'selected' : ''}>鼓励</option>
          <option value="humorous" ${st.style === 'humorous' ? 'selected' : ''}>幽默</option>
        </select></div>
    </div>
    <div class="card">
      <div class="card-title">ℹ️ 关于</div>
      <div class="muted">学习助手 学生端（网页版）v2.1.2<br>服务端：${location.host || '本机'}</div>
    </div>`;

  const bindChips = (sel, key, cast) => {
    v.querySelectorAll(sel + ' .chip').forEach((c) => {
      c.onclick = () => {
        v.querySelectorAll(sel + ' .chip').forEach((x) => x.classList.toggle('active', x === c));
        saveSettings(key, cast ? cast(c.dataset.v) : c.dataset.v);
        if (key === 'theme') applyTheme(c.dataset.v);
        if (key === 'fontSize') applyFontSize(+c.dataset.v);
        toast('已保存');
      };
    });
  };
  bindChips('#themeChips', 'theme');
  bindChips('#fontChips', 'fontSize', Number);
  bindChips('#thinkChips', 'thinking');
  bindChips('#latexChips', 'latexHelper');
  bindChips('#searchChips', 'searchEnabled', (v2) => v2 === '1');
  $('#selEngine').onchange = (e) => { saveSettings('engine', e.target.value); toast('已保存'); };
  $('#selOcr').onchange = (e) => { saveSettings('ocrMode', e.target.value); toast('已保存'); };
  $('#selStyle').onchange = (e) => { saveSettings('style', e.target.value); toast('已保存'); };
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

/* ==================== 启动 ==================== */
(async function boot() {
  const st = settings();
  applyTheme(st.theme); applyFontSize(st.fontSize);
  const u = await API.verify();
  if (u) { S.user = u; enterApp(); }
  else { $('#authView').classList.remove('hidden'); }
})();

})();
