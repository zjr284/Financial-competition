# 给三位成员和各自 AI 助手的任务说明

## 1. 协作方式

三人先共同确认 `contracts/`，之后按模块并行开发。每次向 AI 助手发任务时，附上本仓库路径、`AGENTS.md`、`docs/接口约定.md`、`contracts/contracts.schema.json` 和对应样例。让 AI 只完成一个边界明确的子任务，要求列出改动文件、接口匹配方式、验证命令和已知限制。AI 生成的金融结论须由成员 B 核对原始披露；AI 生成的评测数字须由成员 C 从留出集脚本验证。

**每个成员交付同一格式的交接单：** 功能名称、输入／输出对应的 Schema 定义、使用的资料、运行命令、成功样例、失败样例、日志位置、人工复核结论、未解决问题。未完成的模块可用契约样例联调，但页面与视频须标“接口演示样例”。

## 2. 成员 A：系统、数据提取与运行环境

**拥有目录：** `api/`、`app/ingest/`、`app/parse/`、`app/evidence/`、`app/orchestrator/`、`app/models/`、`app/logging/`。负责固定输入文件快照、OCR／表格解析、证据坐标、结构化提取、OpenAPI 服务、运行清单与一键复现。接口冻结后先把 `POST /documents`、`GET /evidence/{id}`、`POST /runs` 和状态查询做通。

可直接给 AI 助手的任务模板：

> 阅读本仓库的 AGENTS.md、docs/项目总纲.md、docs/接口约定.md、contracts/openapi.yaml、contracts/contracts.schema.json 和 contracts/examples/。只实现【写明模块，例如文档登记和证据索引】。公共字段必须与契约一致，不修改其他成员目录。给出运行命令、一个成功样例和一个解析失败样例；每次文件访问与工具调用写入 Event。不要生成没有原文坐标的数值字段。完成后运行 `python3 contracts/validate_contracts.py` 并汇报结果。

**验收：** 同一 PDF 重复登记得到稳定 ID 或明确版本关联；上传后可查询 SHA-256 与解析状态；证据页码、坐标能在原件上框选；结构化输出通过 Schema；扫描数字不清时返回复核状态；离线重放可得到同一输入哈希与工具调用轨迹。

## 3. 成员 B：财务口径、计算、产业链、估值与备忘录

**拥有目录：** `app/facts/`、`app/tools/`、`app/tasks/financial_analysis/`、`industry_impact/`、`valuation/`、`investment_memo/`。负责指标字典、期间与合并口径、同比和单季运算、异常信号、估值假设及投资文本的金融复核。

可直接给 AI 助手的任务模板：

> 阅读 AGENTS.md、docs/接口约定.md、contracts/contracts.schema.json 和财报分析／估值样例。实现【写明公式或任务】。所有金额用 Decimal 运算并以字符串输出，保留输入 fact_id 与 calculation_id。严格区分合并／母公司、累计／单季、时点／流量以及追溯调整。基期为零、负利润 PE、缺少行情时点、口径不一致时返回 needs_review 和原因。不要直接用大模型生成数值。提供有意义的边界测试和原文页码核对记录。

**验收：** 从 Fact 独立重算主要比率，错误期间被拒绝；异常卡片只声明“需核查”，解释附来源；估值区间有假设与敏感性；备忘录有风险、反证与跟踪指标；金融事实与原始披露逐项一致。

## 4. 成员 C：研报核查、评测、前端与视频

**拥有目录：** `app/tasks/research_verification/`、`report_quality/`、`eval/`、`web/`、`demo/`。负责研报声明拆分、错误类别与建议改写、双人标注与裁决、前端证据对照和参赛视频。

可直接给 AI 助手的任务模板：

> 阅读 AGENTS.md、docs/接口约定.md、contracts/contracts.schema.json、contracts/examples/ 和视频脚本。实现【写明纠错、评测或界面子任务】。逐条声明输出 decision、error_type、原句位置、原始来源证据、重算 ID 和建议改写。模糊或证据不足时输出 needs_review，不强行判错。评测分别统计精确率、召回率和误报，开发集与留出集按公司划分。Web 页面展示真实运行状态，契约样例必须标注为样例。

**验收：** 一份含正确句、故意错误句和无法核实句的草稿均有对应处理；误报与漏报能复盘；界面可从核查结果跳到证据页；录屏和提交版本一致；视频 MP4 不超过 5 分钟和 500MB。

## 5. 跨成员接口交接顺序

1. A 先提供 `Document`、`Evidence`、`Event` 服务，C 据此做前端证据定位；B 据此做财务事实归一化。
2. B 提供 `Fact`、`Calculation`、财务分析结果，C 据此做研报纠错与质量评估，A 集成到任务编排。
3. C 提供 `research_verification` 结果和错误标注；B 复核金融规则，A 接入 HTTP。
4. 三人共同检查七题 `RunRequest` 与 `TaskResult`，再冻结演示版本。每次交接都使用 `contracts/examples/` 做联调；样例只验证接口形状，功能正确性仍需真实留出样本。

## 6. 公共接口变更申请模板

```text
提出人：
当前版本：1.0.0
要变更的 Schema 路径：
当前字段与问题：
建议字段及示例：
影响的任务/前端/评测：
是否需要迁移旧数据：
A/B/C 审阅结论：
```

先在 `docs/接口问题记录.md` 记录，再同时更新 Schema、OpenAPI、样例和版本号。成员或 AI 助手不得为了让本模块快速运行而私下改公共字段。
