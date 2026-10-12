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
