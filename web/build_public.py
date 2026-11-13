"""生成可部署目录 pub/。

    /               服务介绍（代切服务）
    /tool.html      在线切割工具（拆分版，依赖放 vendor/，可按需加载）
    /offline.html   同一个工具的**单文件离线版**，下载后双击即可用，不需要网络
    /vendor/*.js    三个依赖，文件名带内容指纹，可设一年强缓存
    /404.html       静态托管 ErrorDocument

为什么要拆：pdf-lib 压缩后 202KB、pdf.js 主包 87KB、worker 285KB，原来全部压进
tool.html，首屏一次性要下 585KB。其中 pdf-lib 只在拖入文件后分析时才用得上，
pdf.js 的 worker 也要等第一次解析 PDF 才加载。拆出来以后首屏只剩 HTML 本身
（约 12KB gzip），工具页打开速度不再受这三个大包拖累，二次访问还能走强缓存。

离线单文件版仍然保留：有些老师要在没有网络的电脑上批量处理，`offline.html`
一个文件拷过去就能用。

源文件改动后重新执行：python web/build_public.py
"""
import base64
import hashlib
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DST = os.path.join(ROOT, 'pub')
VENDOR = os.path.join(ROOT, 'vendor')

# 工具真正用到的三个依赖；vendor/ 里的 FileSaver、jszip 页面没引用，不发布
DEPS = ('pdf.min.js', 'pdf.worker.min.js', 'pdf-lib.min.js')


def read(p):
    return open(p, encoding='utf-8').read()


def md5_8(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def inline_scripts(html, pairs):
    """把 <script src="x"></script> 换成内联内容"""
    for src, path in pairs:
        code = read(path)
        if '</script' in code.lower():
            raise SystemExit('内联失败：%s 里含有 </script' % path)
        tag = '<script src="%s"></script>' % src
        if tag not in html:
            raise SystemExit('内联失败：页面里找不到 %s' % tag)
        html = html.replace(tag, '<script>\n' + code + '\n</script>')
    return html


def emit_vendor(dst_dir):
    """复制依赖到 pub/vendor/，文件名带内容指纹；返回 {原路径: 新路径}"""
    out = os.path.join(dst_dir, 'vendor')
    os.makedirs(out, exist_ok=True)
    mapping = {}
    for name in DEPS:
        src = os.path.join(VENDOR, name)
        if not os.path.isfile(src):
            raise SystemExit('缺少依赖：%s' % src)
        stem, ext = os.path.splitext(name)          # pdf.min + .js
        hashed = '%s.%s%s' % (stem, md5_8(src), ext)  # pdf.min.a1b2c3d4.js
        shutil.copyfile(src, os.path.join(out, hashed))
        mapping['vendor/' + name] = 'vendor/' + hashed
    return mapping


def main():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)

    src_tool = read(os.path.join(HERE, 'index.html'))
    src_core = os.path.join(HERE, 'split_core.js')
    mapping = emit_vendor(DST)

    # ---------- 1) 拆分版 tool.html ----------
    tool = inline_scripts(src_tool, [('split_core.js', src_core)])
    for old, new in mapping.items():
        tool = tool.replace(old, new)
    left = re.findall(r'<script[^>]+src="([^"]+)"', tool)
    for s in left:
        if not s.startswith('vendor/'):
            raise SystemExit('还有没内联的外链脚本：%s' % s)
        if not os.path.isfile(os.path.join(DST, s)):
            raise SystemExit('引用了不存在的依赖：%s' % s)
    with open(os.path.join(DST, 'tool.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(tool)

    # ---------- 2) 单文件离线版 offline.html ----------
    for name in ('pdf.worker.min.js', 'pdf-lib.min.js'):
        code = read(os.path.join(VENDOR, name))
        if '</script' in code.lower():
            raise SystemExit('内联失败：%s 里含有 </script' % name)
    worker = read(os.path.join(VENDOR, 'pdf.worker.min.js'))
    pdflib = read(os.path.join(VENDOR, 'pdf-lib.min.js'))

    off = src_tool
    # pdf-lib 内联成普通脚本：window.PDFLib 一开始就有，ensureSplitter 会跳过动态加载
    # worker 内联成 text/plain，页面脚本用 Blob 起一个 worker（file:// 下也能跑）
    off = off.replace(
        '</head>',
        '<script>\n' + pdflib + '\n</script>\n'
        '<script type="text/plain" id="pdfWorkerSrc">\n' + worker + '\n</script>\n</head>', 1)
    off = inline_scripts(off, [
        ('vendor/pdf.min.js', os.path.join(VENDOR, 'pdf.min.js')),
        ('split_core.js', src_core),
    ])
    if re.search(r'<script[^>]+src=', off):
        raise SystemExit('离线版还有没内联的外链脚本')
    with open(os.path.join(DST, 'offline.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(off)

    # ---------- 3) 服务介绍 ----------
    page = read(os.path.join(ROOT, '服务介绍.html'))
    img = open(os.path.join(ROOT, 'assets', 'compare.png'), 'rb').read()
    data_uri = 'data:image/png;base64,' + base64.b64encode(img).decode('ascii')
    if 'src="assets/compare.png"' not in page:
        raise SystemExit('服务页里找不到 assets/compare.png')
    page = page.replace('src="assets/compare.png"', 'src="' + data_uri + '"')
    if re.search(r'(src|href)="(?!data:|https?:|mailto:|#)[^"]+\.(png|jpg|css|js)"', page):
        raise SystemExit('服务页还有外部本地资源没内联')
    with open(os.path.join(DST, 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(page)

    # ---------- 4) 404 页 ----------
    notfound = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>页面不存在 · 试卷 A3 拆 A4</title>
<style>
  :root{--ink:#1a2233;--ink-3:#6b7688;--accent:#2563eb;--line:#e3e7ee;--bg:#f6f8fb;}
  *{box-sizing:border-box;}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       padding:32px 20px;background:var(--bg);color:var(--ink);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
       -webkit-font-smoothing:antialiased;}
  .card{width:100%;max-width:520px;background:#fff;border:1px solid var(--line);border-radius:16px;
        padding:40px 36px;box-shadow:0 10px 32px rgba(20,35,70,.06);}
  .code{font-size:52px;font-weight:700;letter-spacing:.02em;color:var(--accent);
        font-variant-numeric:tabular-nums;line-height:1;margin-bottom:14px;}
  h1{font-size:19px;margin:0 0 10px;}
  p{margin:0 0 26px;font-size:14.5px;line-height:1.75;color:var(--ink-3);}
  .acts{display:flex;flex-wrap:wrap;gap:10px;}
  a.btn{display:inline-block;padding:10px 18px;border-radius:9px;font-size:14.5px;
        text-decoration:none;font-weight:500;}
  a.pri{background:var(--accent);color:#fff;}
  a.sec{border:1px solid var(--line);color:var(--ink);}
  .tip{margin-top:24px;padding-top:18px;border-top:1px solid var(--line);
       font-size:12.5px;color:#98a2b3;line-height:1.7;}
</style>
</head>
<body>
  <div class="card">
    <div class="code">404</div>
    <h1>这个地址没有页面</h1>
    <p>可能是链接少了一段，或者文件名拼错了。你要找的两样东西都还在：</p>
    <div class="acts">
      <a class="btn pri" href="index.html">回首页</a>
      <a class="btn sec" href="tool.html">打开在线切割工具</a>
    </div>
    <div class="tip">在线工具完全在浏览器里运行，试卷不会上传到任何服务器。</div>
  </div>
</body>
</html>
"""
    with open(os.path.join(DST, '404.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(notfound)

    # ---------- 5) 自检 ----------
    for name in ('index.html', 'tool.html', 'offline.html', '404.html'):
        p = os.path.join(DST, name)
        ext = re.findall(r'<script[^>]+src="([^"]+)"', read(p))
        print('%-14s %7.1f KB  外链 %s' % (
            name, os.path.getsize(p) / 1024, ', '.join(ext) if ext else '无'))
    vend = sorted(os.listdir(os.path.join(DST, 'vendor')))
    for v in vend:
        print('  vendor/%-28s %7.1f KB' % (v, os.path.getsize(os.path.join(DST, 'vendor', v)) / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
