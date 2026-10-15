# TTM 展示与评测：角色 C（2026-10-13）

本轮只修改 C 的 demo/eval/web。公共契约仍为 v1.0.0、10 条路径；不修改 A/B 实现，不把派生利润写为原始 Fact。C 的主分支基线为已合并 PR #6 的 cfa8d20；PR #7 的生产工具提交 30cb0d4 另在独立工作树组合验收，等待团队评审。

## C1：展示、证据和导出已交付

```powershell
python -m demo.prepare
python -m demo.serve --port 8765
npm --prefix web run test:ttm
```

打开页面，选择“滚动利润估值（虚构接口样例）”或“滚动利润估值（原始利润待复核）”。四家公司的十二项利润、三项市值与 TXT 全部虚构。成功样例调用 B 的当前示例入口：在 main 使用既有演示计算器；与 PR #7 组合后直接使用生产 FinancialCalculator。各结果 warnings 明确标记计算器与资料边界，不能把 main 的演示当作生产成功。

每项 TTM 标注“工具计算的滚动12个月利润”“计算得出”、业务期间、公式和 calculation_id，逐项展开上一年度全年、本年度累计、上一年度同期累计的原始 Fact ID、金额、公司、指标、口径和期间。来源按钮显示证据原文、物理/逻辑页码、bbox 与文档 SHA-256，读取固定原件前验证哈希。TXT 全页区域仅为逻辑定位，明确不能作为真实 PDF/OCR 框选。

下游 PE/估值计算可以跳回上游 TTM。工具 verified 与任务 needs_review 分别展示；少量资料未确认时区间显示“暂无结果”和复核原因，不填零。缺上游记录、循环和重复计算 ID 提示计算链需复核。页面不自行重算利润，也不提高任何输入的复核状态。

JSON 导出保持 TaskResult；HTML/CSV 包含样例标记、派生/原始披露分类、估值区间、计算记录、原始引用、业务期间、来源页码/坐标/哈希和链路状态。HTML 转义原文，CSV 防止非数值公式注入。完整运行 JSON 也保留在 HTML 中。截图与导出位于 Git 忽略的 demo/output/c-ttm-source.png、c-ttm-pending.png、c-ttm-export.json/.html/.csv。

真实模式只使用 API 的 TaskResult，不从离线包补原始事实。当前 ValuationData 不包含原始事实对象，Q6 尚未提供冻结的事实读取接口，因此真实模式显示 Fact ID 和“未读取原始金额”，并保留估值来源按钮；不能宣称真实三项原始金额展开已完成。

## C2：端到端与拒绝场景

```powershell
python -m unittest eval.test_ttm_display -v
python -m pytest tests eval/test_c_modules.py eval/test_c_runtime.py eval/test_annotations.py eval/test_ttm_display.py -q
npm --prefix web test
npm --prefix web run test:ttm
npm --prefix web run test:live
```

- main + C 新模块首次完整回归：168 passed、18 subtests passed，公共契约通过。
- PR #7 + C 新模块组合回归：183 passed、45 subtests passed，公共契约通过。生产工具计算虚构区间 1200/1500/2400 CNY，完整链引用闭合。
- C 的实际 HTTP 用例生成虚构文本层 PDF，走 A 实际解析、B 事实快照与生产工具，再核对 TaskResult 与示例期望、四项 TTM、结果/轨迹重放和事件链。错误金额单位在 API 中返回 failed/VALIDATION_ERROR；不伪装为正常成功。
- 业务用例覆盖缺项、未复核、跨公司/口径/指标、期间起点错误、重述不一致、非正目标利润、少于三家同业、未来披露、行情日期不符。工具月份/闰年独立测试由 PR #7 提供；C 另区分十二个月识别的闰年与差一天拒绝。
- 浏览器验证三项来源跳转、派生标记、空区间、HTML/CSV/JSON 完整导出、移动布局，以及真实模式不混入缓存的虚构事实。生产工具组合结果用于浏览器验收；路由拦截只用于界面测试，不作为真实业务服务。

## C3：公开内容检查与真实定位待办

检查范围分别为 PR #6 的 app/tasks/valuation 新增/更新文件（18 个）及 PR #7 的 13 个变更文件；检查私人 Document/Fact/Evidence/Run 标识、个人复核 JSON 台账和本机绝对路径，未发现命中。虚构 samples 的合法引用保留；官方来源 URL 与公开 PDF 哈希保留。这是限定范围检查，不宣称整个历史仓库不存在私人信息，也不等于重新确认全部真实财报金额。

蔚蓝锂芯半年报归母行“框选过窄”的真实定位复核仍待 B 的内部资料包。当前工作区未找到其原件、实际证据坐标及复核清单；用户正在询问目录。收到后逐项检查物理页码、行名、列名、单位、bbox 覆盖，并将问题反馈给 B/A；实际查看前不写“已确认”，真实截图留在获准分享的内部目录。

## C 对后续公共契约的意见

Q9：C 有条件同意可选 derivation/calculation_id 的方向：旧 Fact 缺字段按 disclosed，calculated 必须有正式计算引用且值/单位/期间/状态对应上游，disclosed 不携带非空 calculation_id；保留原始证据和完整计算链。必须防止人工手填派生 verified 绕过上游状态。A 提交具体 Schema、存储/迁移与非法样例后再正式审阅；本次不启用这些字段。

Q6：C 支持分别评审已经列明的 parse、evidence、replay、export 四条内部路径，以及事实读取的具体方案；不预先批准未列明的第五条路径或将路径数直接改为 15。真实事实读取须绑定本 Run 的文档/事实快照，不能读取可变总清单污染旧结果展示。

PR #7 的 C 展示/测试意见与 C 自身交付 PR 均保留给团队审阅。本轮不代替 A/B 的金融或接口签字，不主动合并任何待审 PR。
