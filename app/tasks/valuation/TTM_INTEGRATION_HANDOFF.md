# A/B：生产 TTM 估值接入交接（2026-10-13）

## 已完成

A 的生产 FinancialCalculator 实现 ttm_from_ytd；B 的估值通过 input_calculation_ids 消费结果，保留上游 TTM、下游 PE/估值及三项原始利润引用。合成演示已经使用生产工具，没有单独的成功演示计算器。内部期间选择记录不登记为原始 Fact，也不返回公共 Fact。

公式：上一年度全年 + 本年度累计 − 上一年度同期累计。半年报样例对应 2025-07-01~2026-06-30。生产工具支持完整月份的对应累计区间，包含一季报、半年报、三季报、年末及闰年月末；工具支持符合条件的非自然年度，B 当前自动选取仅支持自然年度标准季度累计端点。

归母与扣非可分别计算，工具参数 metric 分别为 net_profit_parent_ttm 和 net_profit_parent_excl_nonrecurring_ttm。当前 PE 只使用归母；不能将扣非或其他计算替代归母利润。

## 冻结的内部计算调用

保持 CalculationPort.calculate 原有四个参数和公共 Calculation 返回结构：

- ttm_from_ytd：三个 input_fact_ids 按 annual/current_ytd/prior_ytd 顺序；input_calculation_ids 为空。工具由原始事实生成 company_code、metric、statement_scope、restated、业务 period_start/end、三组输入 period_start/end 及 calendar_rule。调用方已有同名参数必须一致。
- pe：市值 Fact + TTM Calculation；parameters.as_of_date 必填。旧的市值 Fact + TTM Fact 路径保留。
- relative_equity_value：目标 TTM Calculation；参数 multiple，可选 earnings_shock_percent，以及 B 传入的公司、报表口径、期间和估值日。旧的目标 TTM Fact 路径保留。

所有输入仍需 verified，缺值/业务条件失败返回 value=null、status=needs_review 和 reason。未知 ID 属调用方引用错误，仍抛明确异常，由编排适配器转换为错误状态；B 在业务缺资料时预先生成待复核项。

工具独立核查公司、指标、CNY、报表口径、累计关系和 restated，核验利润计算元数据、原始输入与算术结果。PE 要求正市值和正利润，同公司、正确估值日和利润时点。原始值可以为负；计算合法不等于具有可用 PE。

## 财务与来源约束

三期累计区间存在重叠，不是首尾连续。年报覆盖十二个完整月份，本期累计从年报结束次日起，基期从年报起点起，本期与基期对应一年前的完整月份；业务终点为本期期末，不是上一年年报期末。

restated 不一致仍进入独立调节复核；同为 false/true 不能证明政策和合并范围一致。当前不提供自动豁免开关。分红、送股、转增不改利润拼接；市值需估值日有效股本和正确价格口径，增发与注销单独核查。半年末股本不能直接当作估值日股本。

B 检查所有来源披露不晚于估值日、证据属于固定资料包、目标和同业 TTM 期间一致，并要求至少三家有效同业。真实豪鹏候选及行情/股本候选复核状态未改；未执行真实 PE 转正。

## 版本、缓存与重放

计算 ID 现在包含公式实现版本、输入事实完整快照、输入计算记录、全部上游事实及参数，避免复核状态/来源/期间变化后错误复用旧成功结果。相同输入与版本仍可复现同一 ID。注册事实和返回的计算结果使用副本，调用方修改对象不会修改工具内部结果。

这是内部计算标识算法更新：旧结果与旧计算 ID 保留，不重写历史运行。新代码重放旧版本运行可能产生不同 ID/结果哈希；应使用原代码版本复现历史，或创建新运行验收，不能宣称跨代码版本哈希必然一致。新增生产端到端测试确认本版本固定资料与复核快照的结果/事件重放一致。

## 样例与运行

samples/ttm_pe 全部公司、金额和定位均虚构，仅作接口演示。包含目标加三家同业、十二项原始利润、三项市值、四份固定 SHA-256 TXT 资料和人工模拟 Evidence。

```bash
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py --needs-review
python3 -m unittest discover -s tests -p test_ttm_calculation_tool.py -q
python3 -m unittest discover -s tests -p test_role_b_ttm_valuation.py -q
python3 contracts/validate_contracts.py
```

success.json 使用生产工具生成 1200 / 1500 / 2400 CNY 区间；仍保留可比性人工复核项。needs_review.json 将目标的一项原始利润标为待复核，区间为空。--production-calculator 保留为兼容参数，默认已是生产工具。UnsupportedFormulaCalculator 仅用于部署缺公式的失败测试，不参与成功演示。

TXT 示例定位：ev_demo_999999_annual → doc_demo_annual，第 1 逻辑页、bbox=[0,0,1000,1000]；原文在 annual.txt，完整映射在 inputs.json。不能把该逻辑区域当作真实 PDF 框选。A/B 端到端测试另外生成虚构文本层 PDF，使用真实解析器证据与实际事实清单，确认物理页码、区域、置信度、生产估值、JSON 完整导出和离线重放；没有降低 TXT 证据质量保护门槛。

## A 后续边界

本轮工具与编排接入已完成。可选公共派生 Fact 仍待三方评审：旧数据缺 derivation 按 disclosed，calculated 必须有 calculation_id，值/单位/期间和计算状态须对应，保留原始 evidence_ids，禁止手填派生 verified 绕过输入复核。若采用，A 同步 Schema/OpenAPI/接口说明/样例/存储及版本迁移，B 验收与内部选择的去重和一致性。

该公共字段不是当前生产 PE 的前置条件。本提交不改公共契约；Q6 与 Q9 分别评审，新增端点先列清单，再按实际唯一路径数校验，不能直接把 10 改为 15。

## C 下一步执行清单

1. 展示与证据跳转：使用新的 success.json 和 needs_review.json；显著标注“全部虚构的接口演示样例”。TTM 标注“计算得出”、业务期间和公式，从 Calculation 的 input_fact_ids 追溯三项原始值及证据、文档 SHA-256。ValuationData 不包含原始 facts，样例从 inputs.json 读取；真实事实读取接口仍走 Q6 评审，不能假设字段已存在。
2. 状态与导出：区分工具 verified 与任务可比性 needs_review。空区间显示原因，不能填零。JSON 导出已完整保留 TaskResult；现有 CSV/HTML 为通用呈现，C 继续验收并完善估值表格、派生标记和链路展示，不因文件已生成就声称页面已完成。
3. 端到端评测：生产成功至少三家同业，所有计算引用闭合；缺资料、未复核、跨公司/口径、归母扣非混用、重述不一致、非正利润、时点不符均进入待复核。闰年覆盖 2023-03-01~2024-02-29、2024-03-01~2025-02-28 和差一天拒绝；工具通用期间与 B 自动选择范围分别验收。
4. 真实定位：在内部资料包查看此前蔚蓝锂芯半年报归母行框选是否过窄，核实物理页码、行列名、单位、区域；实际查看后再记结论，发现问题交 B 修正候选证据。
5. 公开内容复核：真实研究材料仅保留公开数据与来源，原始材料、私人运行标识、个人复核记录和本机路径留内部。虚构样例合法 ID 应保留。记录检查文件范围与问题；定位截图留在获准分享的内部材料包。

C 交付展示/导出截图、来源跳转结果、失败状态评测和隐私复核范围。真实原始利润、行情和股本完成独立复核后，由 B 创建新真实运行；生产合成验收不能替代真实准确率与可比性复核。
