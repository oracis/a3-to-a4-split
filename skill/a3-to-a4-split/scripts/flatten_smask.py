# -*- coding: utf-8 -*-
"""
把 PDF 里「RGB 图 + 透明蒙版 SMask」预合成到纯白底，并去掉 SMask。

Word 导出 PDF 时会把公式/OLE 对象写成带软蒙版的透明位图。部分 PDF 预览器
（浏览器内置、低 DPI 缩放）对这种图做 alpha 合成时会在笔画周围产生灰色光晕，
看上去就是「浅灰底框 + 文字发虚 + 重影」。预合成到不透明白底后，任何渲染器
得到的都是确定的实心黑字，显示和打印一致清晰。

用增量更新只覆盖相关 Image 对象，其余内容（文字、矢量分数线）原样不动。

用法: python flatten_smask.py <输入.pdf> [输出.pdf]
"""
import re
import zlib
import os
import sys


def parse_objects(data):
    """返回 {对象号: 对象体 bytes}，同号多次定义时取最后一次（增量更新）。"""
    starts = [(m.start(), m.end(), int(m.group(1)))
              for m in re.finditer(rb'(?<!\d)(\d+)\s+0\s+obj\b', data)]
    objs = {}
    for i, (s, e, num) in enumerate(starts):
        end = data.find(b'endobj', e)
        if end < 0:
            continue
        objs[num] = data[e:end].strip()
    return objs


def split_body(b):
    head, sep, tail = b.partition(b'stream')
    if not sep:
        return b, None
    raw = tail.lstrip(b'\r\n').rstrip(b'\r\n')
    return head.strip(), raw


def incremental(data, defs, size=None, prev=None, root=None, info=None, ids=b''):
    trailer = re.findall(rb'trailer\s*<<(.*?)>>', data, re.S)[-1]
    if size is None:
        size = int(re.search(rb'/Size\s+(\d+)', trailer).group(1))
    if root is None:
        root = re.search(rb'/Root\s+(\d+)', trailer).group(1).decode()
    if prev is None:
        prev = int(re.findall(rb'startxref\s+(\d+)', data)[-1])
    inf = re.search(rb'/Info\s+(\d+)', trailer)
    info_part = ('/Info %s 0 R' % (info or inf.group(1).decode())) if (info or inf) else ''
    if not ids:
        m = re.search(rb'/ID\s*\[.*?\]', trailer, re.S)
        ids = m.group(0) if m else b''

    out = bytearray(data)
    offsets = {}
    for num, body in defs:
        offsets[num] = len(out)
        out += str(num).encode() + b' 0 obj\r\n' + body + b'\r\nendobj\r\n'
    xref_off = len(out)
    groups = []
    for n in sorted(offsets):
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    out += b'xref\r\n'
    for g in groups:
        out += ('%d %d\r\n' % (g[0], len(g))).encode()
        for n in g:
            out += ('%010d 00000 n\r\n' % offsets[n]).encode()
    out += ('trailer\r\n<</Size %d/Root %s 0 R%s/Prev %d%s>>\r\n' %
            (size, root, info_part, prev, ids.decode('latin1'))).encode('latin1')
    out += b'startxref\r\n' + str(xref_off).encode() + b'\r\n%%EOF\r\n'
    return bytes(out)


def flatten(src, dst=None):
    if dst is None:
        dst = os.path.splitext(src)[0] + '-无蒙版.pdf'
    data = open(src, 'rb').read()
    objs = parse_objects(data)

    defs, done = [], []
    for num in sorted(objs):
        dic, raw = split_body(objs[num])
        if raw is None or b'/Subtype/Image' not in dic:
            continue
        sm = re.search(rb'/SMask\s+(\d+)\s+0\s+R', dic)
        if not sm:
            continue
        smn = int(sm.group(1))
        smdic, smraw = split_body(objs[smn])
        if smraw is None:
            continue
        w = int(re.search(rb'/Width\s+(\d+)', dic).group(1))
        h = int(re.search(rb'/Height\s+(\d+)', dic).group(1))
        try:
            rgb = zlib.decompress(raw)
            mask = zlib.decompress(smraw)
        except Exception as e:
            print('跳过 obj%d: 解压失败 %s' % (num, e))
            continue
        if len(rgb) < w * h * 3 or len(mask) < w * h:
            print('跳过 obj%d: 数据长度不符 %d/%d 期望 %d' % (num, len(rgb), len(mask), w*h*3))
            continue

        out = bytearray(w * h * 3)
        for i in range(w * h):
            a = mask[i] / 255.0
            base = i * 3
            out[base] = int(rgb[base] * a + 255 * (1 - a))
            out[base+1] = int(rgb[base+1] * a + 255 * (1 - a))
            out[base+2] = int(rgb[base+2] * a + 255 * (1 - a))
        comp = zlib.compress(bytes(out), 9)

        nd = re.sub(rb'/SMask\s+\d+\s+0\s+R', b'', dic)
        nd = re.sub(rb'/Matte\s*\[[^\]]*\]', b'', nd)
        nd = re.sub(rb'/Interpolate\s+\w+', b'/Interpolate true', nd)
        nd, n = re.subn(rb'/Length\s+\d+', b'/Length %d' % len(comp), nd)
        if n == 0:
            print('跳过 obj%d: 找不到 /Length' % num)
            continue
        defs.append((num, nd + b'\r\nstream\r\n' + comp + b'\r\nendstream'))
        done.append((num, smn, w, h, len(comp)))

    if not defs:
        raise RuntimeError('没有找到带 SMask 的图像对象')
    result = incremental(data, defs)
    open(dst, 'wb').write(result)
    print('输出:', dst)
    print('预合成对象数:', len(done))
    for num, smn, w, h, ln in done:
        print('  obj %-4d (蒙版 obj %-4d) %4dx%-4d -> 白底不透明, 新流 %d 字节' % (num, smn, w, h, ln))
    print('剩余 /SMask 引用:', result.count(b'/SMask'))
    return dst


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    flatten(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
