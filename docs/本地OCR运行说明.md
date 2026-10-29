# 扫描 PDF 本地 OCR

本轮把 `OcrAdapter.recognize()` 从占位改为本地 RapidOCR/ONNX 推理，并接入 ParseService。PDFium 渲染无文本层页面，OCR 结果保留原件哈希、页码、0—1000 左上原点检测框和引擎分数。既有 Evidence Schema 不变，`extraction_method=ocr`；OCR 文档的解析清单始终 `needs_review=true`，不自动确认财务事实。

## 安装和运行

使用 Python 3.11 或 3.12（本轮实际为 3.12）；固定 ONNX Runtime 要求 Python >=3.11。联网准备阶段在项目根目录安装，运行识别不需要云端 API 或模型密钥：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[pdf,ocr]'
export FINSCOPE_OCR_ENABLED=1
export FINSCOPE_OCR_MAX_PAGES=20
export FINSCOPE_DATA_DIR=var/ocr-demo
python -m app.cli ingest '关于举办2026年北京市大学生金融人工智能竞赛的通知.pdf' --parse
```

Windows 使用 `.venv\Scripts\Activate.ps1` 激活环境，并通过 `$env:FINSCOPE_OCR_ENABLED='1'`、`$env:FINSCOPE_DATA_DIR='var/ocr-demo'` 配置。已安装本机 `vendor/python` 的环境也可通过 `PYTHONPATH=vendor/python` 调用；本机可选依赖已安装到此忽略目录，Git 不携带依赖或权重。

Web 使用相同配置启动 `python -m demo.live --data-dir var/ocr-demo`，上传扫描 PDF 后复用同一解析服务；云端模型是否启用由原有独立配置决定。OCR 可独立于模型服务工作。

`app.ocr.describe()` 报告开关、依赖及模型文件是否就绪，模型首次调用才加载。只检测到 Tesseract 可执行文件不再报告本适配器可用。固定本地三个 ONNX 文件均须存在；显式指定文件路径，缺模型报错，不在业务识别时自动下载。解析清单记录 RapidOCR、ONNX Runtime、PDFium 版本和模型文件组合 SHA-256。

## 处理边界

- 默认关闭 OCR，只对完全没有文字的页识别；原生文本页保留原解析结果。部分文字页中的图片区域暂不触发 OCR。
- 每份文件默认最多处理 20 个无文本页，配置必须为 1—300。每页渲染长边不超过 2400 像素，图像不超过 600 万像素；旋转与 CropBox 以 PDFium 实际渲染页面为准。默认不做多页并行。
- 部分页失败、空白或超过页数上限时，已有原生／OCR 文字可返回 parsed，但必须保留部分解析与复核警告。整份无文字则返回 `DOCUMENT_PARSE_FAILED`，不冒充成功。
- 保留低分识别候选，低于 0.6 额外警告；分数来自 OCR，引擎高分不等于财务数字正确。跨多个检测框的引用采用最低置信度。
- 当前实现是文字与检测框识别，不是扫描表格结构恢复。表头、合并单元格、相邻列对应关系、跨页表格和文字遗漏仍需复核；不能把检测框宣传成已还原的表格单元格。
- 原生文本缓存继续使用，切换开关不自动覆盖历史解析。验证新引擎优先使用新的 data-dir；确需重新解析时 CLI 支持 `parse DOCUMENT_ID --force`，先保存旧版本及其运行条件。

## 本轮实际样例（2026-10-15）

1. 仓库中的 9 页扫描竞赛通知，经产品 `upload_path → parse_document → Evidence` 完成识别：9 页均有 OCR 证据、170 条证据，2 条低于 0.6，parsed 且 needs_review。约 19.73 秒只是本机单次观察，不是性能基准或准确率。
2. 将已归档的真实欣旺达 2026-09-22 两页质押公告栅格化为无文本 PDF 副本：得到 153 条 OCR 证据、页码／坐标／Evidence 契约通过，重复解析缓存一致；原件和派生副本分别保存哈希。该扫描副本在 rules 提取模式得到 0 条交易记录并明确待复核，说明识别出文字不代表复杂公告字段提取已通过，不能作为“扫描表格全自动提取成功”宣传。
3. 混合原生文本页、旋转 90 度扫描页、空白页：原生页保留 pdf_text，旋转页产生 ocr，空白页没有虚构证据且有部分解析警告。
4. OCR 关闭时扫描副本返回 DOCUMENT_PARSE_FAILED；全空白 PDF 即使启用 OCR 也返回该错误。均未改变原财务清单或人工复核记录。

原始识别与定位示意保存在忽略目录 `var/ocr_acceptance_20261015/`，包括 `notice_result.json`、`pledge_result.json`、`pipeline_proof.json`、`pledge_page1_boxes.png`。截图由程序按返回 bbox 绘制，并经 AI 查看；不冒称真人标注或独立准确率。本轮仅做这些集成样例与契约检查，不将此前整仓环境受限的回归结果改写为通过。

## 依赖和许可

固定主依赖：RapidOCR 3.9.2（Apache-2.0）、ONNX Runtime 1.30.0（MIT）、pypdfium2 5.14.0（BSD-3-Clause、Apache-2.0 及其 PDFium 依赖许可）；Pillow 在安装约束内，本机为 12.3.0。来源分别为 [RapidOCR](https://github.com/RapidAI/RapidOCR)、[ONNX Runtime](https://github.com/microsoft/onnxruntime)、[pypdfium2](https://github.com/pypdfium2-team/pypdfium2)、[Pillow](https://github.com/python-pillow/Pillow)。用于本地文字识别和 PDF 栅格化；本仓库不提交第三方模型或二进制。

依赖下载受本机外网限制时，可以使用预先下载的完整 wheel/model 包离线安装；本轮实际利用本机 pip 缓存安装完整模型后验证，没有把网络安装失败当作识别成功。分发依赖或模型时应一并保留随包许可证、第三方 NOTICE 和模型来源说明。
