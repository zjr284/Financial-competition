# 独立行业短摘录

本目录保存国家能源局 2026-07-30 公开发布中的一段定性原文，来源、披露日、摘录 SHA-256 和限制见同名 provenance.json。浏览器核对原页面；直接抓取 HTML 断连，故此处只固定所选 TXT，不宣称归档完整原网页。它不是公司财务事实或人工金标。

在模型服务已明确配置的终端，可按以下步骤登记。PRIVATE_STORE、PRIVATE_MANIFEST、DOCUMENT_ID 和 EVIDENCE_ID 均须替换为本机真实参数：

```bash
FINSCOPE_DATA_DIR=PRIVATE_STORE python3 -m app.cli ingest samples/semantic/nea_grid_flexibility_20260730_excerpt.txt --source-url https://www.nea.gov.cn/20260730/44149332963f44e3896720d8c02b9cc7/c.html --disclosure-date 2026-07-30 --parse
python3 -m app.facts.review_workflow --data-dir PRIVATE_STORE --manifest PRIVATE_MANIFEST attach DOCUMENT_ID
python3 -m app.facts.review_workflow --data-dir PRIVATE_STORE --manifest PRIVATE_MANIFEST inspect DOCUMENT_ID
python3 -m app.facts.semantic_sources --data-dir PRIVATE_STORE --manifest PRIVATE_MANIFEST --document-id DOCUMENT_ID --evidence-id EVIDENCE_ID --scope industry
```

attach 适用于已存在的私有清单；若没有清单，先用 init DOCUMENT_ID。选择相关证据后重复登记；不要为行业片段添加公司代码，也不要把示例里的占位 ID 当作真实值。任务运行见 [模型与语义任务说明](../../docs/模型与语义任务运行说明.md)。

可用情景：高峰保供对新型储能等灵活资源的调用增加。分析须明确区分调度调用、新增装机、厂商出货、公司订单和利润，并检查境内电网侧与公司海外用户侧业务的地域、产品适用性差异。没有直接证据时保留不确定性，不能从政策方向推出公司盈利增长。
