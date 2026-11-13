#!/usr/bin/env node
/**
 * 把 PDF 渲染成 PNG + 抽取文本坐标，供「肉眼验收」用。
 *
 * 为什么需要它：切分是否切错（比如把「千克」的「千」切到下一页）、
 * 页边是否残留孤立单字，只看文件大小和页数是看不出来的，必须看像素。
 *
 * 用法:
 *   node render_pages.mjs <文件.pdf> <输出目录> [--scale 2] [--pdfjs <pdf.js地址>]
 *
 * 输出:
 *   <输出目录>/p1.png, p2.png ...
 *   <输出目录>/text.json   每页文本项 [{t, x, y}]，y 为 PDF 用户坐标（左下原点）
 *
 * 依赖: Node 22+（内置 WebSocket）、本机 Chrome 或 Edge。
 * 环境变量 CHROME_PATH 可指定浏览器路径。
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean);

function findChrome() {
  for (const p of CHROME_CANDIDATES) if (existsSync(p)) return p;
  throw new Error('找不到 Chrome/Edge，请用 CHROME_PATH 环境变量指定');
}

function parseArgs(argv) {
  const a = { scale: 2, pdfjs: null, files: [] };
  const rest = argv.slice(2);
  for (let i = 0; i < rest.length; i++) {
    const s = rest[i];
    if (s === '--scale') a.scale = parseFloat(rest[++i]);
    else if (s === '--pdfjs') a.pdfjs = rest[++i];
    else a.files.push(s);
  }
  if (a.files.length < 2) throw new Error('需要两个参数: <pdf> <输出目录>');
  [a.pdf, a.outDir] = a.files;
  return a;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.sessionId = null;
    ws.addEventListener('message', (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) rej(new Error(JSON.stringify(m.error)));
        else res(m.result);
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

async function main() {
  const args = parseArgs(process.argv);
  const pdfPath = path.resolve(args.pdf);
  if (!existsSync(pdfPath)) throw new Error('找不到 PDF: ' + pdfPath);
  const outDir = path.resolve(args.outDir);
  mkdirSync(outDir, { recursive: true });

  const chrome = findChrome();
  const port = 9333 + Math.floor(Math.random() * 200);
  const profile = path.join(os.tmpdir(), 'pdfraster-' + Date.now());
  const child = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--disable-extensions', '--allow-file-access-from-files',
    '--remote-debugging-port=' + port,
    '--user-data-dir=' + profile,
    'about:blank',
  ], { stdio: 'ignore' });

  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/json/version`);
        if (r.ok) { ver = await r.json(); break; }
      } catch { /* 还没起来 */ }
      await sleep(250);
    }
    if (!ver) throw new Error('浏览器调试端口未就绪（端口 ' + port + '）');

    const ws = new WebSocket(ver.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', () => rej(new Error('WebSocket 连接失败')), { once: true });
    });
    cdp = new CDP(ws);

    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    cdp.sessionId = sessionId;
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');

    const q = new URLSearchParams();
    if (args.pdfjs) q.set('pdfjs', args.pdfjs);
    const renderUrl = pathToFileURL(path.join(HERE, 'render.html')).href
      + (q.toString() ? '?' + q.toString() : '');
    await cdp.send('Page.navigate', { url: renderUrl });
    await sleep(600);

    const evalJs = async (expr) => {
      const r = await cdp.send('Runtime.evaluate', {
        expression: expr, returnByValue: true, awaitPromise: true,
      });
      if (r.exceptionDetails) {
        const d = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
        throw new Error('页面脚本异常: ' + d);
      }
      return r.result.value;
    };

    let ready = false;
    for (let i = 0; i < 80; i++) {
      const st = await evalJs('({r: !!window.__ready, e: window.__error || null})');
      if (st.e) throw new Error(st.e);
      if (st.r) { ready = true; break; }
      await sleep(250);
    }
    if (!ready) throw new Error('pdf.js 加载超时');

    const pdfUrl = pathToFileURL(pdfPath).href;
    const pages = await evalJs(
      `window.__render(${JSON.stringify(pdfUrl)}, ${args.scale})`);

    const texts = [];
    pages.forEach((pg) => {
      const b64 = pg.png.split(',')[1];
      const file = path.join(outDir, 'p' + pg.page + '.png');
      writeFileSync(file, Buffer.from(b64, 'base64'));
      texts.push({ page: pg.page, width: pg.width, height: pg.height, items: pg.text });
      console.log(`第${pg.page}页 ${pg.width}x${pg.height} -> ${file}`);
    });
    writeFileSync(path.join(outDir, 'text.json'),
      JSON.stringify(texts, null, 1), 'utf8');
    console.log('文本坐标 -> ' + path.join(outDir, 'text.json'));
  } finally {
    try { if (cdp) cdp.ws.close(); } catch { /* ignore */ }
    try { child.kill(); } catch { /* ignore */ }
    await sleep(300);
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

main().catch((e) => {
  console.error('失败:', e.message);
  process.exit(1);
});
