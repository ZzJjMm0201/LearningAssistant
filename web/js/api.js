/* ==================== API 封装（同源调用） ==================== */
const API = (() => {
  const BASE = '';                      // 同源
  let token = localStorage.getItem('la_token') || '';
  let user = JSON.parse(localStorage.getItem('la_user') || 'null');

  function headers(extra = {}) {
    const h = Object.assign({}, extra);
    if (token) h['Authorization'] = 'Bearer ' + token;
    return h;
  }

  async function req(path, { method = 'GET', body, raw, form, timeout = 120000 } = {}) {
    const opt = { method, headers: headers() };
    if (form) {
      opt.body = form;                       // FormData：不要设置 Content-Type
    } else if (raw !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(raw);
    }
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    opt.signal = ctl.signal;
    let res;
    try {
      res = await fetch(BASE + path, opt);
    } finally { clearTimeout(timer); }
    const ct = res.headers.get('content-type') || '';
    let data = null;
    if (ct.includes('application/json')) data = await res.json();
    else data = await res.text();
    if (!res.ok) {
      const msg = (data && data.detail) ? data.detail : (data && data.message) || ('HTTP ' + res.status);
      const e = new Error(msg); e.status = res.status; e.data = data; throw e;
    }
    return data;
  }

  /* ---------- 认证 ---------- */
  async function login(username, password) {
    const r = await req('/auth/login', { method: 'POST', raw: { username, password } });
    token = r.data.token; user = r.data.user;
    localStorage.setItem('la_token', token);
    localStorage.setItem('la_user', JSON.stringify(user));
    return user;
  }
  async function register(username, password) {
    const r = await req('/auth/register', { method: 'POST', raw: { username, password } });
    token = r.data.token; user = r.data.user;
    localStorage.setItem('la_token', token);
    localStorage.setItem('la_user', JSON.stringify(user));
    return user;
  }
  async function verify() {
    if (!token) return null;
    try {
      const r = await req('/auth/verify', { method: 'POST', raw: { token } });
      user = r.user; localStorage.setItem('la_user', JSON.stringify(user));
      return user;
    } catch (e) { logout(); return null; }
  }
  function logout() {
    token = ''; user = null;
    localStorage.removeItem('la_token'); localStorage.removeItem('la_user');
  }

  /* ---------- 解题 ---------- */
  function solveHeaders(opts = {}) {
    const h = {};
    if (opts.engine) h['X-Engine'] = opts.engine;
    if (opts.llmModel) h['X-LLM-Model'] = opts.llmModel;
    if (opts.ocrMode) h['X-OCR-Mode'] = opts.ocrMode;
    if (opts.visionModel) h['X-Vision-Model'] = opts.visionModel;
    // ② 回答风格：服务端正式读的是 X-Style。历史版本误写成 X-Answer-Style，
    //    导致“正式/鼓励/幽默”设了也从来没传到过服务端（两个都发，兼容旧服务端）。
    if (opts.style) { h['X-Style'] = opts.style; h['X-Answer-Style'] = opts.style; }
    if (opts.dialect) h['X-Dialect'] = encodeURIComponent(opts.dialect);
    if (opts.grade) h['X-Grade'] = encodeURIComponent(opts.grade);
    if (opts.thinking) h['X-Thinking'] = opts.thinking;
    if (opts.searchEnabled !== undefined) h['X-Search-Enabled'] = String(opts.searchEnabled);
    if (opts.latexHelper) h['X-Latex-Helper'] = opts.latexHelper;
    if (opts.personality) h['X-Personality'] = opts.personality;
    if (opts.detail) h['X-Detail'] = opts.detail;
    if (opts.subject) h['X-Subject'] = encodeURIComponent(opts.subject);
    // 11.1 动画等“生成时固化配色”的内容需要知道当前主题
    if (opts.theme) h['X-Theme'] = opts.theme;
    // ⑯ 边解答边设问：开启时才发（服务端默认关闭）
    if (opts.interactiveQuiz) h['X-Interactive-Quiz'] = '1';
    return headers(h);
  }

  async function solveImage(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'photo.jpg');
    const res = await fetch(BASE + '/solve', { method: 'POST', body: fd, headers: solveHeaders(opts) });
    return res.json();
  }
  async function solveText(text, opts = {}) {
    const res = await fetch(BASE + '/solve/text', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ text })
    });
    return res.json();
  }
  /* 长按多页拍摄：上传一段连续拍摄的视频，服务端抽关键帧→逐页OCR→合并分题 */
  async function solveMultipage(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'mulipage.webm');
    const res = await fetch(BASE + '/solve/multipage', {
      method: 'POST', body: fd, headers: solveHeaders(opts), timeout: 300000
    });
    return res.json();
  }
  async function confirmSolve(rid) { return req(`/solve/confirm/${rid}`, { method: 'POST' }); }
  async function selectQuestions(rid, indices) {
    return req(`/solve/select_questions/${rid}`, { method: 'POST', raw: { indices } });
  }
  async function cancelSolve(rid) { return req(`/solve/cancel/${rid}`, { method: 'POST' }); }

  /* ---------- 追问 ---------- */
  async function ask(sessionId, question, opts = {}, context = null) {
    const res = await fetch(BASE + '/ask', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      // context：该会话在服务端没有对话上下文时（如知识延伸/动画历史记录）用正文兜底
      body: JSON.stringify({ session_id: sessionId, question: question, context: context })
    });
    return res.json();
  }

  /* ---------- SSE（通用读取器） ---------- */
  function sse(path, { onEvent, onDone, onError, signal } = {}) {
    const src = new EventSource(BASE + path);
    // 服务端以 data: {json} 形式下发，无 event: 名
    src.onmessage = (ev) => {
      if (!ev.data) return;
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.stage === 'complete') { try { src.close(); } catch (e) {} onDone && onDone(m); return; }
      onEvent && onEvent(m);
    };
    src.onerror = (e) => {
      try { src.close(); } catch (_) {}
      onError && onError(e);
    };
    if (signal) signal.addEventListener('abort', () => { try { src.close(); } catch (e) {} });
    return src;
  }

  /* ---------- POST-SSE 读取器（EventSource 只能 GET，报告等接口是 POST）
     注意：服务端在“题量不足 / 无记录”时会直接 return JSON（HTTP 200，非 SSE）。
     以前这种响应会被当成流逐行找 'data:'，一行都找不到 → 静默 onDone(null)，
     客户端 acc 为空 → 界面只显示“报告内容为空”，真正的原因（题不够 5 道）被吞掉。
     现在：先看 Content-Type 与首个非空字符，是 JSON 就解析出 message 当错误上报。 ---------- */
  async function postSSE(path, body, { onEvent, onDone, onError } = {}, opts = {}) {
    try {
      const res = await fetch(BASE + path, {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, solveHeaders(opts)),
        body: JSON.stringify(body || {})
      });
      if (!res.ok || !res.body) {
        let msg = 'HTTP ' + res.status;
        try { const j = await res.json(); msg = j.detail || j.message || msg; } catch (e) {}
        onError && onError(new Error(msg));
        return;
      }
      const ct = res.headers.get('content-type') || '';
      // 非 SSE：服务端用 JSON 告知失败原因（题量不足等）
      if (!ct.includes('text/event-stream')) {
        let msg = '生成失败';
        try { const j = await res.json(); msg = j.message || j.detail || msg; } catch (e) {}
        onError && onError(new Error(msg));
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder('utf-8');
      let buf = '';
      let sawAnyData = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (!line.startsWith('data:')) continue;
          let m; try { m = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
          sawAnyData = true;
          if (m.stage === 'complete') { onDone && onDone(m); return; }
          onEvent && onEvent(m);
        }
      }
      // 流结束但从没收到过任何 data: —— 当作失败上报，不要静默成功
      if (!sawAnyData) {
        onError && onError(new Error('服务端未返回任何内容'));
        return;
      }
      onDone && onDone(null);
    } catch (e) {
      onError && onError(e);
    }
  }

  /* ---------- 历史 ---------- */
  async function history(filters = {}) {
    return req('/history', { method: 'POST', raw: Object.assign({
      start_date: '', end_date: '', subject: [], grade: [], difficulty: [], mastery: []
    }, filters) });
  }
  async function deleteHistory(ids) {
    if (ids.length === 1) return req(`/history/${ids[0]}`, { method: 'DELETE' });
    return req('/history/batch-delete', { method: 'POST', raw: { record_ids: ids } });
  }

  /* ---------- 报告 ---------- */
  async function dataReport(days, theme, opts = {}) {
    // ③ 报告筛选：年级/学科通过请求头传给服务端（空=不限）
    return req('/report/data', {
      method: 'POST', raw: { days, theme, grade: opts.grade || '', subject: opts.subject || '' },
      timeout: 180000
    });
  }
  async function reportMistakes(limit = 10) {
    // 🧭 最近易错点梳理：汇总该用户近期记录的 easy_mistakes
    return req('/report/mistakes', { method: 'POST', raw: { limit } });
  }
  async function aiReport(days, opts = {}) {
    const res = await fetch(BASE + '/report/ai', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ days, theme: 'dark', grade: opts.grade || '', subject: opts.subject || '' })
    });
    return res.json();
  }

  /* ---------- 知识延伸 ---------- */
  async function extendImage(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'photo.jpg');
    const res = await fetch(BASE + '/extend', { method: 'POST', body: fd, headers: solveHeaders(opts) });
    return res.json();
  }
  async function extendText(text, opts = {}) {
    const res = await fetch(BASE + '/extend/text', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ text })
    });
    return res.json();
  }

  /* ---------- 历史导出 ---------- */
  async function exportHistory(title, content, format) {
    return req('/export', { method: 'POST', raw: { title: title, content: content, format: format || 'word' }, timeout: 180000 });
  }

  /* ---------- AI动画 ---------- */
  async function animateImage(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'photo.jpg');
    const res = await fetch(BASE + '/animation', { method: 'POST', body: fd, headers: solveHeaders(opts) });
    return res.json();
  }
  async function animateText(text, opts = {}) {
    const res = await fetch(BASE + '/animation/text', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ text })
    });
    return res.json();
  }

  /* ---------- ⑰ AI 批注 ---------- */
  async function annotateImage(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'photo.jpg');
    // 批注通道跟 OCR 模式一致（qwen=视觉模型直出；paddle=取坐标投给LLM）
    const res = await fetch(BASE + '/annotate', { method: 'POST', body: fd, headers: solveHeaders(opts), timeout: 180000 });
    return res.json();
  }

  /* ---------- 其它 ---------- */
  async function mastery(requestId, level) {
    return req('/mastery', { method: 'POST', raw: { request_id: requestId, mastery_level: level } });
  }
  async function exportDoc(title, content, format) {
    return req('/export', { method: 'POST', raw: { title, content, format }, timeout: 180000 });
  }
  async function health() { return req('/health'); }

  return {
    req, login, register, verify, logout,
    getToken: () => token, getUser: () => user,
    solveImage, solveText, solveMultipage, confirmSolve, selectQuestions, cancelSolve, ask,
    sse, postSSE, history, deleteHistory, dataReport, aiReport, reportMistakes,
    extendImage, extendText, animateImage, animateText, annotateImage,
    mastery, exportDoc, exportHistory, health
  };
})();
