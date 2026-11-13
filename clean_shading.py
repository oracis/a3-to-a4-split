# -*- coding: utf-8 -*-
"""
清除 Word 文档里的图案/网点底纹（w:shd，尤其是 w:val="pct10"）。

这类 10% 网点底纹在屏幕上会与文字笔画叠成一片浅灰，看起来就是
「灰底 + 文字发虚 + 重影」，打印时也容易脏。删掉后文字恢复纯矢量渲染。

只改 word/document.xml，其余部件（公式 OLE、图片、样式、关系）原样保留，
输出到新文件，不覆盖原文档。

用法: python clean_shading.py <源.docx> [输出.docx]
"""
import re
import sys
import os
import zipfile
import shutil


def clean(src, dst=None):
    if dst is None:
        dst = os.path.splitext(src)[0] + '-清晰版.docx'

    zin = zipfile.ZipFile(src, 'r')
    xml = zin.read('word/document.xml').decode('utf-8')

    pat = re.compile(r'<w:shd\b[^>]*(?:/>|>.*?</w:shd>)', re.S)
    hits = pat.findall(xml)
    kinds = {}
    for h in hits:
        v = re.search(r'w:val="([^"]+)"', h)
        kinds[v.group(1) if v else '?'] = kinds.get(v.group(1) if v else '?', 0) + 1

    out = pat.sub('', xml)

    # 顺手清掉空的 rPr / pPr（不留无意义空节点）
    out = re.sub(r'<w:rPr>\s*</w:rPr>', '', out)

    zout = zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED)
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == 'word/document.xml':
            data = out.encode('utf-8')
        zout.writestr(item, data)
    zout.close()
    zin.close()

    print('输出:', dst)
    print('移除底纹:', len(hits), '处', kinds)
    print('剩余 w:shd:', out.count('<w:shd'))
    return dst


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    clean(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
