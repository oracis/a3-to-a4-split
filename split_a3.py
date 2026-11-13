# -*- coding: utf-8 -*-
"""
把 A3 横向 PDF（420x297mm）拆成左右两张标准 A4 竖版（210x297mm）。

关键点：这类「两页 A4 拼一版 A3」的文件，左右两栏并不一定对称居中。
实测常见错位：左栏内容框 84.6~619.4、右栏 624.1~1167.0。
若在页面正中间（1190.64/2 = 595.32）一刀切，会把左栏最后 1~2 个字
（比如「千克」的「千」）切到右页去，左页读起来就成了「多少克」。

做法：
1. 从内容流里识别左右两栏真实的内容框（那一圈页边框线）；
2. 以「左栏右边框与右栏左边框的中点」为切口，两页各自保留自己的边框；
3. 每栏按「A4 宽 595.32pt、以该栏内容框为中心」重设 MediaBox，原点归零；
4. 给该页内容流先平移、再套一个只覆盖本栏的裁剪框，邻页溢出的内容被裁掉；
5. 顺带清掉页边上黑色实心小方块（装订/裁切标记）与左侧「姓名学号」竖条。

全程 PDF 增量更新，不栅格化，字体/公式/图片矢量无损。

用法:
    python split_a3.py <输入.pdf> [输出.pdf]
    python split_a3.py in.pdf out.pdf --keep-marks           # 保留黑色裁切标记
    python split_a3.py in.pdf out.pdf --keep-strip           # 保留左侧姓名学号竖条
    python split_a3.py in.pdf out.pdf --mark-size 28.2x14.1  # 只认这个尺寸的标记
"""
import os
import re
import sys
import zlib

A4_W = 595.32          # A4 宽（pt），恰好是 A3 横向宽度的一半

# 黑色实心小方块的默认尺寸区间（pt）。装订/裁切标记一般 20~40 x 10~20。
MARK_W = (14.0, 70.0)
MARK_H = (5.0, 45.0)
FILL_OPS = {b'f', b'f*', b'F', b'B', b'B*', b'b', b'b*'}
STROKE_OPS = {b'S', b's', b'B', b'B*', b'b', b'b*'}


# ---------- 基础工具 ----------
def find_obj(data, num):
    m = re.search(rb'(?<!\d)' + str(num).encode() + rb' 0 obj(.*?)endobj', data, re.S)
    if not m:
        raise RuntimeError('找不到对象 %d 0 obj' % num)
    return m.group(1).strip()


def get_boxes(body):
    m = re.search(rb'/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]', body)
    if not m:
        raise RuntimeError('页面对象里没有 MediaBox')
    return tuple(float(v) for v in m.groups())


def fmt_box(x0, y0, x1, y1):
    return ('[ %.2f %.2f %.2f %.2f]' % (x0, y0, x1, y1)).encode()


def set_boxes(body, value, parent):
    body, n1 = re.subn(rb'/MediaBox\s*\[[^\]]*\]', b'/MediaBox' + value, body)
    if n1 == 0:
        raise RuntimeError('MediaBox 替换失败')
    body = re.sub(rb'/CropBox\s*\[[^\]]*\]', b'/CropBox' + value, body)
    body, n2 = re.subn(rb'/Parent\s+\d+\s+0\s+R', b'/Parent ' + str(parent).encode() + b' 0 R', body)
    if n2 == 0:
        body = body.replace(b'<<', b'<</Parent ' + str(parent).encode() + b' 0 R', 1)
    return body


def get_stream(data, body):
    """取内容流解压后的明文"""
    m = re.search(rb'stream\r?\n(.*?)\r?\nendstream', body, re.S)
    if not m:
        return b''
    raw = m.group(1)
    head = body.split(b'stream')[0]
    if b'FlateDecode' in head:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return b''
    return raw


# ---------- 黑色裁切标记 ----------
RECT_OP_RE = re.compile(rb'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+re\s*([A-Za-z*]+)')


def _inside_any(x, y, w, h, frames, tol=2.0):
    for fx, fy, fw, fh in frames:
        if (x >= fx - tol and y >= fy - tol
                and x + w <= fx + fw + tol and y + h <= fy + fh + tol):
            return True
    return False


def strip_black_marks(stream, frames=None, size=None):
    """删掉正文框外的黑色实心小方块（装订/裁切标记）。

    判据：填充型小矩形（默认 14~70 x 5~45pt），且不被任何一栏内容框完整包含
    —— 标记都画在页边空白里；正文里真正的题号框、答题格是**描边**而非填充，
    且落在内容框内，因此不会被误删。

    frames: [(x, y, w, h), ...] 内容框；为 None 时只按尺寸判断。
    size:   (w, h) 固定尺寸，只删这一种。
    返回 (新流, 删除的方块个数)。
    """
    if size:
        wlo = whi = float(size[0])
        hlo = hhi = float(size[1])
    else:
        wlo, whi = MARK_W
        hlo, hhi = MARK_H

    spans, filled = [], set()
    for m in RECT_OP_RE.finditer(stream):
        if m.group(5) not in FILL_OPS:
            continue
        x, y, w, h = (float(m.group(i)) for i in (1, 2, 3, 4))
        if not (wlo - .01 <= w <= whi + .01 and hlo - .01 <= h <= hhi + .01):
            continue
        if frames and _inside_any(x, y, w, h, frames):
            continue
        filled.add((round(x, 2), round(y, 2), round(w, 2), round(h, 2)))
        spans.append((m.start(), m.end()))
    if not filled:
        return stream, 0

    # 同一矩形上的描边指令（标记常写成「填充 + 同位置描边」）一并删掉，
    # 但只删坐标完全一致的，避免误伤正文细线。
    for m in RECT_OP_RE.finditer(stream):
        if m.group(5) not in STROKE_OPS:
            continue
        key = tuple(round(float(m.group(i)), 2) for i in (1, 2, 3, 4))
        if key in filled:
            spans.append((m.start(), m.end()))

    spans.sort()
    out, last = bytearray(), 0
    for s, e in spans:
        if s < last:
            last = max(last, e)
            continue
        out += stream[last:s]
        last = e
    out += stream[last:]
    return bytes(out), len(filled)


# ---------- 栏位识别 ----------
def detect_columns(stream):
    """从内容流里识别左右两栏的 x 范围与内容框矩形。

    返回 ([(minX, maxX), (minX, maxX)], [(x, y, w, h), ...])，识别失败返回 (None, rects)
    """
    rects = []
    for m in re.finditer(rb'(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re', stream):
        x, y, w, h = (float(v) for v in m.groups())
        # 只保留「一栏那么大」的矩形：比正文块大、比整页小
        if 150 < w < 700 and 250 < h < 900 and x >= -5:
            rects.append((x, y, w, h))
    if len(rects) < 2:
        return None, rects

    rects.sort(key=lambda r: r[0] + r[2] / 2.0)
    centers = [r[0] + r[2] / 2.0 for r in rects]
    gaps = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
    gap, idx = max(gaps)
    if gap < 30:          # 整页只有一栏（普通 A3 文书），退回中线切分
        return None, rects
    left = rects[:idx + 1]
    right = rects[idx + 1:]
    cols = [(min(r[0] for r in left), max(r[0] + r[2] for r in left)),
            (min(r[0] for r in right), max(r[0] + r[2] for r in right))]
    return cols, rects


def detect_margin_strip(stream, frame_min, frame_max):
    """识别贴在左侧页边距里的竖条（姓名/学号、考场座次这类长条图片）。

    判据：一条窄而高的图片，右边界落在正文框左边界之外。
    返回它的右边界（pt），没有则 None。
    """
    right = None
    for m in re.finditer(rb'([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+) '
                         rb'([\d.\-]+) ([\d.\-]+) cm', stream):
        tail = stream[m.end():m.end() + 160]
        if not re.search(rb'/\w+\s+Do', tail):
            continue
        a, b, c, d, e, f = (float(m.group(i)) for i in range(1, 7))
        w, h = abs(a), abs(d)
        if 10 < w < 90 and 300 < h < 820 and (e + w) <= frame_min + 6:
            right = max(right or 0, e + w)
    return right


def make_stream_obj(num, payload):
    comp = zlib.compress(payload, 9)
    head = ('%d 0 obj\r\n<</Length %d/Filter/FlateDecode>>\r\nstream\r\n'
            % (num, len(comp))).encode()
    return head + comp + b'\r\nendstream\r\nendobj\r\n'


def split(src, dst=None, keep_marks=False, keep_strip=False, mark_size=None):
    if dst is None:
        dst = os.path.splitext(src)[0] + '-A4分割.pdf'
    data = open(src, 'rb').read()

    trailer = re.findall(rb'trailer\s*<<(.*?)>>', data, re.S)[-1]
    size = int(re.search(rb'/Size\s+(\d+)', trailer).group(1))
    root = re.search(rb'/Root\s+(\d+)\s+0\s+R', trailer).group(1).decode()
    prev = int(re.findall(rb'startxref\s+(\d+)', data)[-1])

    pages_num, kids = None, []
    for m in re.finditer(rb'(\d+) 0 obj\s*<<(.*?)>>\s*endobj', data, re.S):
        body = m.group(2)
        if b'/Type/Pages' in body or b'/Type /Pages' in body:
            pages_num = int(m.group(1))
            kids = [int(v) for v in re.findall(rb'(\d+)\s+0\s+R',
                                              re.search(rb'/Kids\s*\[([^\]]*)\]', body).group(1))]
            break
    if not kids:
        raise RuntimeError('没有找到页树的 Kids')

    new_id = size
    defs, streams, new_kids = [], [], []
    report = []

    for pnum in kids:
        pbody = find_obj(data, pnum)
        x0, y0, x1, y1 = get_boxes(pbody)
        cont = int(re.search(rb'/Contents\s+(\d+)', pbody).group(1))
        cbody = find_obj(data, cont)
        cstream = get_stream(data, cbody)

        cols, frames = detect_columns(cstream)

        if not keep_marks:
            cstream, n_mark = strip_black_marks(cstream, frames, mark_size)
            if n_mark:
                report.append('  第 %d 页：清除 %d 个黑色实心小方块（装订/裁切标记）'
                              % (kids.index(pnum) + 1, n_mark))

        if cols is None:
            mid = (x0 + x1) / 2.0
            cols = [(x0, mid), (mid, x1)]
            boundary = mid
            report.append('  第 %d 页：未识别到双栏，按中线切分' % (kids.index(pnum) + 1))
        else:
            # 两栏之间那条缝：取左栏右边框与右栏左边框的中点，
            # 这样左页保留自己的边框，右页也保留自己的边框，互不越界。
            boundary = (cols[0][1] + cols[1][0]) / 2.0

        clips = [(x0, boundary), (boundary, x1)]
        report.append('  第 %d 页：两栏缝隙中心 x=%.1f' % (kids.index(pnum) + 1, boundary))

        # 左侧页边距里的「姓名/学号」竖条：切出去，不进任何一页
        if not keep_strip:
            strip_right = detect_margin_strip(cstream, cols[0][0], cols[0][1])
            if strip_right:
                clips[0] = (strip_right + 2.0, boundary)
                report.append('  第 %d 页：左侧竖条 x<%.1f 已裁除（姓名/学号区块）'
                              % (kids.index(pnum) + 1, strip_right + 2.0))

        for side, (fmin, fmax) in enumerate(cols):
            # ---- 1. 该栏的 A4 窗口：以内容框为中心，宽 595.32 ----
            cx = (fmin + fmax) / 2.0
            wl, wr = cx - A4_W / 2.0, cx + A4_W / 2.0
            if wl < x0:                       # 贴到纸张边缘时整体平移，保持 A4 宽度
                wr += (x0 - wl); wl = x0
            if wr > x1:
                wl -= (wr - x1); wr = x1
            wl, wr = max(wl, x0), min(wr, x1)

            # ---- 2. 只保留本栏内容：x 方向裁剪，y 方向不裁（避免切掉页眉页脚）----
            #     先平移、再下裁剪框：这样裁剪框和内容处在同一坐标系里，
            #     新页面落在 [0, 0, A4_W, H]，打印时不会因原点偏移被缩放。
            #     （顺序反了会把裁剪框钉在旧坐标上，把内容左半截裁掉。）
            cl, cr = clips[side]
            cl, cr = max(cl, x0), min(cr, x1)
            wrapped = (b'q\r\n'
                       + ('1 0 0 1 %.2f 0 cm\r\n' % (-wl)).encode()
                       + ('%.2f -60 %.2f %.2f re W n\r\n'
                          % (cl, cr - cl, (y1 - y0) + 120)).encode()
                       + cstream + b'\r\nQ\r\n')

            # ---- 3. 生成新内容流 + 新页面字典（左栏复用原页面对象，右栏新建）----
            new_cont = new_id; new_id += 1
            streams.append((new_cont, wrapped))
            if side == 0:
                page_obj = pnum
            else:
                page_obj = new_id; new_id += 1
            page_body = set_boxes(pbody, fmt_box(0, 0, A4_W, y1 - y0), pages_num)
            page_body = re.sub(rb'/Contents\s+\d+\s+0\s+R',
                               b'/Contents ' + str(new_cont).encode() + b' 0 R', page_body)
            defs.append((page_obj, page_body))
            new_kids.append(page_obj)
            report.append('  第 %d 页 第%d栏: 内容框 %.1f~%.1f  裁剪 %.1f~%.1f  A4窗口 %.1f~%.1f'
                          % (kids.index(pnum) + 1, side + 1, fmin, fmax, cl, cr, wl, wr))

    pages_body = ('<</Type/Pages/Count %d/Kids[ %s]>>' %
                  (len(new_kids), ' '.join('%d 0 R' % n for n in new_kids))).encode()

    # ---- 4. 写出增量更新 ----
    out = bytearray(data)
    offsets = {}
    for num, body in defs:
        offsets[num] = len(out)
        out += str(num).encode() + b' 0 obj\r\n' + body + b'\r\nendobj\r\n'
    for num, payload in streams:
        offsets[num] = len(out)
        out += make_stream_obj(num, payload)
    offsets[pages_num] = len(out)
    out += str(pages_num).encode() + b' 0 obj\r\n' + pages_body + b'\r\nendobj\r\n'

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

    ids = re.search(rb'/ID\s*\[.*?\]', trailer, re.S)
    id_part = ids.group(0) if ids else b''
    info = re.search(rb'/Info\s+(\d+)', trailer)
    info_part = ('/Info %s 0 R' % info.group(1).decode()) if info else ''
    out += ('trailer\r\n<</Size %d/Root %s 0 R%s/Prev %d%s>>\r\n' %
            (new_id, root, info_part, prev, id_part.decode('latin1'))).encode('latin1')
    out += b'startxref\r\n' + str(xref_off).encode() + b'\r\n%%EOF\r\n'
    open(dst, 'wb').write(out)

    print('输出:', dst)
    print('原页数 %d -> 新页数 %d' % (len(kids), len(new_kids)))
    for line in report:
        print(line)
    print('新页面框:', [b.decode() for b in re.findall(rb'/MediaBox\s*\[[^\]]*\]', bytes(out))[-6:]])
    return dst


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    flags = [a for a in argv[1:] if a.startswith('--')]
    mark_size, skip = None, None
    for f in flags:
        if f.startswith('--mark-size'):
            if '=' in f:
                val = f.split('=', 1)[1]
            else:
                i = argv.index(f)
                val = argv[i + 1] if i + 1 < len(argv) else ''
                skip = val
            try:
                mark_size = tuple(float(v) for v in val.lower().split('x'))
            except ValueError:
                print('--mark-size 需要 WxH 形式，例如 28.2x14.1')
                return 1
    if skip:
        args = [a for a in args if a != skip]
    if not args:
        print(__doc__)
        return 1
    if not os.path.exists(args[0]):
        print('找不到文件:', args[0])
        return 1
    try:
        split(args[0], args[1] if len(args) > 1 else None,
              keep_marks='--keep-marks' in flags,
              keep_strip='--keep-strip' in flags,
              mark_size=mark_size)
    except RuntimeError as e:
        print('切分失败:', e)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
