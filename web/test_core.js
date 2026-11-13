/* Node 侧回归测试：用同一份输入分别跑 JS 核心与 split_a3.py，比较输出结构。
 * 用法：node web/test_core.js <src.pdf> <out.pdf>
 */
const fs = require('fs');
const path = require('path');
const PDFLib = require(path.join(__dirname, '..', 'vendor', 'pdf-lib.min.js'));
const createSplitter = require('./split_core.js');

(async () => {
  const src = process.argv[2];
  const dst = process.argv[3] || path.join(__dirname, '..', 'work_cmp', 'js_a4.pdf');
  const bytes = new Uint8Array(fs.readFileSync(src));
  const splitter = createSplitter(PDFLib);

  const t0 = Date.now();
  const analysis = await splitter.analyze(bytes);
  console.log('页数:', analysis.pageCount, '耗时', Date.now() - t0, 'ms');
  analysis.pages.forEach((p) => {
    console.log(`  第${p.index + 1}页 ${p.width.toFixed(2)}x${p.height.toFixed(2)}` +
      ` 切口=${p.boundary.toFixed(1)} 自动=${p.autoBoundary}` +
      ` 框=${p.cols[0][0].toFixed(1)}~${p.cols[0][1].toFixed(1)} | ${p.cols[1][0].toFixed(1)}~${p.cols[1][1].toFixed(1)}` +
      ` 竖条=${p.stripRight == null ? '无' : p.stripRight.toFixed(1)} 黑块=${p.marksRemoved}`);
  });
  if (analysis.warnings.length) console.log('警告:', analysis.warnings.join(' / '));

  const t1 = Date.now();
  const res = await splitter.splitPdf(bytes, {});
  console.log('\n输出页数:', res.pageCount, '耗时', Date.now() - t1, 'ms', '大小', (res.bytes.length / 1024).toFixed(0), 'KB');
  res.report.forEach((l) => console.log('  ' + l));
  fs.writeFileSync(dst, res.bytes);
  console.log('写出:', dst);
})().catch((e) => { console.error('ERR', e); process.exit(1); });
