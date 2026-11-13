# -*- coding: utf-8 -*-
"""PDF 结构自检：xref 偏移、流长度、页树、页面框、引用完整性。
只检查会让阅读器打不开 / 打印尺寸错位的硬性问题。

用法: python check_pdf.py <文件.pdf>
"""
import re
import sys


def build_index(data):
    """顺着 /Prev 链解析经典 xref 表，返回 {对象号: 偏移}（新的覆盖旧的）"""
    pos = int(re.findall(rb'startxref\s+(\d+)', data)[-1])
    order, visited = [], set()
    while pos and pos not in visited:
        visited.add(pos)
        order.append(pos)
        seg = data[pos:]
        m = re.match(rb'xref\r?\n(.*?)trailer', seg, re.S)
        if not m:
            return None, order
        t = re.search(rb'trailer\s*<<(.*?)>>', seg, re.S)
        pr = re.search(rb'/Prev\s+(\d+)', t.group(1)) if t else None
        pos = int(pr.group(1)) if pr else 0

    mapping = {}
    for xref_pos in order:            # 从新到旧填充，旧的不覆盖新的
        seg = data[xref_pos:]
        m = re.match(rb'xref\r?\n(.*?)trailer', seg, re.S)
        cur = None
        for line in m.group(1).splitlines():
            line = line.strip()
            if re.match(rb'^\d+ \d+$', line):
                cur = int(line.split()[0])
            elif re.match(rb'^\d{10} \d{5} [nf]', line):
                if cur not in mapping and line[17:18] == b'n':
                    mapping[cur] = int(line[:10])
                cur += 1
            elif line:
                break
    return mapping, order


def obj_body(data, off):
    m = re.match(rb'(\d+)\s+(\d+)\s+obj', data[off:off + 40])
    if not m:
        return None, None
    start = off + m.end()
    end = data.find(b'endobj', start)
    return int(m.group(1)), data[start:end]


def stream_len(body):
    i = body.find(b'stream')
    if i < 0:
        return None, None
    if body[i + 6:i + 8] == b'\r\n':
        ds = i + 8
    elif body[i + 6:i + 7] == b'\n':
        ds = i + 7
    else:
        ds = i + 6
    de = body.find(b'endstream', ds)
    if de < 0:
        return None, None
    if body[de - 2:de] == b'\r\n':
        de -= 2
    elif body[de - 1:de] == b'\n':
        de -= 1
    return ds, de - ds


def main(path):
    data = open(path, 'rb').read()
    mapping, order = build_index(data)
    print('文件:', path)
    if mapping is None:
        print('  [!] 不是经典 xref 表结构，跳过')
        return True
    print('大小 %.1f KB | 对象 %d 个 | xref 段 %d 个 | startxref %d'
          % (len(data) / 1024.0, len(mapping), len(order), order[0]))

    ok = True

    # 1) xref 偏移必须精确指向 "对象号 0 obj"
    bad = [(n, o) for n, o in mapping.items()
           if not (lambda m: m and int(m.group(1)) == n)(re.match(rb'(\d+)\s+(\d+)\s+obj', data[o:o + 40]))]
    print('  xref 偏移错误 %d 处%s' % (len(bad), '' if not bad else ' -> %s' % bad[:5]))
    ok &= not bad

    # 2) 每个流的 /Length 必须等于实际字节数
    bad_len, nstream = [], 0
    for num, off in mapping.items():
        _, body = obj_body(data, off)
        if body is None:
            continue
        ds, actual = stream_len(body)
        if ds is None:
            continue
        nstream += 1
        head = body[:body.find(b'stream')]
        lm = re.search(rb'/Length\s+(\d+)(?!\s+0\s+R)', head)
        if not lm:
            rm = re.search(rb'/Length\s+(\d+)\s+0\s+R', head)
            if rm:
                roff = mapping.get(int(rm.group(1)))
                rb_ = obj_body(data, roff)[1] if roff is not None else b''
                dm = re.search(rb'(\d+)', rb_ or b'')
                declared = int(dm.group(1)) if dm else -1
            else:
                declared = -1
        else:
            declared = int(lm.group(1))
        if declared != actual:
            bad_len.append((num, declared, actual))
    print('  流对象 %d 个 | /Length 不符 %d 个%s'
          % (nstream, len(bad_len), '' if not bad_len else ' -> %s' % bad_len[:5]))
    ok &= not bad_len

    # 3) 页树
    pages_num = pages_kids = None
    for num, off in mapping.items():
        _, body = obj_body(data, off)
        if body and re.search(rb'/Type\s*/Pages', body) and b'/Kids' in body:
            km = re.search(rb'/Kids\s*\[([^\]]*)\]', body)
            kids = [int(v) for v in re.findall(rb'(\d+)\s+0\s+R', km.group(1))]
            cm = re.search(rb'/Count\s+(\d+)', body)
            if cm and int(cm.group(1)) == len(kids):
                pages_num, pages_kids = num, kids
    if not pages_kids:
        print('  [!] 找不到自洽的页树')
        return False
    print('  页树 obj%d | /Count %d | Kids %d' % (pages_num, len(pages_kids), len(pages_kids)))

    # 4) 逐页检查
    for i, k in enumerate(pages_kids, 1):
        off = mapping.get(k)
        if off is None:
            print('  !! 第%d页 对象 %d 不在 xref 中' % (i, k)); ok = False; continue
        _, pb = obj_body(data, off)
        box = re.findall(rb'/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]', pb)
        b = [float(v) for v in box[0]] if box else None
        w, h = (b[2] - b[0], b[3] - b[1]) if b else (0, 0)
        cont = re.findall(rb'/Contents\s+(\d+)', pb)
        good = bool(b) and abs(w - 595.32) < .5 and abs(h - 842.04) < .5 \
            and b[0] == 0 and b[1] == 0 and cont and int(cont[0]) in mapping
        if not good:
            ok = False
        has_res = b'/Resources' in pb
        print('  %s第%d页 obj%-4d 页面框 %.2f x %.2f  原点(%.2f, %.2f)  内容流 %s  资源 %s'
              % ('OK ' if good else '!! ', i, k, w, h,
                 b[0] if b else -1, b[1] if b else -1,
                 cont[0] if cont else '缺', '内联' if has_res else '继承'))

    # 5) 页面引用的对象是否都存在
    missing = set()
    for k in pages_kids:
        _, pb = obj_body(data, mapping[k])
        for ref in re.findall(rb'(\d+)\s+0\s+R', pb):
            r = int(ref)
            if r not in mapping:
                missing.add(r)
    print('  页面引用缺失对象: %d %s' % (len(missing), '' if not missing else sorted(missing)[:10]))
    ok &= not missing

    print('结论:', '结构完整，可正常打开与打印' if ok else '存在问题，需修复')
    return ok


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(0 if main(sys.argv[1]) else 1)
