# A/B 服务端估值导出与来源校验（2026-10-14）

## 当前仓库与交付范围

检查时远端 main 为 cfa8d20，生产接入 PR #7 尚未合并。C 已提交独立分支 feat/member-c-ttm-display-20261013。本轮在 PR #7 上继续完成 A 服务端导出、B 年末滚动利润与原始来源引用，不修改或合并 C 模块，不修改冻结公共契约。

## 本轮变更

- 估值 CSV/HTML 展示区间、Calculation、原始 Fact、期间/状态/原因及证据页码、bbox、文档 SHA-256。仅整理已有值，不调用模型、不重算财务结果。
- 区间通过 B 写入的 valuation_scenario=low/base/high 参数明确指向工具记录，不按数值相等猜测因果关系。旧结果缺少标记时保留既有值和证据并提示需复核；不重写旧 Run。DCF 的完整计算记录也保留，范围汇总沿用已有结果证据。
- 服务端读取本次 Run 的 b-reviewed-facts.json，核对其哈希与运行事件记录，再验证文档版本和证据关联。使用同一次读取的内存副本，避免验证后再次读到不同文件。不会查询当前总事实清单来补旧结果。
- 总清单修改后，旧 Run 的 CSV/HTML 与来源保持一致；运行快照被篡改或遗失时拒绝来源明细导出。JSON 仍导出原 TaskResult，不把原始 Fact 或新字段偷偷加进公共响应。
- 无原始快照时保留 Fact ID、空金额和需复核原因；缺上游记录、重复 ID、循环、来源不完整均在导出中标识。HTML 转义内容，CSV 防止非数值公式注入，保留负数金额。日志不记录导出绝对路径、财务值或来源原文。
- B 自动选择增加自然年度年末累计；基期与上年全年相同的退化情况仍通过工具计算，保留三项输入引用，未伪造 TTM 原始 Fact。

## 验收

全仓 tests/ 与 eval/ 本地回归 187 项通过；另外只读加载 C 最新分支的 TTM 模块与评测，5 项兼容测试通过，含实际 HTTP 生产计算与重放。未宣称浏览器截图或远端 CI 由本轮重新验收。

契约校验仍为 7 requests、7 results、7 entities、10 API paths。新增公共 Fact 字段及 Q6 新端点仍待三方评审。

关键端到端验收：虚构文本层 PDF 通过实际解析，15 项原始事实和 19 项计算在导出中形成 37 行（含三个区间端点），原始行保留真实解析的物理页码、区域及来源哈希。JSON 内容与 TaskResult 一致；旧 Run 在总清单变化后导出哈希不变；快照篡改被拒绝，原始 JSON 仍可读取；事件不含本机数据目录。

## 样例与运行

```bash
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py
PYTHONPATH=. python3 app/tasks/valuation/samples/ttm_pe/demo.py --needs-review
python3 -m unittest discover -s tests -p test_role_b_ttm_valuation.py -q
python3 -m pytest tests/test_valuation_exports.py tests/test_role_ab_ttm_pipeline.py -q
python3 contracts/validate_contracts.py
```

成功与待复核 JSON 仍位于 samples/ttm_pe；全部公司、数值及定位为虚构接口样例。可通过内部 run_export 的 valuation_sources 参数生成静态演示报告；实际服务由编排器传入经校验的固定快照，不能借此在 HTTP 请求中注入 Fact。

逻辑定位输出：ev_demo_999999_annual → doc_demo_annual，第 1 逻辑页、bbox=[0,0,1000,1000]，哈希在 inputs.json。端到端测试使用另外生成的文本层 PDF 检查实际物理区域。TXT 演示区域不能冒充财报 PDF 框选。

## B 的真实资料进展与限制

私有豪鹏上年同期归母候选曾引用股份支付影响调整区的同值数字。本轮对照半年报物理第 8 页，将新候选定位至主要会计数据的归母行，保留旧候选为拒绝使用版本；新候选仍 needs_review。原 36 项已确认事实及真人复核记录未改，当前 6 项待复核，另保留 1 项旧定位候选。

真实资料、私人 ID、个人复核记录和运行信息未上传。来源定位更正不是人工财务确认；真实 PE 仍需利润、估值日股本/价格及可比性独立复核。C 的实际 API 页面原始事实展开仍受 Q6 事实读取接口未冻结的限制；本轮完成的是服务端报告来源展开，没有声称该新 HTTP 接口已存在。
