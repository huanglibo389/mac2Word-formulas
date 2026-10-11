# 旧式公式修复流程

## 1 只读诊断

清点指定目录里的 PDF、TeX、旧 DOCX 和使用说明。先查对象类型、公式所在 part、源公式可用性；表格仍属主文档 part，页眉、页脚、脚注需分别检查。使用工作区依赖加载器返回的 Python：

```text
PYTHON scripts/legacy_equations.py audit --docx OLD.docx --report WORK/audit.json
```

报告公式所在 part、OMML 数量、OLE ProgID、缓存目标／类型、原生数据解析状态、EMF 字体及嵌入数据哈希。脚本只会自动解析受支持的 MTEF 3，不猜测其他版本。图片或元数据存在不等于原生编辑数据完整；EMF 字体报告是风险线索，不是原因定论。

报告使用新路径，不覆盖已有文件；输入 DOCX 本身及其路径别名不能作为报告输出。每次重新准备或更换稿件使用新的工作目录，不覆盖先前清单并混用旧 PNG。

## 2 准备已配对的预览源

已有与旧 Word 对应的 OMML 中间稿时，可用当前 Word 提供的 `omml2mathml.xsl`；先查它的真实安装路径，不能假设一定安装了 Microsoft Word。缺少可用 XSLT／核对过的源，不进入自动缓存替换。

```text
PYTHON scripts/legacy_equations.py prepare --docx OLD.docx --source-omml SOURCE.docx --xslt PATH/omml2mathml.xsl --work-dir WORK
```

默认只处理 `word/document.xml`。发现其他 part 中也有公式时，分别使用 `--part` 和 `--source-part`，分配独立工作目录；先核对各 part 映射。先后位置仅用于提出配对，必须由逐公式原生字符核对确认；不一致就停止。

脚本核对 source 样式中明确的黑板粗体字符，再进行支持版本的原生字符比较，并检查 OMML→MathML 字符保真。`m:nary` 的运算符可能存在属性里，不在 `m:t` 中；MTEF 求和模板又可能先存正文和上下界、后存“∑”。已支持的求和模板按运算符、下界、上界、正文及隐藏上界分别核对。遇到原始字符顺序不同，先检查结构，不能删除求和号、排序字符、忽略上下界或放宽匹配来绕过失败；未知求和变体或运算符仍停止。此工具不是所有 MTEF 模板的语义验证器，其他复杂结构不能只凭字符一致自动通过。

进一步人工核对分式、上下标等结构和样式。只有人工确认是编号的项，使用 `--tag-indices 215` 之类显式参数；不自动把所有尾部 `(数字)` 当标签。

预览尺寸默认继承旧框。确有字号或裁切问题时，用审查过的 JSON 文件传 `--geometry`，键为对象序号，值为需要调整的 `width_pt`／`height_pt`；写明原因并重新渲染。不要套用案例中的索引或尺寸。

## 3 离线生成缓存

运行时、包目录来自依赖加载器；Playwright 与 Chromium 已可用才运行，不自动下载。MathJax 不可用时可以使用本地浏览器原生 MathML，但生成源、字体和缩放仍要核验。若该 Playwright 默认的浏览器文件不存在，而本机另有可用 Chromium，可查验路径后显式传 `--browser INSTALLED_BROWSER`；记录真实版本并测试当前任务所用功能，不默默换另一份 Playwright。没有可用浏览器则报告限制。

```text
NODE scripts/render_previews.cjs --packages PACKAGE_DIR --manifest WORK/formulas.json --output-dir WORK/png --dpi 600
```

本地浏览器不访问网络。包路径显式绑定；只设置 NODE_PATH 不能阻止祖先目录中的 node_modules 优先加载。检查 `runtime_validation.json` 的实际包路径。字体使用本机可用数学字体；未取得许可不复制字体到输出。检查生成的 `preview_validation.json`，零尺寸会失败，过小或过大的缩放标记为复核项；字体采样不是全式字符覆盖认证。有标记不能仅凭脚本完成就交付，须放大检查。透明 PNG 请在白底或实际文档中查看。

需要统一公式字号、旧框导致长短式大小不一时，使用可选自然尺寸模式；字号由当前文档决定，下面的 11 仅为示例：

```text
NODE scripts/render_previews.cjs --packages PACKAGE_DIR --manifest WORK/formulas.json --output-dir WORK/png --dpi 600 --geometry-mode natural --font-size-pt 11
```

此模式不缩放公式，测量实际宽高和基线；全量渲染成功后才把新尺寸写回工作清单，并保留输入清单备份及其哈希。原 OLE 数据不变。默认 `preserve` 模式仍沿用旧框，不改变现有工作流。自然尺寸超过安全框或可用行宽时停止／复核，不自动缩小或改写公式。基线如何应用到 Word 要另作版式校准，见 [参考版式与最终文件验收](reference-optimization.md)。

修改工具后，用 `PYTHON tests/test_equation_pairing.py -v` 检查求和阅读顺序、界限分槽与失败条件；渲染后可用 `PYTHON tests/check_preview_output.py --manifest WORK/formulas.json --png-dir WORK/png` 检查像素尺寸、基线记录及源身份保留。这些检查不替代人工看图或 Office 编辑测试。

完整渲染成功后生成 `render_completion.json`，绑定最终工作清单哈希、每张 PNG 哈希、对应 MathML／原生对象哈希及尺寸；没有这个记录不能进入 patch。失败重试或修改版式时使用新的工作目录，不复用旧的完成记录。

## 4 只替换缓存并核对差分

```text
PYTHON scripts/legacy_equations.py patch --work-dir WORK --out NEW.docx
```

输出须是新文件。脚本先核对原 Word 和 OMML 源仍未改变、对象位置与编号一一对应、尺寸有效，以及当前清单—渲染完成记录—PNG 的哈希绑定，然后验证 PNG 结构并完整解码；只验证图片能打开、对象数量相同或嵌入数据未变，不足以排除误用旧预览。

缓存作为新媒体／新关系添加，不删除共享旧图、不移除其他 EMF；仅改对应预览的关系引用及显式审查过的尺寸。检查所有 OLE 嵌入数据、正文及未涉及 part 保持。可用 `--docx` 指定原稿副本，但哈希必须一致。PNG 可解码不等于公式内容正确，仍须检查源映射和实际页图。修改工具后另运行 `PYTHON tests/test_cache_safety.py -v`，检查审计防覆盖、工作目录重用、旧预览、编号与尺寸保护。

## 5 渲染验收

按 `documents` skill 的 authoring marker 约定执行；该 skill 的一次编辑流程只成功标记一次。使用当前渲染器，旧式对象检查时禁止自动转换：

```text
PYTHON scripts/render_preserving_ole.py NEW.docx --renderer CURRENT_DOCUMENTS/render_docx.py --output-dir WORK/render
```

有中文字体问题时按当前环境提供有效 fontconfig／字体，不依赖旧项目的绝对路径。查看全部页图，逐页检查符号、分式、编号、裁切、重叠、基线及分页。需要修正时重做预览／patch 并渲染新的输出副本，不覆盖用户原稿。目标为 Word 且本机可用时再检查 Word 实际输出；不同引擎分别记录，不能相互代替。

最后将 DOCX 放到用户指定位置，核对复制后的哈希，并重新打开最终路径验收，不只查看先前候选稿。页图／QA PDF 不随 Word 一起交付，除非用户要求。实际编辑验证与措辞参见 [制作与验收](authoring-and-acceptance.md)。
