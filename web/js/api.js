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
    if (opts.style) h['X-Answer-Style'] = opts.style;
    if (opts.dialect) h['X-Dialect'] = encodeURIComponent(opts.dialect);
    if (opts.grade) h['X-Grade'] = encodeURIComponent(opts.grade);
    if (opts.thinking) h['X-Thinking'] = opts.thinking;
    if (opts.searchEnabled !== undefined) h['X-Search-Enabled'] = String(opts.searchEnabled);
    if (opts.latexHelper) h['X-Latex-Helper'] = opts.latexHelper;
    if (opts.personality) h['X-Personality'] = opts.personality;
    if (opts.detail) h['X-Detail'] = opts.detail;
    if (opts.subject) h['X-Subject'] = encodeURIComponent(opts.subject);
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
  async function confirmSolve(rid) { return req(`/solve/confirm/${rid}`, { method: 'POST' }); }
  async function selectQuestions(rid, indices) {
    return req(`/solve/select_questions/${rid}`, { method: 'POST', raw: { indices } });
  }
  async function cancelSolve(rid) { return req(`/solve/cancel/${rid}`, { method: 'POST' }); }

  /* ---------- 追问 ---------- */
  async function ask(sessionId, question, opts = {}) {
    const res = await fetch(BASE + '/ask', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ session_id: sessionId, question })
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
  async function dataReport(days, theme) {
    return req('/report/data', { method: 'POST', raw: { days, theme }, timeout: 180000 });
  }
  async function aiReport(days, opts = {}) {
    const res = await fetch(BASE + '/report/ai', {
      method: 'POST', headers: solveHeaders(Object.assign({ 'Content-Type': 'application/json' }, opts)),
      body: JSON.stringify({ days, theme: 'dark' })
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
    solveImage, solveText, confirmSolve, selectQuestions, cancelSolve, ask,
    sse, history, deleteHistory, dataReport, aiReport,
    extendImage, extendText, animateImage, animateText,
    mastery, exportDoc, health
  };
})();
