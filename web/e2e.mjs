/* 浏览器端 E2E：起一个本地静态服务 → 无头 Chrome 打开页面 → 选文件 → 点击切分 → 截图。
 * 用法：node web/e2e.mjs work_cmp/a3.pdf [截图输出路径]
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.E2E_DIR ? path.resolve(process.env.E2E_DIR) : HERE;  // 默认测 web/，可指向 tool/
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean).find((p) => existsSync(p));
if (!CHROME) throw new Error('找不到 Chrome');

const pdfPath = path.resolve(process.argv[2] || path.join(HERE, '..', 'work_cmp', 'a3.pdf'));
const shotPath = path.resolve(process.argv[3] || path.join(HERE, '..', 'work_cmp', 'web_e2e.png'));
if (!existsSync(pdfPath)) throw new Error('找不到 PDF: ' + pdfPath);

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.pdf': 'application/pdf' };
const httpPort = 8300 + Math.floor(Math.random() * 400);
const server = createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  const f = path.join(ROOT, u === '/' ? 'index.html' : u);
  try {
    if (!statSync(f).isFile()) throw new Error('not file');
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' });
    res.end(readFileSync(f));
  } catch {
    res.writeHead(404); res.end('404');
  }
});
await new Promise((r) => server.listen(httpPort, '127.0.0.1', r));

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.sessionId = null; this.logs = [];
    ws.addEventListener('message', (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) {
        this.logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => a.value ?? a.description ?? '').join(' '));
      }
      if (m.method === 'Runtime.exceptionThrown') {
        this.logs.push('exception: ' + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text));
      }
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      }
    });
  }
  send(method, params = {}, sessionId = this.sessionId) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      const msg = { id, method, params };
      if (sessionId) msg.sessionId = sessionId;
      this.ws.send(JSON.stringify(msg));
    });
  }
}

const port = 9400 + Math.floor(Math.random() * 300);
const profile = path.join(os.tmpdir(), 'wb-e2e-' + Date.now());
const child = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--disable-extensions',
  '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
  '--window-size=1100,1500', 'about:blank',
], { stdio: 'ignore' });

let ws, cdp;
try {
  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); if (r.ok) { ver = await r.json(); break; } } catch {}
    await sleep(250);
  }
  if (!ver) throw new Error('调试端口未就绪');
  ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', () => rej(new Error('ws 失败')), { once: true }); });
  cdp = new CDP(ws);

  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  cdp.sessionId = sessionId;
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('DOM.enable');

  if (process.env.E2E_WIDTH) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: parseInt(process.env.E2E_WIDTH, 10), height: 900, deviceScaleFactor: 2, mobile: true,
    });
  }
  const page = process.env.E2E_PAGE || 'index.html';
  const url = process.env.E2E_URL || `http://127.0.0.1:${httpPort}/${page}`;
  console.log('打开:', url);
  await cdp.send('Page.navigate', { url });
  // 等文档真正就绪（内联版 HTML 有 2MB，不能只按固定时长等）
  let ready = false;
  for (let i = 0; i < 120; i++) {
    try {
      const st = await cdp.send('Runtime.evaluate', {
        expression: 'document.readyState === "complete" && !!document.getElementById("file")',
        returnByValue: true,
      });
      if (st.result && st.result.value) { ready = true; break; }
    } catch { /* 导航中 */ }
    await sleep(250);
  }
  if (!ready) throw new Error('页面未就绪（可能 403 或脚本未加载）');
  await sleep(300);

  const ev = async (expr) => {
    const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面异常: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };

  console.log('依赖检查:', await ev('({pdfjs: !!window.pdfjsLib, lib: !!window.PDFLib, core: typeof window.createSplitter})'));

  // 选文件
  const { root } = await cdp.send('DOM.getDocument', { depth: 1 });
  const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: 'input#file' });
  if (!nodeId) throw new Error('找不到 input#file');
  await cdp.send('DOM.setFileInputFiles', { files: [pdfPath], nodeId });

  // 等分析 + 预览渲染完成（按钮出现即代表 refresh() 走完）
  let cards = 0;
  for (let i = 0; i < 200; i++) {
    const st = await ev(`(()=>{const b=document.getElementById('goBtn');
      return {cards: document.querySelectorAll('.pg').length, ready: b && !b.classList.contains('hidden')}})()`);
    cards = st.cards;
    if (st.ready) break;
    await sleep(200);
  }
  await sleep(400);
  cards = await ev('document.querySelectorAll(".pg").length');
  console.log('分析出的 A3 页数:', cards);
  const info = await ev(`[...document.querySelectorAll('.pg-head span')].map(s=>s.textContent.trim())`);
  console.log('每页信息:', info.slice(0, 2));

  // 点切分
  await ev('document.getElementById("goBtn").click()');
  let done = null;
  for (let i = 0; i < 200; i++) {
    done = await ev(`(()=>{const o=document.querySelector('#outHost .ok-box');const a=document.querySelector('#outHost a[download]');
      return o?a.textContent+'||'+(a?a.download:'')+'||'+document.querySelectorAll('#outHost .out-strip canvas').length:null})()`);
    if (done) break;
    await sleep(150);
  }
  console.log('结果:', done);

  // 等结果缩略图渲染完（ok-box 先出现，缩略图与按钮复位都是异步的）
  let thumbs = 0;
  for (let i = 0; i < 80; i++) {
    const st = await ev(`({t: document.querySelectorAll("#outHost .out-strip canvas").length,
      idle: !document.getElementById('goBtn').disabled})`);
    thumbs = st.t;
    if (thumbs >= cards * 2 && st.idle) break;
    await sleep(200);
  }
  console.log('结果缩略图页数:', thumbs, '（应为', cards * 2, '）');

  // 手动微调切口：把第 1 页滑块推到 +8pt，再切一次，看参数里是否跟着变
  const moved = await ev(`(()=>{const r=document.querySelector('.pg input[type=range]');
    r.value=String(parseFloat(r.value)+8); r.dispatchEvent(new Event('input',{bubbles:true}));
    return {now: document.querySelector('.pg-head .cutval').textContent}})()`);
  await ev('document.getElementById("goBtn").click()');
  const first = await ev('(document.querySelector("#outHost pre.report")||{}).textContent || ""');
  let report = null;
  for (let i = 0; i < 150; i++) {
    report = await ev('(document.querySelector("#outHost pre.report")||{}).textContent || null');
    if (report && report !== first) break;
    await sleep(200);
  }
  console.log('滑块调整到:', moved.now, '｜参数首行:', report && report.split('\n')[0]);

  console.log('页面错误:', cdp.logs.length ? cdp.logs : '无');

  // 全页截图
  const metrics = await cdp.send('Page.getLayoutMetrics');
  const h = Math.ceil(metrics.cssContentSize.height);
  const { data } = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: Math.ceil(metrics.cssContentSize.width), height: h, scale: 1 },
  });
  const fs = await import('node:fs');
  fs.writeFileSync(shotPath, Buffer.from(data, 'base64'));
  console.log('截图:', shotPath, (h + 'px 高'));

  if (!done) process.exitCode = 2;
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
  server.close();
}
