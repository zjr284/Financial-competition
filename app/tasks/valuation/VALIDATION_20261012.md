# 角色 B：估值时间口径与最新底座验收（2026-10-12）

基线为 main 的 0854d75 提交：A 新增运行指标、三种格式导出和失联恢复；PR #5 已合入 B 的财务复核底稿。本轮只修改 B 的估值模块、财务测试和交接文档，公共契约仍为 1.0.0。

## 估值时间口径修复

此前只检查利润期间结束日是否早于估值日，无法阻止使用估值日后才披露的年报、更正公告或行情来源。现在 PE 与 DCF 逐个检查每条事实的全部证据文档：披露日期缺失、无效或晚于 as_of_date 的事实不进入计算，并保留警告和人工复核项。估值日当天披露的来源可用；同日具体时刻未由日期字段表达，仍需分析员复核。证据越出固定输入资料包直接拒绝。

PE 要求目标和可比公司的滚动利润起止日期完全相同。十二个月按日历周年与月末判定，包含闰年二月；不再把仅有 360 天左右的期间当成完整十二个月。行情仍须是同一个估值日。

成功样例：test_pe_accepts_sources_disclosed_on_cutoff 使用合成来源，估值日当天披露可运行，输出通过 TaskResult Schema。失败样例：test_pe_excludes_future_or_undated_market_sources_before_calculation 与 test_dcf_excludes_revenue_disclosed_after_cutoff 返回 needs_review、空估值和零计算；另覆盖多来源中含后续更正、同业利润期间错配、不完整十二个月和闰年边界。这些样例只验证软件行为。

## 运行与验证

```bash
python3 -m unittest discover -s tests -p test_finance_modules.py -v
python3 contracts/validate_contracts.py
# WSL 下 C 的合成 PDF 测试需指定现有中文字体；本机代理需绕过 localhost。
NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost C_DEMO_FONT=/mnt/c/Windows/Fonts/simhei.ttf python3 -m pytest tests/ eval/
```

财务模块 24 项 unittest、B/A 集成测试及契约校验通过；全量团队测试 154 项通过。首次全量测试因 localhost 被代理为 502 和 C 的默认 Windows 字体路径失败，调整本次进程的环境变量后通过，无需修改 A/C 代码。

## 真实资料联调与限制

使用已有人工复核的年报和半年报输入创建新 Run，未更改旧结果或人工事实。两项财报分析均 succeeded，通过 TaskResult Schema；三种格式导出后 result_hash 不变，输入哈希、结果哈希、重放轨迹和事件链验证通过。运行指标记录的计算数与结果一致。详细运行 ID、文件哈希、结果及来源定位输出只保存在 Git 忽略的 var/real_case_300207/role_b_latest_validation.json 和相应私有 Run 目录。

导出仅重排已有输出：财报 CSV 是通用字段，HTML 没有财务专用表格，完整数值与计算引用以 JSON 为准。真实 PE 待复核样例返回 needs_review，原因是缺少合格滚动归母利润输入，估值 low/base/high 均为 null、计算数为 0；Schema、重放与事件链通过。

真实 PE 仍需：三期原始利润接入带 calculation_id 的滚动利润链（接口问题待三人确认）；同日未复权收盘价、股本与公司行为来源复核；B 对候选业务可比性确认。证据框选修复仍归 A。本次日期门禁不替代这些确认，不把预计算利润登记为原始 verified Fact。
