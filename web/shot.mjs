/* 通用整页截图：任意 URL（本地文件或用 http 起服务）→ 全页 PNG。
 * 用法：node web/shot.mjs <url> <输出.png> [视口宽=900]
 */
import { spawn } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean).find((p) => existsSync(p));
if (!CHROME) throw new Error('找不到 Chrome');

const url = process.argv[2];
const out = path.resolve(process.argv[3] || 'shot.png');
const width = parseInt(process.argv[4] || '900', 10);
if (!url) throw new Error('用法: node web/shot.mjs <url> <out.png> [width]');

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.sessionId = null;
    ws.addEventListener('message', (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
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

const port = 9100 + Math.floor(Math.random() * 400);
const profile = path.join(os.tmpdir(), 'wb-shot-' + Date.now());
const child = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--disable-extensions',
  '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
  '--window-size=' + width + ',1200', '--hide-scrollbars', 'about:blank',
], { stdio: 'ignore' });

let ws;
try {
  let ver = null;
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); if (r.ok) { ver = await r.json(); break; } } catch {}
    await sleep(250);
  }
  if (!ver) throw new Error('调试端口未就绪');
  ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', () => rej(new Error('ws 失败')), { once: true });
  });
  const cdp = new CDP(ws);
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  cdp.sessionId = sessionId;
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width, height: 1200, deviceScaleFactor: 2, mobile: width < 500,
  });

  await cdp.send('Page.navigate', { url });
  for (let i = 0; i < 120; i++) {
    const r = await cdp.send('Runtime.evaluate', {
      expression: 'document.readyState === "complete"', returnByValue: true,
    }).catch(() => null);
    if (r && r.result && r.result.value) break;
    await sleep(200);
  }
  await sleep(600);

  const m = await cdp.send('Page.getLayoutMetrics');
  const w = Math.ceil(m.cssContentSize.width);
  const h = Math.ceil(m.cssContentSize.height);
  const { data } = await cdp.send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: w, height: h, scale: 1 },
  });
  writeFileSync(out, Buffer.from(data, 'base64'));
  console.log('截图:', out, `${w}×${h}`);
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
}
