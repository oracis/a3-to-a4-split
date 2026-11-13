# -*- coding: utf-8 -*-
"""
一条命令：把 A3 横向的 Word（.docx / .doc）直接切成 4 页标准 A4 可打印 PDF。

用法:
    python split_word.py 试卷.docx                # 输出 试卷-可打印A4.pdf
    python split_word.py 试卷.docx 结果.pdf
    python split_word.py 试卷.docx 结果.pdf --no-clean     # 不清网点底纹
    python split_word.py 已有.pdf  结果.pdf                # 直接给 PDF 时跳过 Word 导出

内部流水线（全程可重复）:
    1. Word 文档 → 清掉 pct 网点底纹（临时 docx，不动原文件）
    2. 用本机 Word（COM）导出 PDF，并强制 300PPI、不压缩图片
    3. 把公式位图的透明蒙版(SMask) 预合成到白底，消除预览发虚/重影
    4. 按两栏真实边框切分：裁掉左侧「姓名·学号」竖条、页顶裁切黑块，页面框归零

为什么要经过 PDF：
    这类试卷的版面是靠「绝对定位文本框」拼的，docx 里并不存在「第几页」这种
    可切的东西——分页结果是 Word 排版引擎算出来的。所以必须先让 Word 排一次
    版（导出 PDF），再按实际渲染出来的两栏位置下刀。直接改 docx 页面设置只会
    让内容重排错位。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = r"""
param([string]$Src, [string]$Dst)
$word = $null; $doc = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  try { $word.Options.DefaultTargetPPI = 300 } catch {}
  try { $word.Options.DoNotCompressPicturesInFile = $true } catch {}
  $doc = $word.Documents.Open($Src, $false, $true)
  $doc.ExportAsFixedFormat($Dst, 17)
  Write-Output ("OK|" + $doc.ComputeStatistics(2))
} catch {
  Write-Output ("ERR|" + $_.Exception.Message)
} finally {
  if ($doc) { $doc.Close(0) }
  if ($word) { $word.Quit() }
}
"""


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', **kw)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def docx_to_pdf(src, dst, workdir):
    """用本机 Word 把 docx 导出为 PDF。返回 True/False 与说明。"""
    ps1 = os.path.join(workdir, '_export.ps1')
    with open(ps1, 'w', encoding='utf-8-sig') as f:   # BOM 让 PS 5.1 认 UTF-8
        f.write(PS1)
    rc, out = run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', ps1, '-Src', src, '-Dst', dst])
    line = next((l for l in out.splitlines() if l.startswith(('OK|', 'ERR|'))), '')
    if line.startswith('OK|'):
        return True, 'Word 排版完成，共 %s 页 A3' % line[3:].strip()
    return False, (line[4:] if line.startswith('ERR|') else out.strip()[:300])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = set(a for a in sys.argv[1:] if a.startswith('--'))
    src = args[0]
    if not os.path.exists(src):
        print('找不到文件:', src)
        return 1
    ext = os.path.splitext(src)[1].lower()
    if ext not in ('.docx', '.doc', '.pdf'):
        print('只支持 .docx / .doc / .pdf，收到的是', ext or '(无扩展名)')
        return 1
    out = args[1] if len(args) > 1 else os.path.splitext(src)[0] + '-可打印A4.pdf'

    work = tempfile.mkdtemp(prefix='split_word_')
    keep = '--keep-temp' in flags
    try:
        cur = src

        # ---- 1. 清网点底纹（只对 Word 生效）----
        if ext != '.pdf' and '--no-clean' not in flags:
            step = os.path.join(work, '1-clean.docx')
            rc, log = run([sys.executable, os.path.join(HERE, 'clean_shading.py'), src, step])
            n = re.search(r'移除底纹: (\d+)', log)
            if rc == 0 and os.path.exists(step):
                cur = step
                print('[1/4] 清网点底纹: 移除 %s 处' % (n.group(1) if n else '?'))
            else:
                print('[1/4] 清底纹失败，改用原文档:', log.strip()[:160])
        else:
            print('[1/4] 跳过清底纹')

        # ---- 2. Word → PDF ----
        if ext != '.pdf':
            pdf1 = os.path.join(work, '2-word.pdf')
            ok, msg = docx_to_pdf(cur, pdf1, work)
            if not ok:
                print('[2/4] Word 导出失败:', msg)
                print('      请确认本机装了 Word 或 WPS；也可以在 Word 里手动「另存为 PDF」后重跑。')
                return 2
            print('[2/4]', msg)
            cur = pdf1
        else:
            print('[2/4] 输入已是 PDF，跳过导出')

        # ---- 3. 去透明蒙版 ----
        pdf2 = os.path.join(work, '3-flat.pdf')
        rc, log = run([sys.executable, os.path.join(HERE, 'flatten_smask.py'), cur, pdf2])
        m = re.search(r'(\d+)\s*个|共\s*(\d+)', log)
        if rc == 0 and os.path.exists(pdf2):
            cur = pdf2
            print('[3/4] 透明蒙版预合成完成')
        else:
            print('[3/4] 去蒙版失败，跳过:', log.strip()[:160])

        # ---- 4. 切分 ----
        rc, log = run([sys.executable, os.path.join(HERE, 'split_a3.py'), cur, out])
        print('[4/4] 切分\n' + '\n'.join('      ' + l for l in log.strip().splitlines()[-12:]))
        if rc != 0 or not os.path.exists(out):
            print('切分失败')
            return 3

        print()
        print('完成:', out, '(%.1f KB)' % (os.path.getsize(out) / 1024))
        rc, log = run([sys.executable, os.path.join(HERE, 'check_pdf.py'), out])
        print('自检:', '通过' if rc == 0 else '有问题')
        for l in log.strip().splitlines()[:8]:
            print('      ' + l)
        return 0
    finally:
        if keep:
            print('临时文件保留在:', work)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
