/* 免费额度专项测试：3 次放行 → 第 4 次拦截 → 续额后恢复。
 * 用法：
 *   node web/test_quota.mjs                        # 测 web/index.html（开发态）
 *   E2E_DIR=pub E2E_PAGE=tool.html node web/test_quota.mjs   # 测真正上线的单文件
 * 参数：[PDF 路径] [截图输出路径]
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.E2E_DIR ? path.resolve(HERE, '..', process.env.E2E_DIR) : HERE;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean).find((p) => existsSync(p));
if (!CHROME) throw new Error('找不到 Chrome');

const pdfPath = path.resolve(process.argv[2] || path.join(HERE, '..', 'work_cmp', 'a3.pdf'));
const shotPath = path.resolve(process.argv[3] || path.join(HERE, '..', 'work_cmp', 'quota_modal.png'));
if (!existsSync(pdfPath)) throw new Error('找不到 PDF: ' + pdfPath);

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
const httpPort = 8800 + Math.floor(Math.random() * 300);
const server = createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  const f = path.join(ROOT, u === '/' ? 'index.html' : u);
  try {
    if (!statSync(f).isFile()) throw new Error('nf');
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f)] || 'application/octet-stream' });
    res.end(readFileSync(f));
  } catch {
    res.writeHead(404); res.end('404');
  }
});
await new Promise((r) => server.listen(httpPort, '127.0.0.1', r));
// E2E_URL 非空则测远端真实站点，本地服务只作兜底（不会被访问）
const REMOTE = (process.env.E2E_URL || '').trim();

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

const port = 9800 + Math.floor(Math.random() * 150);
const profile = path.join(os.tmpdir(), 'wb-quota-' + Date.now());
const child = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--disable-extensions',
  '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
  '--window-size=1100,1400', 'about:blank',
], { stdio: 'ignore' });

let ws, cdp, fails = 0;
const check = (ok, label, extra = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + label + (extra ? '  ' + extra : ''));
  if (!ok) fails++;
};

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
  cdp = new CDP(ws);

  // 一律拒绝真实落盘，避免污染下载目录；监听器在事件阶段就已经跑过了
  try { await cdp.send('Browser.setDownloadBehavior', { behavior: 'deny' }, undefined); } catch {}

  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  cdp.sessionId = sessionId;
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('DOM.enable');

  const page = process.env.E2E_PAGE || 'index.html';
  const url = REMOTE || `http://127.0.0.1:${httpPort}/${page}`;
  console.log('打开:', url);

  const ev = async (expr) => {
    const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面异常: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };

  const waitReady = async () => {
    for (let i = 0; i < 160; i++) {
      try {
        const v = await ev('document.readyState === "complete" && !!document.getElementById("file") && !!document.getElementById("payMask")');
        if (v) return true;
      } catch {}
      await sleep(250);
    }
    throw new Error('页面未就绪');
  };

  const state = () => ev(`({
    used: localStorage.getItem('a3split.used'),
    left: (document.getElementById('quotaLeft')||{}).textContent,
    pillZero: (document.getElementById('quotaPill')||{}).className || '',
    maskHidden: document.getElementById('payMask').classList.contains('hidden')
  })`);

  await cdp.send('Page.navigate', { url });
  await waitReady();

  // ---------- 0) 清空额度，确认初始态 ----------
  console.log('\n[0] 初始态');
  await ev('localStorage.removeItem("a3split.used")');
  await cdp.send('Page.reload');
  await waitReady();
  await sleep(400);
  let s = await state();
  check(s.left === '3', '徽标显示剩余 3 次', `left=${s.left}`);
  check(s.maskHidden === true, '引导层默认隐藏');

  // ---------- 选文件 + 分析 ----------
  const { root } = await cdp.send('DOM.getDocument', { depth: 1 });
  const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: 'input#file' });
  if (!nodeId) throw new Error('找不到 input#file');
  await cdp.send('DOM.setFileInputFiles', { files: [pdfPath], nodeId });

  let cards = 0;
  for (let i = 0; i < 220; i++) {
    const st = await ev(`(()=>{const b=document.getElementById('goBtn');
      return {n: document.querySelectorAll('.pg').length, ready: b && !b.classList.contains('hidden')}})()`);
    cards = st.n;
    if (st.ready) break;
    await sleep(200);
  }
  check(cards > 0, '已分析出 A3 页面', `${cards} 页`);

  const split = async () => {
    await ev('document.getElementById("goBtn").click()');
    for (let i = 0; i < 220; i++) {
      const ok = await ev('!!document.querySelector("#outHost a[download]") && !document.getElementById("goBtn").disabled');
      if (ok) return true;
      await sleep(150);
    }
    return false;
  };

  // ---------- 1~3) 前三次下载应放行 ----------
  console.log('\n[1] 免费额度内（3 次应全部放行）');
  for (let i = 1; i <= 3; i++) {
    const r = await split();
    if (!r) throw new Error('第 ' + i + ' 次切分没出下载链接');
    await ev('document.querySelector("#outHost a[download]").click()');
    await sleep(350);
    s = await state();
    check(s.used === String(i), `第 ${i} 次下载已计入`, `used=${s.used} left=${s.left}`);
    check(s.maskHidden === true, `第 ${i} 次未被拦截`);
  }

  s = await state();
  check(s.left === '0', '额度归零', `left=${s.left}`);
  check(s.pillZero.includes('zero'), '徽标变红提示', `class="${s.pillZero}"`);

  // ---------- 4) 第四次应被拦截 ----------
  console.log('\n[2] 超额（第 4 次应被拦截）');
  const r4 = await split();
  if (!r4) throw new Error('第 4 次切分没出下载链接（切分本身不该被锁）');
  check(r4 === true, '超额后仍可切分、只是拿不走文件');
  const thumbs = await ev('document.querySelectorAll("#outHost .out-strip canvas").length');
  check(thumbs > 0, '超额后结果缩略图照样渲染', `${thumbs} 张`);

  await ev('document.querySelector("#outHost a[download]").click()');
  await sleep(450);
  s = await state();
  check(s.maskHidden === false, '引导层已弹出');
  check(s.used === '3', '拦截时不重复计次', `used=${s.used}`);
  const modalText = await ev(`document.querySelector('#payMask .sheet').innerText.replace(/\\s+/g,' ')`);
  check(modalText.includes('jieshizhe'), '弹层里有微信号');
  check(/微信/.test(modalText) && /再给我/.test(modalText), '弹层里有续额入口');
  console.log('    文案:', modalText.slice(0, 120) + '…');

  // 截图留档
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 720, height: 780, deviceScaleFactor: 2, mobile: false });
  await sleep(300);
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(shotPath, Buffer.from(data, 'base64'));
  console.log('    截图:', shotPath);
  await cdp.send('Emulation.clearDeviceMetricsOverride');

  // ---------- 5) 续额 ----------
  console.log('\n[3] 续额');
  await ev('document.getElementById("grantMore").click()');
  await sleep(350);
  s = await state();
  check(s.maskHidden === true, '续额后弹层关闭');
  check(s.left === '3', '额度恢复 3 次', `left=${s.left}`);
  check(s.pillZero === 'quota', '徽标恢复正常色');

  // 恢复后再下载一次，应放行
  await ev('document.querySelector("#outHost a[download]").click()');
  await sleep(350);
  s = await state();
  check(s.left === '2', '续额后可继续下载', `left=${s.left}`);

  // ---------- 6) 复制按钮 ----------
  console.log('\n[4] 复制微信号');
  await ev(`document.getElementById('payMask').classList.remove('hidden')`);
  await ev('document.getElementById("copyWx").click()');
  await sleep(300);
  const copyTxt = await ev('document.getElementById("copyWx").textContent');
  check(copyTxt === '已复制', '复制后有反馈', `文案="${copyTxt}"`);

  console.log('\n页面错误:', cdp.logs.length ? cdp.logs : '无');
  if (cdp.logs.length) fails++;
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
  server.close();
}

console.log(fails === 0 ? '\n全部通过 ✅' : `\n${fails} 项未通过 ❌`);
if (fails) process.exitCode = 1;
