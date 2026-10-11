# mac2Word公式

在 Mac 上整理带数学公式的 Word，保留现有公式的编辑数据，并修复旧式公式的显示预览。

适合数学教师整理讲义、修复旧 Word 的公式乱码，以及参照一份排版较好的 Word 优化另一份文档。本项目整理了相应的 Codex skill、辅助脚本和一份实际 Word 参考样例。

## 能做什么

- 检查 Word 中的公式类型、旧式对象、显示预览和文件关系。
- 在已有、已核对的 OMML 源与旧式公式对象逐项对应时，生成高清公式预览。
- 只替换显示缓存，保持原来的 OLE 公式编辑数据不变。
- 选择保留旧显示框，或按指定字号生成自然尺寸预览。
- 检查公式编号、求和上下界、预览来源和文件哈希，避免错配、重复编号与旧缓存混用。
- 提供参照旧版排版、核对最终文件和区分不同阅读器结果的工作说明。

它不是 MathType 安装器，也不是任意 LaTeX 到原生 MathType 的转换器。图片、Word 原生公式和 MathType 对象是不同的格式，不能互相冒称。

## 文件结构

```text
README.md
LICENSE
requirements.txt
package.json
THIRD_PARTY_NOTICES.md
skills/zhi-word-gongshi/
  SKILL.md
  agents/openai.yaml
  scripts/
  references/
  tests/
templates/
  README.md
  20261006--两道题解答_公式后方框修复版.docx
```

项目的中文名称为“mac2Word公式”。skill 的调用名保留为 `$zhi-word-gongshi`，以免已有调用失效。

## 使用方式

### 作为 skill 使用

将 `skills/zhi-word-gongshi` 文件夹放入你的本地 skill 目录。若已有同名版本，先备份，不要直接覆盖。然后可以提出这样的请求：

> 用制word公式检查这份 Word。只修公式显示，保留正文和原公式编辑数据，另存新文件，并说明实际验证了哪些内容。

参照旧版时，要明确两个不同文件的角色：

> 用旧文件作为排版参考，优化我指定的新文件。不要把旧文件的题目或证明换进新文件。

skill 使用当前可用的文档渲染工具；本项目没有打包 Microsoft Word、LibreOffice、MathType、浏览器、字体或第三方文档渲染器。

### 单独运行脚本

需要 Python、`lxml`、`Pillow`；预览渲染另外需要 Node.js、Playwright 和已经安装的 Chromium 系浏览器。`requirements.txt` 与 `package.json` 记录了本次测试实际使用的库版本，不表示其他版本已经全部验证。安装依赖应由使用者在自己的环境中明确执行；脚本不会自动下载安装包或字体。

先做只读检查。报告必须使用新路径：

```bash
python3 skills/zhi-word-gongshi/scripts/legacy_equations.py audit \
  --docx INPUT.docx --report work/audit.json
```

重建预览还需要与旧 Word 逐项对应的 OMML 源，以及本机可用的 OMML→MathML XSLT。必须确认对应关系，不能只按公式数量配对：

```bash
python3 skills/zhi-word-gongshi/scripts/legacy_equations.py prepare \
  --docx INPUT.docx --source-omml SOURCE.docx \
  --xslt /absolute/path/omml2mathml.xsl --work-dir work/job-001
```

使用已经核实的绝对包目录与浏览器路径，离线生成预览：

```bash
node skills/zhi-word-gongshi/scripts/render_previews.cjs \
  --packages /absolute/path/node_modules \
  --browser /absolute/path/chromium \
  --manifest work/job-001/formulas.json \
  --output-dir work/job-001/png --dpi 600
```

需要统一字号时，可显式增加 `--geometry-mode natural --font-size-pt 11`。11 磅只是示例；字号应按当前文档决定。

检查渲染报告与实际预览后，再另存修复副本：

```bash
python3 skills/zhi-word-gongshi/tests/check_preview_output.py \
  --manifest work/job-001/formulas.json --png-dir work/job-001/png

python3 skills/zhi-word-gongshi/scripts/legacy_equations.py patch \
  --work-dir work/job-001 --out OUTPUT.docx
```

每次重试或更换源文件使用新的工作目录。详细参数、停止条件和人工检查要求见 [旧式公式修复流程](skills/zhi-word-gongshi/references/legacy-repair.md)。

## 测试与验收

本项目包含 17 项单元回归测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s skills/zhi-word-gongshi/tests -p 'test_*.py' -v
```

测试覆盖求和结构配对、报告防覆盖、工作目录重用、缓存来源、对象编号和尺寸保护。测试通过不表示数学证明已经审查，也不表示任意 MathType 文档均可解析。

公开样例含 270 个 `Equation.3` 旧式公式对象，保留原编辑数据，并使用 PNG 显示预览。它用于排版与显示参考；不是空白 `.dotx` 模板，也没有附带配对的 OMML 源，不能直接用作 `prepare` 的完整输入组合。

已有文档实践包含一个 206 公式对象的全量缓存重建案例。Microsoft Word 与 LibreOffice 曾出现基线及分页差异，不能把一种阅读器的显示通过称为跨阅读器通过。老师电脑上的 Word＋MathType 6.8 双击编辑尚未验证。

## 已知限制

- 自动原生数据配对限于脚本支持的 MTEF 3 结构。已测试的求和模板可以分槽核对；未知版本、变体或运算符会停止。
- 普通复杂公式仍需人工检查分式、根式、上下标和多行结构。字符相同不等于结构相同。
- 没有配对的公式源、XSLT 或本地浏览器时，不应猜测并自动替换预览。
- 显示清楚和能够在目标公式编辑器中编辑，是两个需要分别验证的结果。
- Word、字体与 MathType 的实际兼容性需要在使用者的目标环境中测试。

## MathType 下载及权利说明

本项目不附带 MathType 商业安装包、注册码或激活工具。获取软件请访问 [MathType 官方网站](https://www.wiris.com/en/mathtype/)，并按 [官方许可说明](https://docs.wiris.com/en_US/mathtype-eula) 使用。这里不承诺官方页面提供 6.8 旧版下载。

## 开源许可证

本项目的原创代码、skill 与原创说明文档采用 [MIT 许可证](LICENSE)，版权署名为 `Copyright (c) 2026 huanglibo389`。使用、修改或再分发这些材料时，须保留许可证中的版权声明与许可声明。

MIT 许可证不改变第三方软件、字体和其他第三方材料的许可。`templates/` 中的 Word 成品样例不纳入本项目的 MIT 授权范围；其中题目、图示、引文等内容仍保留原有权利。详见 [第三方与样例说明](THIRD_PARTY_NOTICES.md)。
