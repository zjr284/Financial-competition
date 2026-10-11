# 成员 A 模块运行说明

> 适用范围：`api/`、`app/ingest/`、`app/parse/`、`app/evidence/`、`app/orchestrator/`、`app/models/`、`app/logging/` 与选题 1 `structured_extraction`。
> 对应契约：`api_version = 1.0.0`（`contracts/contracts.schema.json`、`contracts/openapi.yaml`）。
> 计划与前置条件见 [成员A任务实施计划](成员A任务实施计划.md)；数据对象疑虑见 [接口问题记录](接口问题记录.md)。

---

## 1. 三分钟跑通（推荐顺序）

在项目根目录执行：

```powershell
# 1) 必需依赖（契约校验与 JSON Schema 校验）
python -m pip install -r contracts/requirements.txt

# 2) 可选依赖：PDF 逐字符坐标定位 + XLSX 读取加速
python -m pip install --target vendor/python "pypdf==6.20.0" "pdfminer.six==20260107"

# 3) 端到端自检：登记 -> 解析 -> 结构化提取 -> 离线重放
python -m app.cli selftest

# 4) 契约校验（必须通过）
python contracts/validate_contracts.py
# 期望输出：Contract validation passed: 7 requests, 7 results, 7 entities, 10 API paths

# 5) 单元测试（86 项）
python -m pytest tests/
```

`selftest` 的正常输出形如：

```json
{
  "parse": {"status": "parsed", "backend": "text:utf-8-sig", "evidence_count": 16},
  "run": {"status": "succeeded", ...},
  "record_count": 1,
  "review_items": 0,
  "evidence_count": 8,
  "replay_ok": true,
  "run_dir": "...\\var\\runs\\run_XXXXXXXX"
}
```

**`replay_ok: true` 是关键验收项**：它表示同一输入重跑得到同一 `input_hash`、同一 `result_hash`、同一工具调用轨迹。

---

## 2. 环境与目录

| 项 | 说明 |
|---|---|
| Python | ≥ 3.10（本机验证 3.11.4） |
| 必需依赖 | `jsonschema`、`PyYAML`（见 `contracts/requirements.txt`） |
| 可选依赖 | `pdfminer.six`、`pypdf`（PDF 坐标）、`openpyxl`（XLSX）；缺失时自动降级 |
| 运行数据 | `var/`：`metadata.sqlite3`（元数据）、`blobs/<前两位>/<sha256>`（原始文件快照）、`runs/<run_id>/`（运行清单） |
| 可选依赖目录 | `vendor/python/`，由 `app/bootstrap.py` 自动加入 `sys.path` |
| 样例包 | `samples/`（3 类公告成功样例、1 例失败/待复核样例、1 例表格、2 例 PDF） |

`var/` 与 `vendor/` 已在 `.gitignore` 中，不会误提交运行数据或第三方包。

---

## 3. 部署限制（接口约定要求写入说明）

具体数值由部署配置设定，全部可用环境变量覆盖：

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `FINSCOPE_DATA_DIR` | `<仓库>/var` | 运行数据根目录 |
| `FINSCOPE_MAX_FILE_MB` | `50` | 单文件上限（MB），超出返回 `FILE_TOO_LARGE`(413) |
| `FINSCOPE_MAX_PDF_PAGES` | `300` | 单 PDF 页数上限，超出返回 `FILE_TOO_LARGE`(413) |
| `FINSCOPE_OCR_ENABLED` | `0` | 是否允许 OCR；无引擎时置 1 也不会生效 |
| `FINSCOPE_MODEL_ID` | `rule-based-offline` | 写入 Run 的模型标识 |
| `FINSCOPE_MODEL_REVISION` | `v1` | 模型修订号 |
| `FINSCOPE_PROMPT_VERSION` | `prompt-v1` | 提示词版本 |
| `FINSCOPE_CODE_VERSION` | `member-a-0.1.0` | 代码版本 |

上传白名单：`.pdf`、`.txt`、`.csv`、`.xlsx`；其他扩展名返回 `DOCUMENT_UNSUPPORTED`(415)。

---

## 4. 命令行

```powershell
python -m app.cli serve --host 127.0.0.1 --port 8000     # 启动 HTTP 服务
python -m app.cli ingest <文件> --disclosure-date 2026-09-01 --parse   # 登记（可选立即解析）
python -m app.cli parse <document_id> [--force]          # 解析已登记文档
python -m app.cli run structured_extraction --documents <doc_id> --announcement-types pledge bid_award equity_change
python -m app.cli replay <run_id>                        # 离线重放并比对哈希
python -m app.cli events <run_id>                        # 事件分页 + 哈希链校验
python -m app.cli evidence <evidence_id>                 # 单条证据（页码、bbox、片段）
python -m app.cli verify-contracts                       # 调用 contracts/validate_contracts.py
python -m app.cli selftest                               # 端到端自检
```

---

## 5. HTTP 接口

启动后基础地址为 `http://127.0.0.1:8000/api/v1`。

### 5.1 契约内端点（10 条，见 `contracts/openapi.yaml`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务与模型状态、解析后端、OCR 可用性、部署限制 |
| POST | `/documents` | `multipart/form-data`：`file`（必填）、`source_url`、`disclosure_date` |
| GET | `/documents/{document_id}` | 文件元数据（含 SHA-256、解析状态） |
| GET | `/documents/{document_id}/content` | 原始字节流，`ETag` 为 SHA-256 |
| POST | `/runs` | 启动七题之一，返回 `202 Run` 后异步执行 |
| GET | `/runs/{run_id}` | 任务状态 |
| GET | `/runs/{run_id}/result` | `succeeded`/`needs_review` 时返回 `TaskResult`；运行中返回 409 |
| GET | `/runs/{run_id}/events` | 事件分页，支持 `cursor`、`limit` |
| GET | `/evidence/{evidence_id}` | 证据原文位置与片段 |
| POST | `/reviews/{review_id}/decision` | 人工接受/驳回/修正 |

### 5.2 A 新增的 3 条端点（**尚未写入 OpenAPI，待三人确认**，见接口问题记录 Q6）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents/{document_id}/parse` | 触发解析，返回 `ParseManifest`（证据数、warnings、backend） |
| GET | `/documents/{document_id}/evidence` | 列出文档全部证据（可按 `page` 过滤） |
| POST | `/runs/{run_id}/replay` | 离线重放并返回哈希/轨迹比对结论 |

> 联调期可用，但**不得**对外声称它们属于已冻结契约。

### 5.3 curl 示例

```powershell
# 上传
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/documents" `
  -F "file=@samples/announcement_pledge_demo_cjk.pdf" `
  -F "disclosure_date=2026-09-01"

# 解析并取证据
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/documents/<doc_id>/parse"
curl.exe -s "http://127.0.0.1:8000/api/v1/documents/<doc_id>/evidence"

# 跑选题 1
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/runs" -H "Content-Type: application/json" -d "{\"api_version\":\"1.0.0\",\"task_type\":\"structured_extraction\",\"document_ids\":[\"<doc_id>\"],\"task_input\":{\"announcement_types\":[\"pledge\",\"bid_award\",\"equity_change\"]}}"

# 查结果与事件
curl.exe -s "http://127.0.0.1:8000/api/v1/runs/<run_id>/result"
curl.exe -s "http://127.0.0.1:8000/api/v1/runs/<run_id>/events?limit=50"
```

所有响应都带 `X-Request-Id`；错误响应统一为契约 `Error` 结构 `{code, message, details, retryable, run_id}`。

---

## 6. 样例与预期结果

| 样例 | 用途 | 预期 |
|---|---|---|
| `samples/announcement_pledge_demo.txt` | 质押公告成功样例 | 1 条记录，6 个字段全部 `verified`，各带 1 条证据 |
| `samples/announcement_bid_demo.txt` | 中标公告成功样例 | `中标金额：人民币23,500万元` → `235000000` / `CNY` |
| `samples/announcement_equity_demo.txt` | 股权变动成功样例 | `2,400万股` → `24000000` / `share`；`12.00%` → `12` / `percent` |
| `samples/announcement_pledge_incomplete_demo.txt` | **失败/待复核样例** | 缺失字段保留、`value=null`、`status=needs_review`、`reason` 说明；Run 状态 `needs_review` |
| `samples/pledge_table_demo.csv` | 表格型资料 | 表头用字段代码，2 个数据行 → 2 条记录，证据带 `row`/`column` |
| `samples/announcement_pledge_demo.pdf` | ASCII PDF 坐标样例 | 用于验证纯标准库后端 |
| `samples/announcement_pledge_demo_cjk.pdf` | **中文 PDF 原点坐标样例** | 字段证据页码 1、bbox 可框选 |

> **样例声明：** 上述样例均为自编的接口联调数据（公司代码 `999999`），**不是竞赛实测成果**，页面与视频展示时必须标注"接口演示样例"。

批量回归：

```powershell
python tools/smoke_extraction.py
```

---

## 7. 运行清单与重放

每次 Run 在 `var/runs/<run_id>/` 下产出：

| 文件 | 内容 |
|---|---|
| `manifest.json` | Run 元数据、`task_input`、部署限制、输入捆绑（含每个文档的 SHA-256） |
| `events.jsonl` | 逐条事件（含 `prev_hash`、`chain_hash`） |
| `result.json` / `result.needs_review.json` | 结构化结果 |
| `error.json` | 失败时的契约 `Error` |
| `trace.json` | `(事件类型, 工具名, 输入哈希, 输出哈希)` 轨迹 + 哈希链校验结果 |

重放判定依据：

- `input_hash_match`：输入文件哈希 + `task_input` + 代码/提示词版本一致；
- `result_hash_match`：结果内容一致（哈希基**不含** `run_id` 与 `generated_at`）；
- `trace_match`：工具调用轨迹一致（按"事件类型 + 输入哈希 + 输出哈希"比对，不含工具名）；
- `chain.ok`：事件哈希链未被改写。

---

## 8. 测试与契约校验

```powershell
python -m pytest tests/ -q          # 86 项
python contracts/validate_contracts.py
```

测试分布：

| 文件 | 覆盖 |
|---|---|
| `tests/test_text_and_ids.py` | 中文数字/量纲/百分比/日期归一化、十进制字符串契约、bbox 翻转与排序、稳定 ID |
| `tests/test_ingest_parse.py` | 稳定登记 ID、更正公告新版本、格式/大小校验、PDF 坐标抽查、无文本层失败、CSV 行列、纯标准库后端、阅读顺序 |
| `tests/test_structured_extraction.py` | 三类公告成功样例、失败/待复核样例、单位与百分比、证据指向原文、表格路径、`event_id` 契约形状 |
| `tests/test_orchestrator.py` | Run 状态机白名单、TaskResult 校验、事件类型与哈希链、分页、离线重放、两次运行同哈希、人工复核回写 |
| `tests/test_api.py` | 13 条路由、错误码映射（400/404/409/413/415）、`X-Request-Id`、`ETag`、multipart、真实 TCP 往返 |

---

## 9. 已知限制与故障处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动正常但 `health.status = degraded` | 未装 `pdfminer.six`/`pypdf` | 按第 1 节第 2 步安装到 `vendor/python`；或接受纯标准库后端（仅支持 FlateDecode 文本层） |
| PDF 解析返回 `DOCUMENT_PARSE_FAILED`(422) | 该 PDF 无文本层（扫描件），且当前环境无 OCR 引擎 | 补装 OCR 引擎并置 `FINSCOPE_OCR_ENABLED=1`，或人工录入；系统不会假装解析成功 |
| TXT/CSV/XLSX 证据的 bbox 只近似 | 这些格式没有原生坐标 | 结合 `extraction_method`、`confidence`(0.7/0.95) 与 `warnings` 展示，**不得**当作原件框选位置 |
| 某字段 `value=null` + `needs_review` | 原文确实没有该字段或写的是"详见附件" | 走 `POST /reviews/{id}/decision` 接受/驳回/修正；系统不会填 0 或臆测 |
| `POST /runs` 返回 202 后立刻查 result 得到 409 | 任务仍在运行 | 轮询 `GET /runs/{id}` 直到 `succeeded`/`needs_review`/`failed` |
| 其他六题返回 500/失败并提示主责成员 | 对应选题由 B/C 实现，尚未接入 | 属预期行为：接口形状可用，业务实现待接入，系统不会返回编造结果 |

---

## 10. 与本仓库其他文档的关系

| 文档 | 用途 |
|---|---|
| `docs/成员A任务实施计划.md` | 范围、前置条件、依赖、里程碑、风险 |
| `docs/接口问题记录.md` | Q1—Q8 契约歧义与建议（改契约前必须先记录） |
| `docs/成员A交接单.md` | 按仓库统一格式的交接材料（成功/失败样例、日志位置、未解决问题） |
| `contracts/` | 公共接口唯一事实来源 |
