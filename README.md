# A3 试卷分割 · A3 to A4 Split

把「两页 A4 拼在一张 A3 横向纸」的 Word / PDF（试卷、讲义、教辅、公文）**无损拆成独立的标准 A4 竖版页面**，直接可打印。

- 输出 **4 页 A4 PDF**（595.32 × 842.04 pt，原点归零，不缩放下不会跑版）
- 矢量无损：不清洗、不栅格化，字体 / 公式 / 图片原样保留
- 按**两栏真实边框**下刀，不是按页面中线——避免把「千克」的「千」切到下一页
- 自动裁掉左侧「姓名·学号」竖条、页边黑色裁切小方块
- 纯 Python 标准库，零第三方依赖

> 不想自己跑？有代切服务，在线说明页：https://wbapp-gby0ngpwa3zwqbmb34cfjh.app.workbuddy.host/?sharecode=EVWIMGSIFI

## 安装到 WorkBuddy

已发布到 SkillHub（腾讯技能市场）：`@user_bd60432e/a3-to-a4-split`

```bash
# 在 WorkBuddy 里说「安装 a3-to-a4-split」，或直接给 AI 这句：
请根据 https://skillhub.cn/install/skillhub.md 安装 @user_bd60432e/a3-to-a4-split
```

## 两种用法

### 1. 网页版（推荐，双击即用）

Windows 下双击 `启动服务.bat`，浏览器会打开 `http://127.0.0.1:8765/`。
把 `.docx` / `.doc` / `.pdf` / 图片拖进去，等 30 秒左右，得到可下载、可直接打印的 A4 PDF。

```bash
python serve.py                 # 默认 127.0.0.1:8765，端口被占用会自动往后找
python serve.py --port 9000
python serve.py --no-browser    # 不自动开浏览器
```

服务只监听本地回环，文件不离开本机，临时文件用完即删。

### 2. 命令行

```bash
# Word 一步到位 → 试卷-可打印A4.pdf
python split_word.py 试卷.docx

# 指定输出名
python split_word.py 试卷.docx 结果.pdf

# 已经是 PDF 就直接切（跳过 Word 导出）
python split_word.py 已排版.pdf 结果.pdf

# 只想切，不做底纹处理
python split_word.py 试卷.docx 结果.pdf --no-clean
```

单独切分（已有 A3 PDF）：

```bash
python split_a3.py in.pdf out.pdf
python split_a3.py in.pdf out.pdf --keep-marks           # 保留黑色裁切标记
python split_a3.py in.pdf out.pdf --keep-strip           # 保留左侧姓名学号竖条
python split_a3.py in.pdf out.pdf --mark-size 28.2x14.1  # 只清这个尺寸的标记
```

## 为什么必须先转 PDF

这类试卷的版面是用**绝对定位文本框**拼出来的，docx 里根本不存在「第几页」这个可切的对象——分页是 Word 排版引擎算出来的结果。
直接改 docx 的页面设置 / 分栏只会让所有文本框重新排版、整体错位（实测把「姓名学号」从 docx 里删掉，第 2 页会塌成一页）。

所以流程是：**让 Word 排一次版 → 导出 PDF → 按实际渲染出来的两栏位置下刀**。

## 内部流水线

```
docx
 ├─ 1. clean_shading.py   清掉 pct 网点底纹（w:shd），消除打印发灰/预览重影
 ├─ 2. Word COM 导出      300 PPI、不压缩图片（ExportAsFixedFormat）
 ├─ 3. flatten_smask.py   把公式位图的透明蒙版预合成到白底，去掉 /SMask
 ├─ 4. split_a3.py        识别两栏真实边框 → 以两框间隙中点为切口 → 增量更新 PDF
 └─ 5. check_pdf.py       结构自检：xref 链、/Length、页树 Count、Resources
```

第 4 步是核心，做四件事：

1. 从内容流里聚类 `re` 矩形，识别左右两栏各自的页边框；
2. 切口取「左栏右边框」与「右栏左边框」的中点（实测常见错位 4.7pt，正中间一刀就会吃掉 24pt 内容）；
3. 每页 MediaBox 重设为 `[0 0 595.32 842.04]`，内容先 `1 0 0 1 -Δ 0 cm` 平移、再套只覆盖本栏的裁剪框；
4. 最后清掉页边黑色实心小方块与左侧竖条。

全程走 PDF 增量更新（追加对象 + xref + trailer `/Prev`），原内容流一个字节不动。

## 目录

```
index.html              网页界面（拖拽上传、预览、下载）
serve.py                本地服务：静态托管 + POST /api/docx 跑流水线
启动服务.bat            Windows 双击启动
split_word.py           一键流水线（Word/PDF → 4 页 A4 PDF）
split_a3.py             切分核心
clean_shading.py        去网点底纹
flatten_smask.py        预合成透明蒙版
check_pdf.py            PDF 结构自检
vendor/                 pdf.js（本地离线渲染用，Apache-2.0）
skill/a3-to-a4-split/   打包成 Agent Skill 的版本（含 11 条踩坑记录）
```

## 环境

- Python 3.8+（只用标准库）
- 从 `.docx` / `.doc` 转换需要本机装着 **Microsoft Word**（走 COM 导出）；纯 PDF 切分不需要
- 浏览器端预览需要 `vendor/` 下的 pdf.js（已内置）

## 踩过的坑

`skill/a3-to-a4-split/references/pitfalls.md` 里记了 11 条，包括：中文底纹导致的文字重影、SMask 透明蒙版在预览器里的灰边、按中线切字、先裁剪后平移会丢掉左边框、非零 MediaBox 原点导致打印缩放、Word/ps1 脚本的 BOM 与编码问题等。

## License

MIT
