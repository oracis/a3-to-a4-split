# -*- coding: utf-8 -*-
"""
本地服务：让网页版工具可以直接上传 Word（.doc/.docx），自动切成 4 页 A4 可打印 PDF。

启动:
    python serve.py                 # 默认 http://127.0.0.1:8765/
    python serve.py --port 9000
    python serve.py --no-browser    # 不自动开浏览器

浏览器只能渲染 PDF/图片，不能排版 Word。所以这个服务做两件事：
  1. 把 index.html 及 vendor/ 当静态站点提供；
  2. 接收 POST /api/docx（请求体就是 docx 原始字节），
     在本机调 Word 排版导出 PDF → 去网点底纹 → 预合成透明蒙版 → 按两栏真实边界切 4 页 A4，
     直接把成品 PDF 作为响应返回。

全程 127.0.0.1 本地回环，不上传任何外部服务器；临时文件用完即删。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = 80 * 1024 * 1024      # 80MB 上限


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def convert_docx_to_a4pdf(raw_bytes, filename, workdir):
    """docx 字节 -> 4 页 A4 PDF 字节。失败抛 RuntimeError（消息可直接展示给用户）。"""
    ext = os.path.splitext(filename)[1].lower() or '.docx'
    src = os.path.join(workdir, '1-src' + ext)
    with open(src, 'wb') as f:
        f.write(raw_bytes)

    # ---- 1. 清网点底纹 ----
    cleaned = os.path.join(workdir, '2-clean' + ext)
    rc, out = run([sys.executable, os.path.join(HERE, 'clean_shading.py'), src, cleaned])
    cur = cleaned if rc == 0 and os.path.exists(cleaned) else src
    log('  清底纹: %s' % (out.strip().splitlines()[-2:] if rc == 0 else '跳过'))

    # ---- 2. Word 排版导出 PDF ----
    import split_word
    pdf1 = os.path.join(workdir, '3-word.pdf')
    ok, msg = split_word.docx_to_pdf(cur, pdf1, workdir)
    log('  Word: %s' % msg)
    if not ok:
        raise RuntimeError('调用本机 Word 失败：' + str(msg)
                           + '（请确认装了 Word / WPS，且没有弹窗占用）')

    # ---- 3. 透明蒙版预合成 ----
    pdf2 = os.path.join(workdir, '4-flat.pdf')
    rc, out = run([sys.executable, os.path.join(HERE, 'flatten_smask.py'), pdf1, pdf2])
    cur_pdf = pdf2 if rc == 0 and os.path.exists(pdf2) else pdf1
    log('  去蒙版: %s' % ('完成' if cur_pdf == pdf2 else '跳过'))

    # ---- 4. 切分 ----
    out_pdf = os.path.join(workdir, '5-a4.pdf')
    rc, out = run([sys.executable, os.path.join(HERE, 'split_a3.py'), cur_pdf, out_pdf])
    if rc != 0 or not os.path.exists(out_pdf):
        raise RuntimeError('切分失败：' + out.strip()[:400])
    log('  切分: ' + ' / '.join(l.strip() for l in out.strip().splitlines()[-4:]))

    with open(out_pdf, 'rb') as f:
        return f.read()


class Handler(SimpleHTTPRequestHandler):
    server_version = 'PaperSplit/1.0'

    def log_message(self, fmt, *args):
        log('%s %s' % (self.address_string(), fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith('/api/docx'):
            self._json(404, {'error': '未知接口'})
            return
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            self._json(400, {'error': '没有收到文件内容'})
            return
        if n > MAX_UPLOAD:
            self._json(413, {'error': '文件超过 80MB 上限'})
            return

        raw = self.rfile.read(n)
        name = urllib.parse.unquote(self.headers.get('X-Filename') or 'upload.docx')
        log('收到 %s (%.1f MB)，开始处理' % (name, len(raw) / 1048576.0))
        t0 = time.time()

        workdir = tempfile.mkdtemp(prefix='upload_')
        try:
            pdf = convert_docx_to_a4pdf(raw, name, workdir)
        except Exception as e:
            log('失败: %s' % e)
            traceback.print_exc()
            self._json(500, {'error': str(e)})
            return
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        log('完成，用时 %.1f 秒，输出 %.1f KB' % (time.time() - t0, len(pdf) / 1024.0))
        self.send_response(200)
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Length', str(len(pdf)))
        self.send_header('X-Elapsed', '%.1f' % (time.time() - t0))
        self.end_headers()
        self.wfile.write(pdf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    handler = partial(Handler, directory=HERE)
    port = args.port
    srv = None
    for p in range(args.port, args.port + 6):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), handler)
            port = p
            break
        except OSError:
            print('端口 %d 被占用，换一个…' % p, flush=True)
    if srv is None:
        print('8765~8770 都被占用了，请用 --port 指定其它端口')
        return
    url = 'http://127.0.0.1:%d/' % port
    print('=' * 56)
    print(' A3 试卷分割工具 · 本地服务已启动')
    print(' 打开:', url)
    print(' 现在可以直接上传 .doc/.docx，会自动调 Word 转成 4 页 A4 PDF')
    print(' 停止: Ctrl+C')
    print('=' * 56, flush=True)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
