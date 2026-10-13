# B：TTM 估值接入与财务口径（2026-10-12）

## 当前交付边界

B 确认 `ttm_from_ytd`，输入顺序为上一年度全年、本年度累计、上一年度同期累计；输出指标为 `net_profit_parent_ttm`，金额单位人民币元十进制字符串。本次使用 2025 全年 + 2026 上半年 − 2025 上半年，对应 2025-07-01 至 2026-06-30。

估值模块现从三项原始利润调用 CalculationPort 推导 TTM，将其 calculation_id 传给 PE 和目标股权估值；TaskResult.calculations 包含上游 TTM 和下游计算，失败时也保留已产生的计算记录。内部选择记录只用于期间与同业匹配，不登记为原始 Fact、不返回公共 Fact、不手写复核记录。既有直接 TTM Fact 输入路径保持兼容。

本提交不修改公共 Schema、A 的计算工具或 C 的展示。当前生产工具尚不支持公式和上述混合计算输入，实际运行返回 needs_review、空区间；合成适配器演示成功不代表生产联调完成。

## A 的计算接口接入要求

保持现有 CalculationPort.calculate 四个参数：

- `ttm_from_ytd`：input_fact_ids 按 annual/current_ytd/prior_ytd 顺序传三项；input_calculation_ids 为空。parameters 包含 company_code、metric、statement_scope、period_start/end，以及三组输入 period_start/end，均为字符串。
- `pe`：派生利润路径 input_fact_ids 仅含市值 Fact，input_calculation_ids 含一个 TTM Calculation；参数 as_of_date。原有两个 Fact 输入路径保留。
- `relative_equity_value`：派生利润路径 input_fact_ids 为空，input_calculation_ids 含目标 TTM Calculation；参数 multiple，可选 earnings_shock_percent。原有一个 Fact 输入路径保留。

A 必须自行校验计算输入的公司、指标、单位、期间、状态和口径；不能因 B 已筛选而省略工具校验。所有派生输入仍必须 verified，禁止用开关放行 needs_review。缺失资料由 B 在调用前产生待复核项；已生成的失败 Calculation 为 value=null、status=needs_review。

## 财务口径

首版 B 选择器支持自然年度公司一季报、半年报和三季报累计利润。三项累计区间不是首尾相接，必须按对应年度起点与同期终点校验。目标与同业 TTM 期间一致、资料披露不晚于估值日、证据属于固定资料包。市值使用估值日快照；至少三家有效同业，非正利润不能计算有效 PE。

restated 不一致时进入独立调节复核；同为 false/true 不能证明合并范围、政策及可比基础一致。首版不自动豁免此阻断，完成口径复核后由 A/B/C 确定表达方式。分红、送股、转增不改利润拼接公式；市值需核查估值日有效股本和价格口径，增发与注销另审。半年末股本不得直接视为估值日股本。

真实豪鹏候选及既有资料的复核状态未改动。用户同意实现方案不等于确认财报原文，生产真实 PE 仍需目标及同业输入、行情和股本复核。

## 公共派生 Fact 方案：B 有条件同意，尚待 A/C

采纳可选 derivation（disclosed/calculated）和 calculation_id。旧 Fact 缺 derivation 按 disclosed；calculated 必须有 calculation_id，disclosed 不应有非空 calculation_id；值、单位、期间与计算记录一致。原始 evidence_ids 和完整计算链均须保留。派生状态不得通过人工手填绕过上游状态，原始事实修正后重新计算。

新增字段需提升次版本并同步 Schema、接口说明、OpenAPI、样例、校验器、存储与消费端；旧严格校验器不一定能接受新字段。B 同意 A 为此修改 app/facts 的必要部分，PE 消费仍由 B 维护。新增字段的公共组装实现需三人确认后启用。

Q9 与 Q6 可同批评审、分别表决。目前登记的是四条新增路径（10→14），第五条尚未明确，B 不批准直接改成 15。

## 可复现合成演示与验收

所有公司、数字、来源定位及 verified 标记均为虚构接口样例，未进入真实事实库。samples/ttm_pe 包含一家目标、三家同业、12 项原始利润、3 项市值、四份固定 SHA-256 的 TXT 来源与 Evidence。TXT 全页 bbox 是逻辑定位，不是财报原件框选。

在项目根目录运行：

```bash
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py --production-calculator
python3 -m unittest discover -s tests -p test_role_b_ttm_valuation.py -q
python3 contracts/validate_contracts.py
```

success.json：演示工具产生区间 1200 / 1500 / 2400 CNY，保留四项 TTM 计算及其三项原始输入；结果仍 needs_review，需人工判断可比性。needs_review.json：当前生产工具不支持 ttm_from_ytd，区间为空，保留失败记录与原因。DemoCalculator 只提供可执行的接口行为示例，没有挂入生产应用，也不能承担真实财务校验。

来源定位例：ev_demo_999999_annual → doc_demo_annual，第 1 逻辑页，bbox=[0,0,1000,1000]，文本含虚构公司、期间和金额；完整信息在 inputs.json 与 annual.txt。

C 后续展示派生值时标注“计算得出”，可点回 Calculation 的三项事实与证据，并显示业务期间。闰年月末与近似十二个月拒绝已有估值期间识别回归；三输入公式的完整闰年校验由 A 工具与 C 用例共同验收。

## 本提交验证结果

新增 9 项 B 联调用例通过；既有财务 24 项、A/B 集成 1 项通过。全仓 tests/ 与 eval/ 回归共 163 项通过（本机测试环境沿用已有本地字体与 localhost 代理配置）。契约校验仍为 7 requests、7 results、7 entities、10 API paths，公共契约未变。

公开提交仅含 B 代码、文档、测试与完全虚构资料。没有上传真实原始 PDF、真实数据库标识、本机路径或个人复核记录。

## 成员 A 下一步执行清单

### A1：先补计算工具，保持现有公共返回结构

修改 app/tools/financial_calculations.py，实现前述三种调用组合；支持旧 Fact 输入，新增 Calculation 输入不能放宽 verified 要求。ttm_from_ytd 输出 CNY，使用 Decimal 和金额舍入规则。三期顺序与参数名称直接按本文件约定，成功和失败均返回完整 Calculation。

工具独立核查：三项同公司、net_profit_parent、CNY、同 statement_scope、累计区间关系、复核状态、重述基础；PE 校验市值与利润公司一致、估值日一致、利润期间不晚于估值日、正市值和正利润。读取 TTM Calculation 的 parameters 获取公司、指标、口径和业务期间，禁止用未经校验的任意 Calculation 冒充 TTM。计算标识和缓存须绑定输入版本、公式及参数；同一输入复现同一 ID，来源修正或有效状态变化不能命中旧的成功结果。

A1 验收：直接用生产 FinancialCalculator 运行第二条演示命令，也产生与 success.json 相同的区间及四项 TTM 计算；非空区间不是要求取消可比性复核。B 的 test_current_production_calculator_fails_closed 是当前依赖缺失的快照测试：A 接入后应由 B 改为生产成功验收，同时保留独立“不支持公式”的适配器失败用例，不能为通过旧快照而继续禁用公式。

### A2：补齐工具级失败用例与可重放记录

覆盖未复核输入、缺一项、未知输入 ID、错公司、归母/扣非混用、错单位、合并/母公司混用、累计起点不对应、同期终点不对应、重述不一致、非正利润、估值日不符。未知 ID 当前会抛异常，应先区分调用方引用错误和业务缺资料，再按既有错误/待复核约定处理，不要一律吞异常。

确认所有 TTM/PE/估值调用在事件记录中保留摘要与结果哈希，TaskResult 中保留完整计算链，真实来源文本不进入公开日志。用 B 虚构样例验证重放、导出和输入版本固定。

### A3：提交公共派生 Fact 契约提案，待三人评审后启用

同步 contracts/contracts.schema.json、contracts/openapi.yaml、contracts/examples/、contracts/ports.py（仅当签名确需修改）、docs/接口约定.md 与版本声明。样例至少包括旧披露 Fact、新披露 Fact、合法派生 Fact、缺 calculation_id 的非法派生 Fact、来源计算待复核的派生 Fact。校验器同时检查新旧数据兼容及组合约束。app/facts 的组装、存储、复核路径确保派生值不能经手动修改状态绕过上游；修正来源建立新版本和新计算链。

本轮 B 的内部计算记录接入不依赖新增公共字段；公共派生 Fact 接通后，再由 B 核实其与当前内部选择记录的去重、来源和状态一致性。不要并行创建两份独立的“已复核 TTM”。

Q6 与 Q9 分别记录 A/B/C 意见。若确需第五条新增端点，先列明方法、路径、请求、响应、错误和权限；按最终唯一的路径数更新断言，不预设 15。三人未确认前，不合入公共契约改动。

## 成员 C 下一步执行清单

### C1：现在即可用合成资料设计展示与定位

读取 samples/ttm_pe/inputs.json、success.json、needs_review.json，页面显著标注“接口演示样例，全部数据虚构”。展示 TTM 的“计算得出”标记、2025-07-01~2026-06-30 期间、公式和 calculation_id；从计算记录的 input_fact_ids 展开三项原始值，再通过 evidence_ids 显示原文、文档 SHA-256 与位置。当前 ValuationData 不返回原始事实对象，C 不应假设 data.facts 已存在：样例页面可从 inputs.json 读取，真实页面的事实读取接口由 Q6 评审确认。

结果页区分“工具已核算”与“可比性仍待人工审查”；needs_review 结果仍可显示有依据的区间，但本例因公式缺失而为空时显示原因与复核项，不填零。TXT 的 bbox 是逻辑定位，不能作为真实 PDF 框选截图。前端、HTML/CSV 导出都应保留样例和派生标记；避免某个导出把 TTM 写成财报直接披露。

### C2：补端到端评测，与 A 工具级测试区分

成功：生产工具接通后，一家目标加三家同业获得非空 low/base/high，页面和导出可从估值计算回到四项 TTM，再回到各三项原始事实，且状态与工具记录一致。

失败：缺原始利润、未复核、跨公司/口径、归母与扣非混用、重述未调节、非正利润、少于三家同业、晚于估值日披露、估值日与行情不匹配。验收输出应为空或排除不合格同业后待复核，不能通过手填 verified 得到区间。期间识别覆盖 2023-03-01~2024-02-29 与 2024-03-01~2025-02-28，并覆盖差一天的近似十二个月；若首版公式仅支持自然年度标准季度端点，闰年期间识别测试与公式输入测试分别命名，不强迫非支持会计年度走公式。

### C3：真实证据位置与公开资料复核

在内部资料包检查此前蔚蓝锂芯半年报归母利润行是否框选过窄；逐项记录物理页码、行名、列名、单位和区域是否清楚，发现问题反馈 B 修正候选证据。没有实际查看页面时不写“已确认”。真实定位截图留在获准分享的内部资料包，不把真实数据库 ID、私人运行记录或本机路径贴入公开评审说明。

复查 PR #6 的公开文件及新增样例：公开研究数字须能对照公开来源，内部原始文件、个人复核记录和本机路径不应上传；虚构样例的 fact_demo/ev_demo/doc_demo/calc 标识应保留，它们是合法演示引用。复核结果记录文件范围、结论和需修改项，不要只写“全部安全”。

## 接入次序与完成条件

1. A 做 A1/A2；C 同时做 C1 的样例展示、C2 用例及 C3 复核。
2. A 提交 A3/Q6/Q9 的具体契约提案，A/B/C 分别评审；尚有歧义时不强行合并。
3. A 工具提交后，B 更新生产验收、运行完整回归；C 以生产输出完成页面、证据跳转、导出和失败状态验收。
4. 真实原始利润、股本与行情完成独立复核后，由 B 创建新真实运行。合成演示验收不等于真实 PE 已完成。

交付时 A 提供公式与接口版本、成功/失败输出和重放结果；C 提供展示与导出截图、证据跳转结果、评测结果及隐私复核范围。B 再据此核对财务口径、可比性和计算链，三方评审通过后合入。
