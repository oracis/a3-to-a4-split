/* 真实浏览器加载测量：给一个 URL，输出导航时序 + 各资源耗时。
 * 用新 profile，保证是冷缓存首访。
 * 用法：node web/measure.mjs <url> [标签]
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
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
const label = process.argv[3] || url;
if (!url) throw new Error('用法: node web/measure.mjs <url> [label]');

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

const port = 9500 + Math.floor(Math.random() * 300);
const profile = path.join(os.tmpdir(), 'wb-measure-' + Date.now());
const child = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--disable-extensions',
  '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
  '--window-size=1100,900', 'about:blank',
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
  await cdp.send('Network.enable');

  await cdp.send('Page.navigate', { url });
  for (let i = 0; i < 200; i++) {
    const r = await cdp.send('Runtime.evaluate', {
      expression: 'document.readyState === "complete"', returnByValue: true,
    }).catch(() => null);
    if (r && r.result && r.result.value) break;
    await sleep(150);
  }
  await sleep(500);

  const out = await cdp.send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => {
      const n = performance.getEntriesByType('navigation')[0] || {};
      const res = performance.getEntriesByType('resource').map(e => ({
        name: e.name.split('/').pop().slice(0, 46),
        type: e.initiatorType,
        start: Math.round(e.startTime),
        done: Math.round(e.responseEnd),
        dur: Math.round(e.duration),
        size: e.transferSize,
      }));
      return {
        ttfb: Math.round(n.responseStart || 0),
        domReady: Math.round(n.domContentLoadedEventEnd || 0),
        load: Math.round(n.loadEventEnd || 0),
        htmlBytes: n.transferSize || 0,
        res,
      };
    })()`,
  });
  const d = out.result.value;

  console.log('\n■ ' + label);
  console.log('  HTML 首字节      %s ms   (传输 %s KB)', d.ttfb, (d.htmlBytes / 1024).toFixed(1));
  console.log('  DOM 就绪         %s ms', d.domReady);
  console.log('  load 完成        %s ms', d.load);
  if (d.res.length) {
    console.log('  资源：');
    for (const r of d.res) {
      console.log('    %s 启动 %s ms  完成 %s ms  耗时 %s ms  %s KB',
        r.name.padEnd(34),
        String(r.start).padStart(5), String(r.done).padStart(5),
        String(r.dur).padStart(5), (r.size / 1024).toFixed(1));
    }
    const total = d.res.reduce((s, r) => s + r.size, 0);
    console.log('  资源合计 %s KB', (total / 1024).toFixed(1));
  } else {
    console.log('  资源：无外部请求（单文件）');
  }
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
}
