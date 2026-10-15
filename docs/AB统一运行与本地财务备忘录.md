# A/B 统一运行与本地财务备忘录

本轮补齐五类任务共用运行／重放入口，以及备忘录的本地财务附录。公共请求和结果保持契约 1.0.0、10 条路径，不增加 Fact 字段或 API。

## 运行

在项目根目录准备 Python 依赖，按 contracts/examples/requests.json 的形状编写**单个** RunRequest，保存到私有运行目录。文档 ID 须替换为已经上传、解析并固定 SHA-256 的实际 ID；财报／估值同时准备已复核的私有事实清单。

```bash
PYTHONPATH=vendor/python python -m app.tasks.ab_runtime \
  --data-dir PRIVATE_STORE --manifest PRIVATE_MANIFEST \
  run --request PRIVATE_REQUEST.json

PYTHONPATH=vendor/python python -m app.tasks.ab_runtime \
  --data-dir PRIVATE_STORE replay RUN_ID
```

支持 structured_extraction、financial_analysis、valuation、industry_impact、investment_memo。普通 app.cli 的插件范围保持原样；原 app.tasks.synthesis_runtime 参数兼容保留，底层使用统一执行逻辑。

财报、估值和 rules 公告直接使用本地工具，不要求可用云端配置。公告 model 模式及有定性来源的语义任务仍须显式配置模型服务，见 [模型运行说明](模型与语义任务运行说明.md)。备忘录只有财务事实、没有定性摘录时不调用模型，输出财务附录和缺资料复核项，不能充当完整研究结论。

终端只打印 Run ID、状态、计数和哈希；完整财务数值和证据留在 PRIVATE_STORE。每次完成的运行（包括 needs_review）导出私有 JSON／HTML。业务待复核与运行失败分开：前者退出码为 0，后者为 1；重放只有输入、结果、轨迹与事件链全部一致才返回 0。请求错误仅输出契约错误码，不复制私有输入。

## 备忘录的本地事实

模型只读取明确选择且归属符合要求的定性来源；不再把财务 Fact 清单加入提示。模型引用先按该次定性输入核验，不能引用仅在本地财务清单中出现的证据。

随后本地向 MemoData.evidence 追加最多四条原始事实：营业收入、归母净利润、扣非归母净利润、经营现金流净额。每条写明公司、累计起止期间、合并口径、重述状态、人民币元原字符串、本地事实 ID 和原文 evidence_ids。各指标分别取截止日前已披露的最新累计期间，不能假定四条期间相同，不计算增长率或 TTM。计算继续交由财报／估值工具，模型输出的 calculation 声明仍被拒绝。

筛选同时限制公司、合并报表、累计期间、事实期末及证据所属文档的披露日。只追加 verified 且非空的值；零值有效。最新期间待复核、重述待复核或同口径冲突时留下明确复核项，不回退到方便使用的旧值。缺指标时明确缺失。不修改原事实状态、复核历史或原运行。

这只保证本地财务附录的来源和口径；定性摘录和模型因果解释仍须分析员复核。管理层自述不自动成为客观结论，整份备忘录保持 needs_review。

## 离线重放及版本限制

统一入口使用当前 Git 提交及 app/contracts 源码内容摘要标记代码，包括新建、未提交的运行源文件。版本不同即拒绝重放，须切回原提交并恢复原源码及依赖。旧版本 Run 应使用原版本入口；新入口不迁移或覆盖旧 Run。

相同代码下，从原 manifest 恢复模型名、修订、提示版本、公告提取后端和网格开关，核对输入包哈希及数据库 Run 元数据；不要求当前环境保留原模型配置。即使云端环境变量无效，也直接装配离线模型，不向网络回退。

B 任务必须已有本次固定的 b-reviewed-facts.json；丢失时拒绝，不能从可能已变化的全局清单重新生成。model-calls 响应快照继续绑定原事件哈希；丢失或篡改均失败。重放不追加事件、不改写原结果。

运行目录须完整保留原件内容寻址存储、解析证据、数据库、manifest、事实快照、事件和模型响应快照。PR #15 的 C 审阅包没有完整原事件／模型响应，不能代替完整重放包。

## 验收样例

```bash
PYTHONPATH=vendor/python python -m pytest \
  tests/test_ab_runtime.py tests/test_memo_local_facts.py \
  tests/test_model_synthesis_runtime.py -q
PYTHONPATH=vendor/python python contracts/validate_contracts.py
```

全仓回归 357 passed；契约通过 7 requests、7 results、7 entities、10 paths。协议样例使用合成资料和本机 HTTP 响应器，不是实际模型准确率：

- 成功路径：公告规则提取、财报和估值通过统一入口生成结果及导出；移除端点／密钥、改变模型和提取环境变量后按固定快照重放一致。模型公告候选也验证恢复 model 模式，而非误用 rules。
- 财务附录：定性请求不含合成私有财务金额、事实 ID 或财务证据，输出保留大数小数原字符串和来源。全局清单改变后，按本次固定事实重放一致。
- 待复核：缺指标、无定性摘录、最新事实未复核、重述未复核、冲突、未来／无披露日均覆盖。无模型服务不伪造语义结论。
- 失败：换代码、缺事实快照、模型响应丢失／篡改、无效请求均失败，输出不泄露原请求内容。

来源定位通过原 Evidence 的 document_id、page、bbox 保留，可从结果 JSON 回查；不宣称真实 bbox 已获 C 人工验收。

## 后续协作

A/B 继续维护统一入口、来源筛选及计算链，扩大公告类型／表格覆盖，处理真实语义候选的自述归因；不要求用户重签已确认的财务值和市场快照。

C 先通过私有渠道收到最新版 v2 资料包，再按包内 README 核验哈希、定位和标注；只有 Git 代码不足以获取真实资料。展示新附录时使用原 evidence_ids 跳转，保留累计期间、合并和重述说明，不将其显示成模型计算的 TTM 或完整投资结论。独立人工标注、裁决、留出集指标与最终视频提交仍由 C／团队完成，本轮 A/B 工程验收不代签。
