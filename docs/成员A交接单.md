# 成员 A 交接单

> 按 `docs/给成员和AI助手的任务说明.md` 第 1 节统一格式：功能名称、输入／输出对应的 Schema 定义、使用的资料、运行命令、成功样例、失败样例、日志位置、人工复核结论、未解决问题。
> 契约版本 `1.0.0`｜代码版本 `member-a-0.1.0`｜验证环境 Windows + Python 3.11.4

---

## 0. 交付清单

| # | 功能名称 | 输入 Schema | 输出 Schema | 状态 |
|---|---|---|---|---|
| 1 | 文件登记与内容快照（`app/ingest/`） | `multipart/form-data` | `Document` | ✅ 已验证 |
| 2 | 文档解析与坐标定位（`app/parse/`） | `document_id` | `Evidence`、`ParseManifest` | ✅ 已验证 |
| 3 | 证据索引与查询（`app/evidence/`） | `evidence_id` / `document_id` | `Evidence` | ✅ 已验证 |
| 4 | 事件日志与运行清单（`app/logging/`） | `run_id` | `Event`、`EventPage` | ✅ 已验证 |
| 5 | 模型适配与 OCR（`app/models/`） | `ModelPort` 签名 | `ModelCallRecord` | ✅ 已验证（离线后端） |
| 6 | 任务编排与离线重放（`app/orchestrator/`） | `RunRequest` | `Run`、`TaskResult` | ✅ 已验证 |
| 7 | HTTP 服务（`api/`） | OpenAPI 定义 | 各端点响应 | ✅ 已验证 |
| 8 | 选题 1 结构化提取（`app/tasks/structured_extraction/`） | `ExtractionInput` | `ExtractionData` | ✅ 已验证 |

**汇总验收结果：** `python -m pytest tests/` → **86 passed**；`python contracts/validate_contracts.py` → **Contract validation passed: 7 requests, 7 results, 7 entities, 10 API paths**；`python -m app.cli selftest` → `replay_ok: true`。

---

## 1. 功能：文件登记与内容快照

**输入／输出 Schema：** 入参 `multipart/form-data`（`file` 必填，可选 `source_url`、`disclosure_date`）→ 出参 `$defs.Document`。

**使用的资料：** `samples/announcement_pledge_demo.txt`、`samples/announcement_pledge_demo_cjk.pdf`、`samples/pledge_table_demo.csv`（自编接口演示样例）。

**运行命令：**

```powershell
python -m app.cli ingest samples/announcement_pledge_demo.txt --disclosure-date 2026-09-01 --parse
```

**成功样例：** 同一文件重复登记返回同一 `document_id`；同内容不同 `disclosure_date` 返回新 `document_id` 但共享同一内容寻址快照。

```json
{"document_id": "doc_H2ASLPP2HH2LMFOTHSMP", "filename": "announcement_pledge_demo.txt",
 "media_type": "text/plain", "sha256": "b8a6fe356714bee61ed43a3cf78addfe8b9648123446c4a0c4150bc4999bfe23",
 "source_url": null, "disclosure_date": "2026-09-01", "page_count": 1, "parse_status": "pending"}
```

**失败样例：** 上传 `.exe` → `DOCUMENT_UNSUPPORTED`(415)；超过 `FINSCOPE_MAX_FILE_MB` → `FILE_TOO_LARGE`(413)；`disclosure_date=2026/09/01` → `VALIDATION_ERROR`(400)。三者响应体均通过 `$defs.Error` 校验。

**日志位置：** `var/runs/run_ingest_<文档ID后缀>/events.jsonl`（登记阶段无 Run，使用稳定别名，见未解决问题 Q1）。

**人工复核结论：** ⬜ 待成员 B/C 复核（本模块无金融结论，仅需确认"同一文件稳定 ID"与"更正公告不覆盖旧文件"两条规则符合预期）。

---

## 2. 功能：文档解析与坐标定位

**输入／输出 Schema：** 输入 `document_id` → 输出 `ParseManifest`（`evidence_ids`、`warnings`、`page_count`、`status`、`backend`）与 `$defs.Evidence`。

**使用的资料：** 上表三类样例 + `samples/announcement_pledge_demo.pdf`（ASCII 文本层）。

**运行命令：**

```powershell
python -m app.cli parse <document_id> --force
python -m app.cli evidence <evidence_id>
python tools/smoke_extraction.py          # 全部样例批量回归
```

**成功样例（中文 PDF 原件坐标，来源定位输出）：**

样例文档 `samples/announcement_pledge_demo_cjk.pdf` 由 `tools/make_sample_pdf_cjk.py` 生成，页 595×842pt，正文自 x=56 起、基线 780pt、行距 18pt、字号 11pt。解析结果（证据片段带 ±20 字上下文）：

| 字段 | 证据 ID | 页码 | bbox（左上角原点，0—1000） | 方法 | 置信度 | 原文片段（截断） |
|---|---|---|---|---|---|---|
| `company_code` | `ev_WMG4JGPJZNNBNEB4PNQD` | 1 | `[94, 85, 649, 98]` | `pdf_text` | 1.00 | 示例公司股权质押公告 证券代码：999999… |
| `shareholder` | `ev_BXBGAVTFWYOF7WOVVOD6` | 1 | `[94, 127, 353, 140]` | `pdf_text` | 1.00 | …出质人：示例控股集团有限公司 质权人：… |
| `pledgee` | `ev_RVTTM7JBQLQ2VKSK4563` | 1 | `[94, 149, 427, 162]` | `pdf_text` | 1.00 | …质权人：示例银行股份有限公司城中支行… |
| `pledged_shares` | `ev_HI2YIAQKI4WSKIFZPX3C` | 1 | `[94, 170, 307, 183]` | `pdf_text` | 1.00 | …本次质押股数：1,000万股… |
| `pledged_ratio` | `ev_KTKTM4HMHCQQOIVEGOCL` | 1 | `[94, 191, 307, 205]` | `pdf_text` | 1.00 | …占公司总股本比例：5.20%… |
| `pledge_start_date` / `event_date` | `ev_PG5XRBJTNAPKJGDAWDZY` / `ev_P72GPVNRAC3XDZYOQSDJ` | 1 | `[94, 213, 325, 226]` | `pdf_text` | 1.00 | …质押起始日：2026年8月20日… |
| `pledge_end_date` | `ev_NC7GQLWBHYC6EOG46BTQ` | 1 | `[94, 234, 325, 247]` | `pdf_text` | 1.00 | …质押到期日：2027年8月19日… |

**坐标可验证性：** 以 `pledgee` 所在行为例，基线 y=708pt、字号 11pt：

* 归一化上边界 = (842 − (708 + 9)) / 842 × 1000 ≈ **149**（实测 149）；
* 归一化下边界 = (842 − 708) / 842 × 1000 ≈ **159**（实测 162，含字形下沉）。

即"页面坐标 → 契约 bbox"的换算可手算复核，评审可在原件上按此框选。

**失败／待复核样例：**

* 无文本层的 PDF → `DOCUMENT_PARSE_FAILED`(422)，`message` 提示该 PDF 无文本层且 OCR 不可用，**不返回空结果冒充成功**；
* TXT/CSV/XLSX → 能生成证据，但 bbox 是按行／行列推导的**近似版面**，`confidence` 分别为 0.70／0.95，并在 `warnings` 与文档解析状态中显式声明。

**日志位置：** `var/runs/<run_id>/events.jsonl` 中的 `file_access / parse_document` 与 `file_access / load_index` 两条事件；解析产物缓存于 `documents.parse_manifest`（下游任务不再重复解析原文件）。

**人工复核结论：** ⬜ 待成员 C 用前端在原件上框选核对（需 Q4 的近似坐标说明先确认）。

---

## 3. 功能：事件日志与运行清单

**输入／输出 Schema：** 输出 `$defs.Event`、`$defs.EventPage`。

**运行命令：** `python -m app.cli events <run_id>`

**成功样例（真实运行 `run_X4JHN5RQYNHZ7ADIJTSO` 的事件流）：**

```
state        create_run                   创建任务 structured_extraction，输入文档 1 份
state        execute_run                  任务开始运行
file_access  parse_document               解析文档 doc_AFTPNP3HPEIG77LGLD4N（pdfminer），提示 0 条
file_access  load_index                   读取文档 doc_AFTPNP3HPEIG77LGLD4N 的解析索引
tool_call    task:structured_extraction   调用任务插件 structured_extraction
tool_call    task:structured_extraction.done  任务插件返回 0 条待复核项
state        execute_run                  任务结束：succeeded
```

**运行清单目录：** `var/runs/<run_id>/` → `manifest.json`、`events.jsonl`、`result.json`（或 `result.needs_review.json`）、`error.json`、`trace.json`。

**失败样例：** 手工改写任一事件后 `verify_chain` 返回 `{"ok": false, "broken_at": <seq>}`（见 `tests/test_orchestrator.py::test_event_chain_detects_tampering`）。

**日志安全：** 单条消息截断至 240 字，不写完整文档文本、模型响应或密钥；只保留文件 ID、SHA-256、调用摘要、版本与结果哈希。

**人工复核结论：** ⬜ 待成员 B/C 确认事件粒度是否满足前端"运行状态可视化"需要。

---

## 4. 功能：任务编排与离线重放

**输入／输出 Schema：** `$defs.RunRequest` → `$defs.Run`、`$defs.TaskResult`。

**运行命令：**

```powershell
python -m app.cli run structured_extraction --documents <doc_id> --announcement-types pledge bid_award equity_change
python -m app.cli replay <run_id>
```

**成功样例（重放判定）：**

```json
{"input_hash_match": true, "result_hash_match": true, "trace_match": true,
 "chain": {"ok": true, "checked": 6, "broken_at": null},
 "result_original_matches": true,
 "hash_basis": "result_hash 不含 run_id/generated_at；轨迹比对不含工具名，只比对输入/输出哈希"}
```

（`checked` 为该 Run 的事件总数：单文档、首次解析的场景下为 6 条。）

**同一输入两次独立运行：** `result_hash` 完全相同（`61e8dd1a…318d63`），`run_id` 不同 —— 这是"固定输入能复现"的直接证据。

**失败样例：**

* 未接入的选题（如 `financial_analysis`）→ Run 状态 `failed`，`error.code = MISSING_INPUT`，`message` 注明主责成员 B，并写 `error.json`；**不返回编造结果**；
* 非法状态转换（终态再转 `running`）→ `VALIDATION_ERROR`；
* 运行中读取结果 → `RUN_NOT_READY`(409)。

**日志位置：** `var/runs/<run_id>/trace.json`（轨迹 + 哈希链校验）、`manifest.json`（输入捆绑，含每份文档 SHA-256）。

**人工复核结论：** ⬜ 待成员 B/C 确认"非法状态转换"与"未接入选题"的失败语义是否符合各自模块预期。

---

## 5. 功能：选题 1 结构化提取

**输入／输出 Schema：** `$defs.ExtractionInput`（`announcement_types` ⊆ `pledge`/`bid_award`/`equity_change`）→ `$defs.ExtractionData`（`records[].fields[]` 为 `$defs.FieldValue`）。

**字段字典（v1，与 `docs/接口约定.md` 第 4 节一致）：**

| 公告类型 | 字段 |
|---|---|
| `pledge` | `shareholder, pledged_shares, pledged_ratio, pledge_start_date, pledge_end_date, pledgee` |
| `bid_award` | `project_name, winning_party, contract_amount, currency, performance_period` |
| `equity_change` | `holder, shares_before, shares_after, ratio_before, ratio_after, change_method` |
| 记录级（固定） | `document_id, company_code, event_date` |

**运行命令：**

```powershell
python -m app.cli run structured_extraction --documents <doc_id> --announcement-types pledge
```

### 5.1 成功样例（同一 PDF，`run_COMJGFPHZNAQFOD4GN73`，`status=succeeded`）

```
event_id event_XDGP2FBOJGRZBVUPTYDN   company_code 999999   event_date 2026-08-20
  document_id          raw=doc_AFTPNP3HPEIG77LGLD4N  value=doc_AFTPNP3HPEIG77  unit=none     verified   -
  company_code         raw=999999                    value=999999              unit=none     verified   ev_WMG4JGPJZNNBNEB4PNQD
  event_date           raw=2026年8月20日                value=2026-08-20          unit=none     verified   ev_P72GPVNRAC3XDZYOQSDJ
  shareholder          raw=示例控股集团有限公司            value=示例控股集团有限公司      unit=none     verified   ev_BXBGAVTFWYOF7WOVVOD6
  pledged_shares       raw=1,000万股                  value=10000000            unit=share    verified   ev_HI2YIAQKI4WSKIFZPX3C
  pledged_ratio        raw=5.20%                     value=5.2                 unit=percent  verified   ev_KTKTM4HMHCQQOIVEGOCL
  pledge_start_date    raw=2026年8月20日                value=2026-08-20          unit=none     verified   ev_PG5XRBJTNAPKJGDAWDZY
  pledge_end_date      raw=2027年8月19日                value=2027-08-19          unit=none     verified   ev_NC7GQLWBHYC6EOG46BTQ
  pledgee              raw=示例银行股份有限公司城中支行       value=示例银行股份有限公司城中支行  unit=none     verified   ev_RVTTM7JBQLQ2VKSK4563
```

**归一化要点（可独立复算）：**

| 原值 | 归一值 | 单位 | 说明 |
|---|---|---|---|
| `1,000万股` | `10000000` | `share` | 量纲"万"换算为股 |
| `5.20%` | `5.2` | `percent` | **百分数值**，不是 0.052 |
| `人民币23,500万元` | `235000000` | `CNY` | 币种前缀剥离 + 万→元 |
| `2,400万股` | `24000000` | `share` | — |
| `12.00%` | `12` | `percent` | — |
| `2026年8月20日` | `2026-08-20` | `none` | — |

**表格型资料：** `samples/pledge_table_demo.csv` 表头用字段代码（`pledged_shares` 等），2 个数据行 → 2 条记录，证据带 `table_id`/`row`/`column`（行列从 1 开始）。

### 5.2 失败／待复核样例（`samples/announcement_pledge_incomplete_demo.txt`，`status=needs_review`）

原文明写 `本次质押股数：详见附件`、`占公司总股本比例：详见附件`，且缺质权人与起止日期：

```
  shareholder          raw=示例控股集团有限公司   value=示例控股集团有限公司  verified
  pledged_shares       raw=None               value=None              needs_review  reason=未在原文中定位到「质押股数」，需人工复核原文
  pledged_ratio        raw=None               value=None              needs_review  reason=未在原文中定位到「质押比例」，需人工复核原文
  pledge_start_date    raw=None               value=None              needs_review  reason=…
  pledge_end_date      raw=None               value=None              needs_review  reason=…
  pledgee              raw=None               value=None              needs_review  reason=…
review_items: review_WGEIL7F72H3YMY3GOP4V → target_id=event_QI6OXT3MFSSPR5DI36LP
```

**人工复核结论（本模块自检）：** ✅ 通过
**复核人：** 成员 A（自检，**非**独立复核）

* 缺失字段**保留字段位**、`value=null`、`status=needs_review`、`reason` 说明原因，**未填 0、未臆测**；
* 样例中解释性文字（`说明：本样例故意缺失质权人…`）**没有**被当作质权人的取值；
* 只指定 `announcement_types=["bid_award"]` 但资料是质押公告时，返回空 `records` + `review_items` + `warnings`，**不把空结果当成功**。

⚠️ **本模块的金融字段取值仍须由成员 B 对照原始披露逐项复核**，标注规范与留出集评测由成员 C 负责（`docs/项目总纲.md` 第 1 节分工）。

**日志位置：** `var/runs/<run_id>/`（该样例产出 `result.needs_review.json`）。

---

## 6. 运行环境与复现

| 项 | 内容 |
|---|---|
| 一条命令 | `python -m app.cli selftest` |
| 契约校验 | `python contracts/validate_contracts.py` → 7 requests / 7 results / 7 entities / 10 API paths |
| 测试 | `python -m pytest tests/` → 86 passed |
| 依赖锁定 | `requirements.lock`（`jsonschema==4.17.3`、`PyYAML==6.0`、`pytest==7.4.0`；可选 `pdfminer.six==20260107`、`pypdf==6.20.0`、`openpyxl==3.0.10`） |
| 部署限制 | `FINSCOPE_MAX_FILE_MB=50`、`FINSCOPE_MAX_PDF_PAGES=300`、上传白名单 PDF/TXT/CSV/XLSX |
| 运行说明 | [成员A运行说明](成员A运行说明.md) |

---

## 7. 未解决问题

### 7.1 待三人确认的契约歧义（详见 [接口问题记录](接口问题记录.md)）

| 编号 | 摘要 | 影响 |
|---|---|---|
| Q1 | 登记阶段无 Run，`Event.run_id` 用稳定别名 | 事件流的可读性 |
| Q2 | 提取记录 `event_id` 放入 `event_` 家族以便复核项引用 | B/C 引用记录的方式 |
| Q3 | 非分页来源的 `Document.page_count` 语义（TXT=1、CSV/XLSX=行数） | 跨格式一致性 |
| Q4 | 非 PDF 来源的 bbox 为近似版面，建议契约补注 | 前端框选准确性 |
| Q5 | 提取类任务是否需写 `calculations`（当前空数组） | 结果语义 |
| Q6 | A 新增 3 条端点未入 OpenAPI，`validate_contracts.py` 断言 `len(paths)==10` | 契约完整性 |
| Q7 | `X-Request-Id` 由服务端生成而非回显 | 排查一致性 |
| Q8 | `ExtractionInput` 无 `company_code`，占位值 `000000` 的处理方式 | 多主体公告易错 |

### 7.2 本模块技术未完成项

| 项 | 说明 | 影响 |
|---|---|---|
| 失败恢复策略 | 进程中断后 `running` 悬挂态尚无超时改判 | 长时间运行的任务需人工干预 |
| 重试与退避计数 | `attempt` 字段已预留，未实现自动重试写回 | 模型调用失败需重跑 |
| `metrics.json` | `实施路线与验收清单.md` 第 2 节要求的指标文件未产出 | 运行清单不完整 |
| 导出（`export` 事件） | JSON/CSV/HTML 导出未实现 | 结果只能通过 API 获取 |
| 跨页表格 | 表头与数据行分页时尚未合并 | 真实年报回归待验证 |
| 旋转页坐标 | PDF `/Rotate` 非 0 时的坐标校正未验证 | 部分扫描件坐标可能偏移 |
| 页数超限用例 | 未构造超页数 PDF 的自动化测试 | 边界覆盖不足 |

### 7.3 环境侧限制

| 项 | 说明 |
|---|---|
| 无 OCR 引擎 | 未检测到 tesseract/pytesseract；扫描件只能返回 `DOCUMENT_PARSE_FAILED` + 人工录入提示。若评测集含扫描公告，需先补齐 OCR 依赖 |
| 可选依赖未装系统环境 | `pdfminer.six`/`pypdf` 装在 `vendor/python`，缺失时自动退回纯标准库后端（仅支持 FlateDecode 文本层） |
| 中文 PDF 样例依赖宿主字体 | `tools/make_sample_pdf_cjk.py` 需要 `C:/Windows/Fonts/simhei.ttf` 等；缺失时跳过生成并返回 0 |

### 7.4 样例声明

`samples/` 下全部样例均为**自编接口联调数据**（公司代码 `999999`），`任务输出不得作为竞赛实测成果`。真实公司资料到位后需重新回归，并按 `实施路线与验收清单.md` 第 4 节补充留出集评测。

---

## 8. 给其他成员的接口交接说明

**给成员 B（财务口径）：**
* `Evidence` 与 `Document` 服务可直接用：`app.parse.get_evidence(evidence_id)`、`app.parse.list_evidence(document_id)`；
* 财务事实抽取建议复用解析索引（`app.parse.build_index(document_id)`）与 `app/common/text.py` 的归一化函数，避免各自实现不同拼法；
* `Fact`/`Calculation` 的校验位在 `app/common/contracts.py`（`validate_contract("Fact", ...)`），落地时请直接调用，不要自行放宽。

**给成员 C（前端与评测）：**
* 前端证据定位所需字段：`GET /evidence/{id}` 返回 `page`、`bbox`（0—1000 左上角原点）、`text` 短片段、`extraction_method`、`confidence`；完整文件走 `GET /documents/{id}/content`（`ETag` = SHA-256）；
* 实时运行状态：`GET /runs/{id}` 轮询 + `GET /runs/{id}/events` 事件流；
* 复核交互：`POST /reviews/{id}/decision`（`accept`/`reject`/`correct`），`correct` 会回写字段值并重算 `result_hash`；
* A 新增的 `/documents/{id}/parse`、`/documents/{id}/evidence`、`/runs/{id}/replay` **尚未写入 OpenAPI**（Q6），前端依赖前请先在三人评审确认。
