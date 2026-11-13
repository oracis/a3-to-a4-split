/* 只截页面底部一屏（看页脚/尾部落款用，整页太长时 shot.mjs 不便目视）。
 * 用法：node web/shot_bottom.mjs <url> <输出.png> [视口宽=900]
 */
import { spawn } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe']
  .filter(Boolean).find((p) => existsSync(p));

const url = process.argv[2];
const out = path.resolve(process.argv[3] || 'bottom.png');
const width = parseInt(process.argv[4] || '900', 10);
const port = 9500 + Math.floor(Math.random() * 400);
const profile = path.join(os.tmpdir(), 'wb-b-' + Date.now());

const child = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox',
  '--no-first-run', '--disable-extensions', '--remote-debugging-port=' + port,
  '--user-data-dir=' + profile, '--window-size=' + width + ',1200',
  '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });

let ws;
try {
  let ver = null;
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); if (r.ok) { ver = await r.json(); break; } } catch {}
    await sleep(250);
  }
  ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', rej, { once: true }); });
  let id = 0; const pend = new Map();
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) { const { res, rej } = pend.get(m.id); pend.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  });
  const send = (method, params = {}, sessionId = null) => new Promise((res, rej) => {
    const i = ++id; pend.set(i, { res, rej });
    const msg = { id: i, method, params }; if (sessionId) msg.sessionId = sessionId; ws.send(JSON.stringify(msg));
  });

  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  await send('Page.enable', {}, sessionId);
  await send('Runtime.enable', {}, sessionId);
  await send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 2, mobile: width < 500 }, sessionId);
  await send('Page.navigate', { url }, sessionId);
  for (let i = 0; i < 120; i++) {
    const r = await send('Runtime.evaluate', { expression: 'document.readyState === "complete"', returnByValue: true }, sessionId).catch(() => null);
    if (r && r.result && r.result.value) break;
    await sleep(200);
  }
  await send('Runtime.evaluate', { expression: 'window.scrollTo(0, document.body.scrollHeight)' }, sessionId);
  await sleep(700);
  const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
  writeFileSync(out, Buffer.from(data, 'base64'));
  console.log('底部截图:', out);
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
}
