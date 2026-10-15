# C 二次反馈修订计划

这两份计划是公开定性语句和逐字段修改说明，不包含真实财务附录、内部定位或个人历史。对 C 的正式回复在 PR #17。新结果明确为本地 AI 工程修订，保持 needs_review，不是云端生成质量或真人裁决。

industry.json 同时修正结论、假设、限制、边和情景假设，不只修情景正文。memo.json 将真实续行证据加入该条陈述本身。所有旧字段须与计划一致；不是任意覆盖工具。缺绑定、改错版本、快照与事件不一致均拒绝。

已有完整私有运行存储的开发者可直接复现，不需另收压缩包：

```bash
PYTHONPATH=vendor/python python -m app.tasks.synthesis_revision \
  --data-dir PRIVATE_STORE --source-run ORIGINAL_ENGINEERING_RUN \
  --plan samples/semantic/c_second_review/industry.json

PYTHONPATH=vendor/python python -m app.tasks.synthesis_revision \
  --data-dir PRIVATE_STORE --source-run ORIGINAL_MEMO_RUN \
  --plan samples/semantic/c_second_review/memo.json \
  --bind header=HEADER_EVIDENCE --bind first_line=FIRST_EVIDENCE \
  --bind continuation=CONTINUATION_EVIDENCE
```

来源运行和三条证据取自 C 已核对的私有资料，不能编造；续行必须原本就在该 Run 固定的定性资料中。代码读取事件哈希绑定的原模型快照，生成新 Run；不会覆盖旧结果，也不会把旧模型型号沿用为新稿实际调用。新稿型号为 local-ai-engineering-draft，修订号指向原 Run，外部模型调用为零。

结果在 PRIVATE_STORE/runs/新Run，另导出 JSON/HTML，并生成 annotation_a_blank.json 和 annotation_b_blank.json；它们按 C 现有模板函数绑定新结果及其模板哈希，标签为空、split=development。模板的全结果摘要和 Orchestrator 去运行 ID／时间戳的正式 result_hash 是不同哈希基，不应混用。

C 的审阅包不含原始事件和模型响应，不能单靠审阅包执行这个生成命令或独立重放。A/B 的新运行与模板在完整私有存储中生成，PR 给出具体改句与检查结论供 C 逐项复核；不把 PR 文本检查冒称为 C 已独立重放。四条已通过的财务定位和既有确认状态保持原样。
