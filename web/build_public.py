"""生成可部署目录 pub/：一个 app 承载「服务介绍」与「在线工具」两个页面。

    /           服务介绍（代切服务）
    /tool.html  在线切割工具

两个页面都打成**完全自包含的单文件**：JS 全部内联、图片转 data URI。
原因是托管侧对不带 sharecode 的子资源请求会返回 403，相对路径的 js/png 会加载失败；
单文件只有一次 HTML 请求，既避开这个问题，也让老师可以直接下载这一个文件离线双击使用。

源文件改动后重新执行：python web/build_public.py
"""
import base64
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DST = os.path.join(ROOT, 'pub')


def read(p):
    return open(p, encoding='utf-8').read()


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


def main():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)

    # ---------- 1) 在线工具 → tool.html ----------
    tool = read(os.path.join(HERE, 'index.html'))

    # worker 源码内联成 text/plain，页面脚本会用 Blob 起一个 worker
    worker = read(os.path.join(ROOT, 'vendor', 'pdf.worker.min.js'))
    if '</script' in worker.lower():
        raise SystemExit('内联失败：pdf.worker.min.js 里含有 </script')
    tool = tool.replace(
        '</head>',
        '<script type="text/plain" id="pdfWorkerSrc">\n' + worker + '\n</script>\n</head>', 1)

    tool = inline_scripts(tool, [
        ('vendor/pdf.min.js', os.path.join(ROOT, 'vendor', 'pdf.min.js')),
        ('vendor/pdf-lib.min.js', os.path.join(ROOT, 'vendor', 'pdf-lib.min.js')),
        ('split_core.js', os.path.join(HERE, 'split_core.js')),
    ])
    if re.search(r'<script src=', tool):
        raise SystemExit('还有没内联的外链脚本')
    with open(os.path.join(DST, 'tool.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(tool)

    # ---------- 2) 服务介绍 → index.html ----------
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

    # ---------- 3) 404 页（静态托管的 ErrorDocument） ----------
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

    # ---------- 4) 自检 ----------
    for name in ('index.html', 'tool.html', '404.html'):
        p = os.path.join(DST, name)
        text = read(p)
        ext = re.findall(r'<script[^>]+src=', text) + re.findall(r'<link[^>]+href="(?!data:)', text)
        print('%-12s %6.2f MB  外链资源 %d 处' % (
            name, os.path.getsize(p) / 1024 / 1024, len(ext)))
    extra = [f for f in os.listdir(DST) if f not in ('index.html', 'tool.html', '404.html')]
    if extra:
        print('注意：pub/ 里还有其它文件', extra)
    return 0


if __name__ == '__main__':
    sys.exit(main())
