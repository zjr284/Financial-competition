# 角色 C 交接单

本次交付研报核查与质量评分的可运行规则基线、评测工具、七题 API 前端、合成资料包与可重录的 C 模块演示。公共契约保持 v1.0.0，未实现/替代 A 的 API、文档解析或 B 的生产财务计算。

## 一键运行与验证

在仓库根目录（Python 3.10+，Node.js 20.19+）：

```powershell
$env:PYTHONUTF8 = '1'
python -m pip install -r contracts/requirements.txt
npm --prefix web ci
python -m demo.build_fixtures
python -m demo.run_demo
python -m eval.smoke
python -m unittest eval.test_c_modules -v
python contracts/validate_contracts.py
python -m demo.serve --port 8765
```

打开 `http://127.0.0.1:8765`。服务器仅绑定本机；只提供 `web/` 静态文件和白名单合成 TXT，不暴露仓库其他目录。端口被占用时可换端口。连接 A 的 API 时填写地址（默认 `http://127.0.0.1:8000/api/v1`），由 A 设置 CORS 或反向代理。上传大小/页数限制由 A 后端部署配置定义，C 前端不伪造这些限制。离线资料不使用任何密钥或网络服务。

浏览器测试及录屏（先生成 `web/demo.json`；可运行一次 `python -m demo.prepare`，不必保持服务器）：

```powershell
npm --prefix web test
npm --prefix web run record
ffmpeg -y -i demo/output/c-demo.webm -c:v libx264 -crf 22 -pix_fmt yuv420p -an -movflags +faststart demo/output/C模块演示.mp4
ffprobe -v error -show_entries format=duration,size:stream=codec_name,width,height -of json demo/output/C模块演示.mp4
```

测试默认使用 Windows Chrome；跨平台设置 `CHROME_PATH` 指向已安装 Chromium/Chrome。浏览器测试用本地请求拦截加载固定页面文件，测试 API 路由只存在于测试脚本中，不会作为真实业务服务运行。录屏加载实际规则模块生成的结果，无 API mock。演示 MP4 是约 45 秒的 C 模块字幕演示，无配音，不能代替原计划 4分45秒的全队初赛成片。

## A 的集成入口

```python
from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.report_quality import ReportQualityTask

# 注册至 A 的任务插件表；均满足 contracts.ports.TaskPlugin。
plugins = {
    "research_verification": ResearchVerificationTask(),
    "report_quality": ReportQualityTask(),
}
fragment = plugins[request["task_type"]].run(context, request["task_input"])
# A 填充 TaskResult 的运行元数据；fragment.review_items 非空则 needs_review。
```

输入分别匹配 `VerificationInput` / `QualityInput`，模块只返回 `data, evidence_ids, calculations, review_items, warnings`；data 分别匹配 `VerificationData` / `QualityData`。A 补齐 `TaskResult`。内部及导出边界通过 JSON Schema 校验。

`DocumentPort.parse_document()` 返回 evidence_ids；C 不自行创建生产证据。事实通过 `EvidencePort.extract_facts` 读取，必须满足 Fact 契约。公司、文件、证据版本和草稿/来源隔离都会校验。日志通过 EventPort 写出，包含文件/工具访问摘要及输入输出哈希，不保存原文或完整模型响应。异常传播给 A，由 A 映射 Run.error，不能在异常时返回空的“成功”。

## B 的适配约定

只接受 verified、非空且有完整独立来源的 Fact。同比按 `calculate("yoy", [current_fact_id, prior_fact_id], [], {})` 调用并校验返回公式、输入和 percent 单位；计算结果输出 Calculation，声明仅引用 calculation_id。若适配器支持 register_facts，先登记本次已核查事实并记录调用事件；纯 Protocol 适配器可预装事实。没有同期间正基期或存在多个追溯版本时不自行推断。生产工具由 B 提供，`demo/adapters.py` 仅实现合成同比测试，不能注册为生产 CalculationPort。

已发现上游 `feat/finance-valuation` 的 B 模块。可在 fetch 后运行 `python -m eval.integration_b --ref upstream/feat/finance-valuation`，只读导出其 facts/tools 至临时目录，使用实际 FinancialCalculator 验证登记和同比，不把 B 代码复制到 C 的提交。

金额文字解析用 Decimal，不做财务估值或比率运算；金额与归一化 Fact 比较，按声明最末位精度半单位容差。同比下降按负号处理；修订统一写“同比变动”加有符号工具值，避免“下降−20%”双重负号。

## 成功、错误和待复核样例

`demo/fixtures/source.txt` / `draft.txt` / `success.txt` 和 package.json 均为虚构公司 999999 的合成资料，文件哈希固定。TXT 坐标仅表示演示页面，明确不冒充 PDF OCR 坐标。成功样例两条声明全部 correct。混合样例九条声明：2 correct、2 incorrect、5 needs_review；输出具体数字错误、单位错误、期间/口径待核查与无法验证的语义句。JSON、事件和版本清单位于 `demo/output/`。

质量评分为公开的规则暂定分：准确性40、来源25、口径期间20、风险反证15，扣分有原句及来源证据，维度扣分封顶，缺公司/解析时不给相关维度分数。人工复核项始终保留；关键词只能说明存在字面内容，不能证明风险讨论充分。重复运行 data、计算和复核 ID 一致；事件 ID/时间和 TaskResult.generated_at 按真实调用更新。

前端支持七题请求、上传登记、真实 Run 状态轮询、错误/超时处理、needs_review 结果、复核提交、JSON 导出。PDF 先校验登记 SHA-256，再用仓库本地 pdf.js 旋转后渲染，归一化框选直接覆盖原页面；非 PDF 只展示原文与坐标，不声称可靠框选。后台返回文本全用 textContent，避免把报告内容当成 HTML。

## 评测与人工核验状态

`eval/标注规范.md` 定义双人标注、独立裁决及按公司划分。评测拒绝跨集合公司泄漏、重复/多余预测；缺预测计弃权/漏报，输出 precision、recall、误报率、覆盖率、类别准确率和失败清单，分母为零为 null。

`python -m eval.smoke` 是两个虚构公司、每组9声明的代码冒烟测试，不是真实留出集。输出显式 synthetic=true；人工双人标注、真实公司留出集实测及 B 的金融事实签字尚未完成。不得将这里的合成指标放入计划书作为竞赛成果。

真实数据评测：按规范完成源文件固定和双人裁决后，gold JSON 格式为 `{dataset_id, synthetic:false, records:[{claim_id,company_code,split,decision,error_type}]}`；每个集合单独导出预测 checks 或 TaskResult：

```powershell
python -m eval.run_eval --gold PATH_TO_GOLD --predictions PATH_TO_PREDICTIONS --split holdout --output eval/output/real-holdout.json
```

## 已知限制与下一步

- 这是无需模型的保守规则基线。支持明确的单指标/数值/公司期间口径句和同比；预测、条件句、复合句、引用真实性、估值倍数与产业逻辑均 needs_review，不自动判错。尚未接入国产模型候选抽取，不声称满足全队“大模型核心”验收。
- 原始来源真实性和 Fact 的 verified 标记依赖 A/B；C 验证关联及口径，不代替人工金融复核。
- QualityInput 缺公司代码，只在原始事实唯一公司时核验。多公司报告先复核。评分不是投研投资建议或主观质量终审。
- A/B 当前仓库未有服务。前端真实模式的上传、轮询及复核已通过浏览器路由测试，尚未与生产 API 联调；EventPage 当前展示第一页，并保留 next_cursor 原值。
- 真实双人标注、留出集实测、全系统视频与最终提交，需要团队实际资料、A/B 服务与人工复核。本次已交付工具及可录制的 C 模块部分，未代签这些结论。
- 两项公共接口适配疑问已记入 `docs/接口问题记录.md`，没有私改冻结字段。

建议团队后续按“原始资料登记 → B 确认事实/计算 → C 双人标注 → A 接入插件 → 七题联调 → 留出集冻结 → 全队录屏”顺序验收。
