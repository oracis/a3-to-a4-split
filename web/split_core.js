/*!
 * A3 双拼 → A4 切分核心（浏览器 / Node 通用，纯逻辑、无 DOM）
 *
 * 与 split_a3.py 的几何算法一一对应：
 *   1) 从内容流的 `re` 矩形里识别左右两栏的真实边框
 *   2) 切口取「左栏右边框」与「右栏左边框」的中点（不是页面中线）
 *   3) 每栏以自身内容框为中心开一个 A4 宽的窗口，先平移再下裁剪框
 *   4) 顺带删掉页边空白里的黑色实心小方块、裁掉左侧姓名/学号竖条
 *
 * 用法：const splitter = createSplitter(PDFLib); await splitter.splitPdf(bytes, opts)
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.createSplitter = factory;
})(typeof self !== 'undefined' ? self : this, function createSplitter(PDFLib) {
  'use strict';

  var PDFDocument = PDFLib.PDFDocument;
  var PDFName = PDFLib.PDFName;
  var PDFArray = PDFLib.PDFArray;
  var PDFRawStream = PDFLib.PDFRawStream;
  var decodePDFRawStream = PDFLib.decodePDFRawStream;

  var A4_W = 595.32;
  var MARK_W = [14, 70];
  var MARK_H = [5, 45];
  var FILL_OPS = { f: 1, 'f*': 1, F: 1, B: 1, 'B*': 1, b: 1, 'b*': 1 };
  var STROKE_OPS = { S: 1, s: 1, B: 1, 'B*': 1, b: 1, 'b*': 1 };

  var RE_RECT = /(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+re\s*([A-Za-z*]+)/g;
  var RE_CM = /(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+(-?\d*\.?\d+)\s+cm\b/g;

  /* ---------------- 字节 ↔ 单字节字符串（下标与字节偏移一一对应） ---------------- */

  function bytesToLatin1(u8) {
    var s = '', CH = 8192;
    for (var i = 0; i < u8.length; i += CH) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
    }
    return s;
  }

  function latin1ToBytes(str) {
    var u = new Uint8Array(str.length);
    for (var i = 0; i < str.length; i++) u[i] = str.charCodeAt(i) & 0xff;
    return u;
  }

  function f2(v) { return (Math.round(v * 100) / 100).toFixed(2); }

  /* ---------------- 内容流解码 ---------------- */

  function pageContentsBytes(doc, page) {
    var c = page.node.get(PDFName.of('Contents'));
    var refs = [];
    if (c instanceof PDFArray) refs = c.asArray();
    else if (c) refs = [c];

    var parts = [], total = 0;
    for (var i = 0; i < refs.length; i++) {
      var st = doc.context.lookup(refs[i]);
      if (!st) continue;
      var bytes = null;
      if (st instanceof PDFRawStream) {
        try { bytes = decodePDFRawStream(st).decode(); }
        catch (e) { bytes = st.getContents ? st.getContents() : null; }
      } else if (typeof st.getUnencodedContents === 'function') {
        bytes = st.getUnencodedContents();
      }
      if (bytes && bytes.length) { parts.push(bytes); total += bytes.length; }
    }
    var out = new Uint8Array(total), off = 0;
    for (var j = 0; j < parts.length; j++) { out.set(parts[j], off); off += parts[j].length; }
    return out;
  }

  /* ---------------- 栏位识别 ---------------- */

  function parseRects(s) {
    var out = [], m;
    RE_RECT.lastIndex = 0;
    while ((m = RE_RECT.exec(s)) !== null) {
      out.push({
        x: parseFloat(m[1]), y: parseFloat(m[2]),
        w: parseFloat(m[3]), h: parseFloat(m[4]),
        op: m[5], start: m.index, end: m.index + m[0].length
      });
    }
    return out;
  }

  // 只保留「一栏那么大」的矩形：比正文块大、比整页小
  function columnFrames(rects) {
    var keep = [];
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      if (r.w > 150 && r.w < 700 && r.h > 250 && r.h < 900 && r.x >= -5) keep.push(r);
    }
    return keep;
  }

  function detectColumns(rects) {
    var frames = columnFrames(rects);
    if (frames.length < 2) return { cols: null, frames: frames };

    var sorted = frames.slice().sort(function (a, b) { return (a.x + a.w / 2) - (b.x + b.w / 2); });
    var best = -1, idx = -1;
    for (var i = 0; i < sorted.length - 1; i++) {
      var c0 = sorted[i].x + sorted[i].w / 2;
      var c1 = sorted[i + 1].x + sorted[i + 1].w / 2;
      var gap = c1 - c0;
      if (gap > best) { best = gap; idx = i; }
    }
    if (best < 30) return { cols: null, frames: frames };   // 整页一栏，退回中线

    var left = sorted.slice(0, idx + 1);
    var right = sorted.slice(idx + 1);
    var lmin = Infinity, lmax = -Infinity, rmin = Infinity, rmax = -Infinity;
    left.forEach(function (r) { lmin = Math.min(lmin, r.x); lmax = Math.max(lmax, r.x + r.w); });
    right.forEach(function (r) { rmin = Math.min(rmin, r.x); rmax = Math.max(rmax, r.x + r.w); });
    return { cols: [[lmin, lmax], [rmin, rmax]], frames: frames, gutter: [lmax, rmin] };
  }

  /* ---------------- 黑色实心小方块 ---------------- */

  function insideAny(x, y, w, h, frames, tol) {
    tol = tol == null ? 2 : tol;
    for (var i = 0; i < frames.length; i++) {
      var fr = frames[i];
      if (x >= fr.x - tol && y >= fr.y - tol &&
          x + w <= fr.x + fr.w + tol && y + h <= fr.y + fr.h + tol) return true;
    }
    return false;
  }

  function stripBlackMarks(s, frames, size) {
    var wlo, whi, hlo, hhi;
    if (size) { wlo = whi = size[0]; hlo = hhi = size[1]; }
    else { wlo = MARK_W[0]; whi = MARK_W[1]; hlo = MARK_H[0]; hhi = MARK_H[1]; }

    var rects = parseRects(s);
    var spans = [], filled = {}, count = 0;
    var i;
    for (i = 0; i < rects.length; i++) {
      var r = rects[i];
      if (!FILL_OPS[r.op]) continue;
      if (!(r.w >= wlo - 0.01 && r.w <= whi + 0.01 && r.h >= hlo - 0.01 && r.h <= hhi + 0.01)) continue;
      if (frames && frames.length && insideAny(r.x, r.y, r.w, r.h, frames)) continue;
      var key = [r.x, r.y, r.w, r.h].map(function (v) { return v.toFixed(2); }).join(',');
      if (!filled[key]) { filled[key] = 1; count++; }
      spans.push([r.start, r.end]);
    }
    if (!count) return { text: s, removed: 0 };

    // 同一矩形上的描边指令（标记常写成「填充 + 同位置描边」）一并删掉
    for (i = 0; i < rects.length; i++) {
      var q = rects[i];
      if (!STROKE_OPS[q.op]) continue;
      var k2 = [q.x, q.y, q.w, q.h].map(function (v) { return v.toFixed(2); }).join(',');
      if (filled[k2]) spans.push([q.start, q.end]);
    }

    spans.sort(function (a, b) { return a[0] - b[0]; });
    var out = '', last = 0;
    for (i = 0; i < spans.length; i++) {
      if (spans[i][0] < last) { last = Math.max(last, spans[i][1]); continue; }
      out += s.slice(last, spans[i][0]);
      last = spans[i][1];
    }
    out += s.slice(last);
    return { text: out, removed: count };
  }

  /* ---------------- 左侧姓名/学号竖条 ---------------- */

  function detectMarginStrip(s, frameMin, frameMax) {
    var right = null, m;
    RE_CM.lastIndex = 0;
    while ((m = RE_CM.exec(s)) !== null) {
      var tail = s.slice(m.index + m[0].length, m.index + m[0].length + 160);
      if (!/\/[\w.\-]+\s+Do/.test(tail)) continue;
      var a = parseFloat(m[1]), d = parseFloat(m[4]), e = parseFloat(m[5]);
      var w = Math.abs(a), h = Math.abs(d);
      if (w > 10 && w < 90 && h > 300 && h < 820 && (e + w) <= frameMin + 6) {
        right = Math.max(right == null ? 0 : right, e + w);
      }
    }
    return right;
  }

  /* ---------------- 单页分析 ---------------- */

  function analyzePage(pageIndex, mediaBox, contentBytes, opts) {
    opts = opts || {};
    var x0 = mediaBox.x, y0 = mediaBox.y;
    var x1 = mediaBox.x + mediaBox.width, y1 = mediaBox.y + mediaBox.height;

    var s = bytesToLatin1(contentBytes);
    var det = detectColumns(parseRects(s));
    var cols = det.cols, boundary, auto = true;

    if (!cols) {
      var mid = (x0 + x1) / 2;
      cols = [[x0, mid], [mid, x1]];
      boundary = mid;
      auto = false;
    } else {
      // 两栏之间那条缝：左栏右边框与右栏左边框的中点（不是页面中线）
      boundary = (cols[0][1] + cols[1][0]) / 2;
    }

    var stripRight = null;
    if (!opts.keepStrip) stripRight = detectMarginStrip(s, cols[0][0], cols[0][1]);

    var markInfo = { removed: 0, text: s };
    if (!opts.keepMarks) markInfo = stripBlackMarks(s, det.frames, opts.markSize);

    return {
      index: pageIndex,
      mediaBox: { x0: x0, y0: y0, x1: x1, y1: y1 },
      cols: cols,
      gutter: det.gutter || null,
      boundary: boundary,
      autoBoundary: auto,
      stripRight: stripRight,
      marksRemoved: markInfo.removed,
      width: x1 - x0,
      height: y1 - y0,
      // 内部字段（analyze 会剔除，splitPdf 复用，避免重复解码与重复去标记）
      _frames: det.frames,
      _stripped: markInfo.text
    };
  }

  function publicPage(info) {
    var out = {};
    for (var k in info) if (k.charAt(0) !== '_') out[k] = info[k];
    return out;
  }

  /* ---------------- 主流程 ---------------- */

  async function loadDoc(bytes) {
    return await PDFDocument.load(bytes, {
      ignoreEncryption: true,
      throwOnInvalidObject: false,
      updateMetadata: false
    });
  }

  function clampWindow(fmin, fmax, x0, x1) {
    var cx = (fmin + fmax) / 2;
    var wl = cx - A4_W / 2, wr = cx + A4_W / 2;
    if (wl < x0) { wr += (x0 - wl); wl = x0; }
    if (wr > x1) { wl -= (wr - x1); wr = x1; }
    wl = Math.max(wl, x0); wr = Math.min(wr, x1);
    return [wl, wr];
  }

  // 把「原内容 + 平移 + 裁剪」包成一个新内容流
  function wrapContent(contentStr, wl, cl, cr, y0, y1) {
    return 'q\r\n' +
      '1 0 0 1 ' + f2(-wl) + ' 0 cm\r\n' +
      f2(cl) + ' -60 ' + f2(cr - cl) + ' ' + f2((y1 - y0) + 120) + ' re W n\r\n' +
      contentStr + '\r\nQ\r\n';
  }

  async function deflate(bytes) {
    if (typeof CompressionStream !== 'function') return null;
    try {
      var cs = new CompressionStream('deflate');
      var w = cs.writable.getWriter();
      w.write(bytes); w.close();
      var ab = await new Response(cs.readable).arrayBuffer();
      return new Uint8Array(ab);
    } catch (e) { return null; }
  }

  /**
   * 分析（不写出）：给 UI 用
   * @returns {Promise<{pageCount, pages:[...], warnings:[]}>}
   */
  async function analyze(bytes, opts) {
    var doc = await loadDoc(bytes);
    var pages = [], warnings = [];
    for (var i = 0; i < doc.getPageCount(); i++) {
      var page = doc.getPage(i);
      var mb = page.getMediaBox();
      var info = analyzePage(i, mb, pageContentsBytes(doc, page), opts);
      pages.push(publicPage(info));
      if (!info.autoBoundary) {
        warnings.push('第 ' + (i + 1) + ' 页未识别到左右两栏，按页面中线切分');
      }
      if (info.width < info.height * 1.25) {
        warnings.push('第 ' + (i + 1) + ' 页宽高比 ' + (info.width / info.height).toFixed(2) +
          '，不像 A3 横向双拼，拆开后可能是两张窄页');
      }
    }
    return { pageCount: pages.length, pages: pages, warnings: toUniq(warnings) };
  }

  function toUniq(a) {
    var seen = {}, out = [];
    a.forEach(function (v) { if (!seen[v]) { seen[v] = 1; out.push(v); } });
    return out;
  }

  /**
   * 切分并输出 PDF
   * @param {Uint8Array} bytes 源 PDF
   * @param {object} opts
   *   boundary:  {页序号: 切口x}  手动微调（缺省用自动识别值）
   *   keepMarks: true 保留黑色小方块
   *   keepStrip: true 保留左侧姓名/学号竖条
   *   markSize:  [w,h] 只删固定尺寸的方块
   */
  async function splitPdf(bytes, opts) {
    opts = opts || {};
    var src = await loadDoc(bytes);
    var out = await PDFDocument.create();
    var report = [], overrides = opts.boundary || {};

    for (var i = 0; i < src.getPageCount(); i++) {
      var page = src.getPage(i);
      var mb = page.getMediaBox();
      var contentBytes = pageContentsBytes(src, page);
      var info = analyzePage(i, mb, contentBytes, opts);

      var boundary = (overrides[i] != null) ? Number(overrides[i]) : info.boundary;
      var x0 = info.mediaBox.x0, y0 = info.mediaBox.y0;
      var x1 = info.mediaBox.x1, y1 = info.mediaBox.y1;

      var clips = [[x0, boundary], [boundary, x1]];
      if (info.stripRight != null) clips[0] = [info.stripRight + 2, boundary];

      // 内容流已在此前去掉黑方块（opts.keepMarks 时原样）
      var contentStr = info._stripped;

      // 同一页要出两张（左栏 / 右栏），copyPages 复制两份，资源各自独立
      var copied = await out.copyPages(src, [i, i]);
      out.addPage(copied[0]);
      out.addPage(copied[1]);

      var newPages = [out.getPage(out.getPageCount() - 2), out.getPage(out.getPageCount() - 1)];

      for (var side = 0; side < 2; side++) {
        var fr = info.cols[side];
        var win = clampWindow(fr[0], fr[1], x0, x1);
        var wl = win[0];
        var cl = Math.max(clips[side][0], x0), cr = Math.min(clips[side][1], x1);
        var wrapped = wrapContent(contentStr, wl, cl, cr, y0, y1);

        var pageObj = newPages[side];
        var raw = latin1ToBytes(wrapped);
        var packed = await deflate(raw);
        var dict = { };
        if (packed) dict = { Filter: PDFName.of('FlateDecode') };
        var stream = out.context.stream(packed || raw, dict);
        pageObj.node.set(PDFName.of('Contents'), out.context.register(stream));

        pageObj.setMediaBox(0, 0, A4_W, y1 - y0);
        pageObj.setCropBox(0, 0, A4_W, y1 - y0);
        pageObj.node.delete(PDFName.of('Rotate'));

        report.push('第 ' + (i + 1) + ' 页 第' + (side + 1) + '栏：内容框 ' +
          fr[0].toFixed(1) + '~' + fr[1].toFixed(1) + '　裁剪 ' + cl.toFixed(1) + '~' +
          cr.toFixed(1) + '　A4 窗口 ' + wl.toFixed(1) + '~' + (wl + A4_W).toFixed(1));
      }
      report.push('第 ' + (i + 1) + ' 页：切口 x=' + boundary.toFixed(1) +
        (info.stripRight != null ? '，左侧竖条已裁掉（x<' + (info.stripRight + 2).toFixed(1) + '）' : '') +
        (info.marksRemoved ? '，清除 ' + info.marksRemoved + ' 个黑方块' : ''));
    }

    var saved = await out.save({ useObjectStreams: false, addDefaultPage: false });
    return {
      bytes: saved,
      pageCount: out.getPageCount(),
      report: report,
      analysis: { pageCount: src.getPageCount() }
    };
  }

  return {
    analyze: analyze,
    splitPdf: splitPdf,
    // 供测试
    _internals: {
      bytesToLatin1: bytesToLatin1, latin1ToBytes: latin1ToBytes,
      parseRects: parseRects, detectColumns: detectColumns,
      stripBlackMarks: stripBlackMarks, detectMarginStrip: detectMarginStrip,
      analyzePage: analyzePage, pageContentsBytes: pageContentsBytes
    }
  };
});
