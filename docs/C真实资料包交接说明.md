# A/B 真实资料向 C 的私有交接

C 在另一台电脑或独立账户时，不能直接访问 B 的本机路径。真实资料位于被 Git 忽略的 var/；拉取 GitHub 仓库只获得代码与合成样例，不会自动获得原始 PDF、私有数据库、财务清单或运行结果。

## C 接收后的操作

1. 通过团队私有渠道接收 A/B 提供的 ZIP，解压到新的本机私有目录。不要上传公开仓库。
2. 在解压目录运行 `python verify_bundle.py --restore-blobs`，看到 verified=true 后再使用。验证器只依赖 Python 标准库，不需要 B 的路径、模型密钥或项目安装。
3. 打开 `资料入口.html`。documents/ 保存可直接打开的原件；链接带页码，表格列出指标、期间、金额、事实状态、bbox 和待核验提示。原生 PDF 链接不会自动画框，坐标仍须 C 独立检查。
4. 阅读 README_C.md；事实清单为 store/reviewed_facts.json，原始运行结果在 results/，引用和原文定位在 evidence.json。annotations/ 保存空白坐标表及两份独立语义标注模板。

该包是审阅副本。保留原财务数值、状态和证据；去除个人复核历史，不代签新的金融或坐标结论。已确认金额不等于框选已合格。该案例已经用于开发，模板标为 development，不冒称独立公司留出集。

## 页面与 API

校验和恢复原件后，按包内 README 用最新项目代码启动 demo.live，指向包内 store 与去身份事实清单，查看资料时使用 offline-rule 后端。历史结果可从 results/ 或 /runs/运行ID/result 读取；证据与原件可通过现有 API 读取。当前网页没有按 Run ID 直接加载历史结果的控件，不应给 C 一个不存在的操作步骤。

需要现有页面框选时，可按 runs.json 里的 document_ids 与 task_input 新建财报或估值纯工具任务。新 Run 是当前代码上的新计算，不是旧 Run 的重放。历史语义候选已附空白模板，无需再次调用模型。

包中省略模型响应快照、原事件和个人签字历史，故不宣称包含完整离线重放条件。查看副本不改变 B 原始清单；如需复核原事件或重放，另在原版本和原私有环境中进行。

## A/B 重建工具

以下均为占位参数，不是实际事实或定位 ID。私有 spec 示例：

```json
{
  "fact_manifest": "PRIVATE_STORE/reviewed_facts.json",
  "stores": [
    {"data_dir": "PRIVATE_STORE", "run_ids": ["run_REPLACE_WITH_ORIGINAL_ID"]},
    {"data_dir": "OTHER_PRIVATE_STORE", "run_ids": ["run_REPLACE_WITH_OTHER_ID"]}
  ]
}
```

```bash
python3 -m app.export.review_bundle --spec PRIVATE_SPEC.json --output NEW_PRIVATE_DIRECTORY
```

输出目录和同名 ZIP 必须尚不存在，防止覆盖 C 已填写的审阅工作。只按白名单导出所需原件、证据和结果，不复制完整数据库。相同文档在两个 store 中的附加单元格证据合并，碰撞则拒绝；标注公司从原 Run 输入读取，不靠外部标签猜测。source_fact_manifest_sha256 固定原始清单版本；去身份清单的字节哈希与原版不同，不冒充原签字文件。

## 本轮验收与后续

真实交接包包含 23 个文档，其中 13 个 PDF；49 项事实包括原有 45 项确认与 4 项拒绝记录，6 条既有真实运行。实际 ZIP 保留在本机忽略目录，未进入本 PR。

在另一独立临时目录中模拟收包：无项目导入的校验器通过；23 个原件哈希一致，6 条原结果经本机 HTTP 可读、引用闭合。在副本中新建财报分析与估值，分别产生 9 项、31 项计算，模型调用均为零。这是本机跨目录模拟，不冒称已在 C 电脑验收，也不是新的云端生成。

六项新增测试覆盖跨电脑恢复、个人历史/密钥排除、双 store 追加证据、原件损坏拒绝、防覆盖、文件篡改/目录越界，以及标注归属绑定原 Run。全仓 342 项测试通过，公共契约保持 1.0.0、10 条路径。

C 后续核验偏小框选、准备真实来源截图，完成两名真人独立标注与裁决。A 后续可对接历史 Run 的页面读取；B 后续继续将已确认财务与定性判断分开组装。包中所有核验标签保持空白，以上工作未被标为完成。
