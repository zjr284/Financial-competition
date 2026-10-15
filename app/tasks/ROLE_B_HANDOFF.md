# 角色 B 模块交接说明

2026-10-15 后续交付：备忘录只向模型提供定性摘录，四项已确认的原始财务指标由本地按截止日／合并／累计／重述规则追加；最新值未复核或冲突不回退旧值。五类 A/B 任务共用运行及离线重放入口，全仓 357 项通过。操作、样例、版本限制与 C 后续说明见 [统一运行和本地财务附录](../../docs/AB统一运行与本地财务备忘录.md)。真实资料仍在私有目录，交接工具 PR #15 待合并，不通过公开 Git 传原底稿。

2026-10-15 更新：PR #11 已合并，百炼 qwen3.7-plus 已接通；本轮增加公司来源归属、披露时间和资料期间约束，使用同公司年报／半年报十五条片段跑通两条语义任务，并新增公司半年报与国家能源局独立行业短摘录的对照案例。行业资料不绑定公司，也不直接证明公司订单或利润；部分自述归因仍待分析员复核。默认离线规则与真实云端结果区分，全部解释候选保留待复核。用法及私有清单迁移见 [运行说明](../../docs/模型与语义任务运行说明.md)，A/B 后续分工见 [本轮验收](../../docs/公告候选与来源归属验收_20261015.md)。

本轮新增跨电脑私有交接：保留财务数值、状态、原件哈希及引用，去除个人复核历史，导出空白坐标及语义标注模板。C 接收、原始结果读取和纯工具复测步骤见 [交接说明](../../docs/C真实资料包交接说明.md)；该包不是完整模型重放包，不代签人工核验。

## 已实现的范围

| 模块 | 入口 | 输入 | 输出 |
|---|---|---|---|
| 事实字典与核验 | `app.facts.FactIndex` | `Fact` 列表 | 同公司、口径、期间的规范事实；冲突显式报错 |
| 确定性计算 | `app.tools.FinancialCalculator` | 已登记事实及公式参数 | 符合公共 Schema 的 `Calculation` |
| 财报分析 | `FinancialAnalysisTask.run(context, task_input)` | `FinancialInput`、证据和计算端口 | `FinancialData` 加公共证据、计算、复核项 |
| 相对 PE / DCF | `ValuationTask.run(context, task_input)` | `ValuationInput`、财务与行情快照 | `ValuationData`、敏感性矩阵、计算轨迹 |
| 产业链 | `IndustryImpactTask.run(context, task_input)` | `IndustryInput`、归档行业证据、模型端口 | `IndustryData` 候选与人工复核项 |
| 买方备忘录 | `InvestmentMemoTask.run(context, task_input)` | `MemoInput`、财务和行业证据、模型端口 | `MemoData` 候选与人工复核项 |

以上任务插件均返回 `{data, evidence_ids, calculations, review_items, warnings}`，由 A 的编排器按照 `contracts/contracts.schema.json#/$defs/TaskResult` 加上公共包裹层。若 `review_items` 非空，编排器应把任务状态设为 `needs_review`。任务插件没有实现 PDF/OCR、文件存储、Web、真实模型部署或 HTTP 路由，这些接口由 A/C 负责。

A 可调用 `app.tasks.role_b.get_role_b_tasks()` 一次性取得四个 `task_type → TaskPlugin` 映射并注册到编排器。

## 与 A/C 已合并模块的对接（更新于 2026-10-12）

`main` 已包含 A 的文档登记、PDF/TXT 解析、证据定位和编排器，也包含 C 的研报核查与质量评估。A 的 `_TaskContext` 暂无 `evidence`、`calculations` 两个端口。B 在 `app.tasks.a_bridge` 提供适配器，**只改 B 的目录**。在 A 的应用启动后、创建 Run 前调用：

```python
from app.container import build_app
from app.tasks.a_bridge import install_role_b

app = build_app()
install_role_b(app)  # 默认为 app.settings.data_dir / "reviewed_facts.json"
# 或 install_role_b(app, "/path/to/private/reviewed_facts.json")
```

这会注册 `financial_analysis`、`valuation`、`industry_impact`、`investment_memo`。A 的默认 `build_app()` 尚未自动调用它；团队的 `python -m demo.live --data-dir var/team-live` 已显式安装 B/C 任务。适配器每次运行新建计算器，并把 A 的 `DocumentService + ParseService` 包装为公共 Document/Evidence 端口。A 目前仅捕获 `AppError`；适配器把输入/来源/模型校验异常转换成结构化失败，避免 Run 停在 `running`。C 的两题由 `install_role_c` 独立注册，复用相同的已复核事实格式。

B 的适配器在首次运行时只选取本次文档的事实，固定到 `var/runs/<run_id>/b-reviewed-facts.json`，事件流记录快照哈希；随后修改总清单只影响新 Run，旧 Run 重放继续读取自己的快照。计算事件的输入／输出哈希同时写入持久日志和重放轨迹。修订已完成的 Run 应创建新 Run，不能在原 Run 上替换证据。运行和重放验证：

```bash
python3 -m unittest discover -s tests -p 'test_role_b_a_integration.py' -v
```

该测试使用合成 PDF 验证证据页码与 bbox、同比、快照隔离、重放结果及轨迹一致，以及过期文档哈希导致的结构化失败。C 的相同边界另由 `eval/test_c_runtime.py` 验证。

### 经复核事实清单

`reviewed_facts.json` 放在 A 的私有数据目录，格式如下；每个 Fact 须完整符合 `contracts/contracts.schema.json#/$defs/Fact`：

```json
{
  "version": 1,
  "documents": [
    {
      "document_id": "doc_实际登记ID",
      "sha256": "此文档实际SHA256十六进制值",
      "facts": [
        {
          "fact_id": "fact_唯一ID", "company_code": "999999", "metric": "revenue",
          "value": "120000000", "unit": "CNY", "period_start": "2025-01-01",
          "period_end": "2025-12-31", "period_type": "cumulative",
          "statement_scope": "consolidated", "restated": false,
          "evidence_ids": ["ev_实际证据ID"], "review_status": "verified", "reason": null
        }
      ]
    }
  ]
}
```

以上 ID/数值仅说明字段形状，**不能直接复制为真实样本**。B 先在 A 的证据页核对原文、页码、框选、单位、期间、合并/母公司及更正公告，再填写数值；未完成核验的事实标 `needs_review` 并填 `reason`。导入时会重查文件 SHA-256、Fact Schema、证据是否来自该文件，以及 verified 事实的证据文本和解析置信度；不匹配则失败。缺清单或缺事实时任务返回待复核，不会臆造财务值。财报分析会展示待复核事实，但不会拿它们计算同比或比率。

### 真实资料的四步复核流程

1. A 登记并解析正式披露 PDF，保存文件 SHA-256、披露日期和来源地址。选定一家公司及对照年度后，把所有需要的年报、半年报、更正公告先登记齐全；不同版本使用不同 `document_id`。
2. B 在原 PDF 上核对具体页码、框选、表头年度、单位、报表口径及脚注，再用 `inspect` 找到对应 `evidence_id`。`inspect` 只展示短片段和位置，不自动判定数值。
3. 用 `init` 创建空白私有清单，再逐条用 `add` 登记事实。金额输入人民币**元**的十进制字符串；只有人工核对完成才加 `--verified`，否则必须提供 `--reason`。同一来源、指标、期间和口径不覆盖旧 Fact。
4. 后续新增报告用 `attach` 加入清单，保留已有事实；B 本人核对原件后可用 `verify fact_id --reviewer 姓名或团队代号` 确认待复核事实，私有清单会记录复核人、时间和证据 ID。用 `check` 核验全部事实的文件哈希、证据归属、Schema 和来源位置。B 再独立复核同比、归母／扣非、单季／累计及更正公告；C 在此基础上进行研报声明双人标注和留出集评测。

```bash
FINSCOPE_DATA_DIR=var/team-live python3 -m app.cli ingest PATH_TO_ANNUAL_REPORT.pdf --parse
python3 -m app.facts.review_workflow --data-dir var/team-live inspect doc_实际ID --contains 营业收入
python3 -m app.facts.review_workflow --data-dir var/team-live init doc_当期ID doc_上年ID
python3 -m app.facts.review_workflow --data-dir var/team-live attach doc_新增报告ID
python3 -m app.facts.review_workflow --data-dir var/team-live add \
  --document-id doc_当期ID --evidence-id ev_实际ID --company-code 真实六位代码 \
  --metric revenue --value 120000000 --period-start 2025-01-01 \
  --period-end 2025-12-31 --period-type cumulative \
  --statement-scope consolidated --verified
python3 -m app.facts.review_workflow --data-dir var/team-live check
python3 -m app.facts.review_workflow --data-dir var/team-live verify fact_待复核ID --reviewer B的真实姓名或团队代号
```

示例命令中的代码、日期和金额仅为输入形状，需以真实文件核对结果替换。`init` 采用独占创建，避免覆盖已有人工清单；`add` 先验证候选清单再原子替换，重复来源会拒绝。`check` 会输出每条事实的 `fact_id → evidence_id → page/bbox` 定位记录。清单默认位于 `var/team-live/reviewed_facts.json`，该运行目录被 Git 忽略；不要把真实全文或私人清单提交到公开仓库。

欣旺达真实案例的官方披露文件 SHA-256、18 条成员 B 已确认的财务事实与新 Run 见 [案例记录](FINANCE_REAL_CASE_300207.md)。旧诊断 Run 保留用于复核门禁对照，不作为正式结果。

相对估值的早期候选筛选见 [可比公司底稿](valuation/REAL_CASE_PEER_SCREEN.md)。后续已接通 TTM Calculation 与同日行情复核，用户已确认豪鹏原始利润及三家同业市场快照，旧候选记录不代表当前正式输入。当前真实案例保留四十五项确认事实及待复核的研究解释；本轮语义扩充不重签或修改这些事实。

财务入口仍是**人工复核事实导入**，不是全年报表格的自动提取。A 本轮另增加公告模型候选，不能直接当作财务 Fact。C 的研报核查只接受 verified 且来源解析置信度至少 0.9 的事实；TXT 的近似定位通常达不到此阈值。默认离线规则不生成 IndustryData/MemoData；显式启用已接通的云端后可运行有来源的语义候选，真实抽取准确率和留出集仍须独立验收。

已合并代码可用合成 PDF 验证 `上传 → 解析 → 证据 → 复核事实 → financial_analysis → 同比 20% → needs_review → 离线重放哈希与轨迹一致`；这只证明接口贯通，不是年报准确率。C 的 `research_verification` 对缺少足够可信来源的声明返回 `needs_review`。

## A 侧接入方式

```python
from contracts.ports import TaskContext
from app.tools import FinancialCalculator
from app.tasks.financial_analysis import FinancialAnalysisTask

context = TaskContext(
    run_id="run_...",
    document_ids=("doc_...",),
    documents=document_port,
    evidence=evidence_port,
    calculations=FinancialCalculator(),
    model=model_port,
    events=event_port,
)
payload = FinancialAnalysisTask().run(context, {
    "company_code": "999999",
    "periods": ["2025-12-31", "2024-12-31"],
    "statement_scope": "consolidated",
})
```

`evidence_port.extract_facts(document_ids, field_codes)` 必须返回公共 `Fact` 对象；它们的 `evidence_ids` 必须能被 `get_evidence` 查到，且 `Evidence.document_id` 属于此次运行的 `document_ids`。计算器在任务运行时自动登记提取到的事实。`events.append_event` 应持久化计算事件；日志只记录 ID、摘要与哈希。`model.infer_json` 用于产业链与备忘录，须按照 `IndustryData` / `MemoData` 约束返回 JSON，并记录模型与 Prompt 版本。

请勿将 `contracts/examples/` 的虚构公司数据作为实测成果；目前测试只证明接口与关键算术路径，不证明真实公告解析准确率。

## 财务计算与适用条件

| `formula_code` | 输入顺序 | 输出 | 主要拒绝条件 |
|---|---|---|---|
| `yoy` | 当期、上年同期同指标事实 | `percent` | 期间／口径不匹配、基期为零 |
| `quarter_from_ytd` | 本期累计、上一季度累计 | `CNY` | 非流量、非相邻季度、不同年或不同口径 |
| `gross_margin` | 营业收入、营业成本 | `percent` | 不同期间／口径、收入为零 |
| `cash_conversion` | 经营现金流、归母净利润 | `times` | 不同期间／口径、利润为零 |
| `nonrecurring_gap_share` | 归母、扣非归母净利润 | `percent` | 不同期间／口径、归母利润为零 |
| `pe` | 同日总市值、同公司近期 TTM 归母利润 | `times` | 日期不符、利润或市值非正 |
| `relative_equity_value` | 目标公司 TTM 归母利润 | `CNY` | 缺少或无效的可比 PE 倍数 |
| `forecast_fcf` 等 DCF 公式 | 详见代码中的参数名 | `CNY` | 缺少假设、假设范围错误、WACC 不高于终值增长率 |

输出金额均为**人民币元**；相对估值和 DCF 输出的是**股权总价值**，不是每股价格。百分数以 `"12.5"` 表示 12.5%。`nonrecurring_gap_share` 只是归母与扣非归母差额的筛查指标，不能直接代替公司披露的非经常性损益明细。

相对 PE 使用资料包内三家及以上可比公司的同日市值与近期 TTM 利润，取最小／中位／最大 PE，附 ±10% 目标盈利敏感性；可比性必须由分析员复核。DCF 需要显式输入 `revenue_growth, operating_margin, tax_rate, da_pct, capex_pct, nwc_pct, wacc, terminal_growth, net_debt`，可选 `forecast_years`，默认 5 年。所有利率和增长率都采用百分数值，例如 `"10"` 表示 10%。`nwc_pct` 指**每年新增营运资本投入占当年收入的百分比**；此简化假设必须在最终报告中解释。DCF 使用 WACC ±1 个百分点、终值增长率 ±0.5 个百分点生成九格敏感性。

## 成功与待复核样例

运行：

```bash
python3 -m unittest discover -s tests -p 'test_finance_modules.py' -v
python3 contracts/validate_contracts.py
```

`test_financial_analysis_recomputes_yoy_and_flags_profit_cash_divergence` 是成功样例：收入 1.2 亿与上年 1 亿重算同比 20%，同时对“利润为正、经营现金流为负”生成有证据的提示。`test_relative_pe_uses_three_peers_and_sensitivity` 与 `test_dcf_explicit_assumptions_generate_nine_scenarios` 展示两种估值及敏感性。`test_zero_base_and_scope_mismatch_require_review`、`test_dcf_missing_assumptions_returns_no_invented_value` 展示关键失败／待复核路径。

`test_industry_and_memo_return_grounded_reviewable_data` 展示模型产出的候选结构，以及系统强制保留人工复核项；`test_synthesis_without_evidence_stops_before_model_call` 展示缺少归档来源时不调用模型、不编造分析。

测试证据定位输出样例：`ev_rev25 → doc_annual_demo，第 42 页，bbox=[100,200,700,240]`。这是**模拟定位**，只用于验证 A/C 的跳转接口；真实页码与框选必须由 A 的解析模块从正式披露文件生成，再由 B 抽查。

## 待接入与金融复核

1. A 已实现文档哈希、文本层 PDF 证据坐标和运行状态；扫描件 OCR、真实模型服务及真实 PDF 跨页表格仍需验证。C 已实现证据对照页面。
2. B 与 C 共同选定真实上市公司样本，由 B 逐项核对归母／扣非、合并／母公司、累计／单季及更正公告。当前代码尚未用真实年报做准确率评测。
3. 产业链和备忘录模块会调用模型，但对因果与投资判断始终保留人工复核项。提交前必须检查模型输出是否与原文一致、资料是否仍在适用期。
4. 如确需修改公共字段，在 `docs/接口问题记录.md` 提议，由三人确认后同步修改 Schema、OpenAPI 和示例。

## 合并前审查修复

备忘录仅使用期间和来源披露日不晚于 as_of_date 的资料，缺披露日的资料暂不送入模型。产业链/备忘录禁止模型直接输出 kind=calculation，且引用只能来自本次实际提供给模型的证据集合；计算声明须另接确定性工具后再开放。相对 PE 按公司代码排序，保证跨进程结果顺序稳定；DCF 基期描述按实际母公司/合并口径展示。

## 2026-10-12 最新底座回归与估值时间口径

已同步 main 的 0854d75 提交，并修复 PE/DCF 使用估值日后披露来源的风险。估值输入逐条检查全部证据的披露日期，PE 强制目标与同业滚动利润期间一致，完整十二个月按日历与闰年判定。缺失或晚于估值日的来源输出警告和复核项，不进入计算。

真实年报／半年报已在新底座创建新 Run，验证指标、三格式导出及离线重放。成功与待复核样例、运行命令和限制见 [本轮验收记录](valuation/VALIDATION_20261012.md)。运行和来源定位元数据仅留在私有数据目录。正式 PE 仍受滚动利润公共计算链、同日行情／股本与可比性复核门槛约束。

## 2026-10-12 TTM 计算链接入

新增 [TTM 接入交接](valuation/TTM_INTEGRATION_HANDOFF.md) 与完全虚构的目标加三家同业资料包。B 估值通过 input_calculation_ids 消费 TTM，保留完整上游记录；生产 A 工具缺公式时返回待复核，不将派生值登记为原始事实。公共派生 Fact 方案仅有 B 的有条件确认，尚待 A/C；真实候选未自动转正。

## 2026-10-13 A/B 生产接入

生产工具已实现 ttm_from_ytd 和基于 Calculation 的 PE/目标估值，虚构样例已移除成功演示计算器。新增工具校验、生产 PDF 资料包、JSON 导出与固定复核快照重放测试；交接文件已改为当前状态及 C 后续清单。真实候选未转正，公共 Fact 字段未改。

## 2026-10-14 服务端报告与实际候选定位

完成服务端估值 CSV/HTML 原始事实与计算来源展开，校验本次 Run 的固定快照哈希，不读取变化后的总清单。B 年末累计 TTM 与区间场景引用已补齐；真实豪鹏候选一项定位改为主表归母行，仍待人工复核。详见 [本轮验收](valuation/VALIDATION_20261014.md)；C 最新独立分支已只读通过兼容测试，未改其模块。

## 后续市场来源与盈利可持续性

三家同业原始日终市值已固定并完成日期/代码/股本/非复权收盘价交叉检查，登记为待复核候选，原始来源可直接进入复核队列。新增归母与扣非 TTM 的工具化差额检查；实际目标差异需审查非经常损益，不把技术 PE 情景直接作为合理市值。交接见 valuation/MARKET_INPUT_HANDOFF_20261014.md。

2026-10-14 用户已明确确认采用上述三项历史市值快照，已登记采用记录并创建新的真实 PE Run。私有清单现在有 45 项已确认事实；历史复核记录与旧运行保持原样。估值情景区间非空，21 项计算可追溯，三格式导出与新旧运行重放通过；任务仍保留可比性、非经常损益和历史来源版本复核。公开资料包不包含真实完整底稿及个人采用记录。

2026-10-15：PR #9 已合并，新增有符号差额金额工具和三家同业盈利质量检查。欣旺达三期非经常性损益明细、税后归母合计及 TTM 差额已机器复核一致，新明细不代签人工确认。业务研究保留德赛／豪鹏消费业务参考、亿纬综合行业参考的区别，详见 [本轮复核与 C 交接](valuation/EARNINGS_QUALITY_REVIEW_20261015.md)。
