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

    # ---------- 3) 自检 ----------
    for name in ('index.html', 'tool.html'):
        p = os.path.join(DST, name)
        text = read(p)
        ext = re.findall(r'<script[^>]+src=', text) + re.findall(r'<link[^>]+href="(?!data:)', text)
        print('%-12s %6.2f MB  外链资源 %d 处' % (
            name, os.path.getsize(p) / 1024 / 1024, len(ext)))
    if os.listdir(DST) != ['index.html', 'tool.html']:
        print('注意：pub/ 里还有其它文件', os.listdir(DST))
    return 0


if __name__ == '__main__':
    sys.exit(main())
