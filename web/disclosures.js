// 只投影后端结果；不在展示层重算金额、改写复核状态或补造原始事实。
export const fieldLabels = {
  document_id: "文档 ID",
  company_code: "证券代码",
  event_date: "事件日期",
  shareholder: "出质股东",
  pledged_shares: "本次质押股数",
  pledged_ratio: "占公司总股本比例",
  pledge_start_date: "质押起始日",
  pledge_end_date: "质押到期日",
  pledgee: "质权人",
  project_name: "项目名称",
  winning_party: "中标方",
  contract_amount: "合同金额",
  currency: "币种",
  performance_period: "履约期间",
  holder: "变动主体",
  shares_before: "变动前股数",
  shares_after: "变动后股数",
  ratio_before: "变动前比例",
  ratio_after: "变动后比例",
  change_method: "变动方式",
  revenue: "营业收入",
  net_profit_parent: "归母净利润",
  net_profit_parent_excl_nonrecurring: "扣非归母净利润",
  operating_cash_flow: "经营现金流净额",
  total_assets: "总资产",
  total_liabilities: "总负债",
  accounts_receivable: "应收账款",
  inventory: "存货",
  cost_of_revenue: "营业成本",
};
export const statusLabels = {
  verified: "已核验",
  needs_review: "待复核",
  rejected: "已驳回",
};
export const unitLabels = {
  CNY: "人民币元",
  share: "股",
  percent: "%",
  times: "倍",
  none: "无量纲",
};
export const scopeLabels = {
  consolidated: "合并",
  parent: "母公司",
  not_applicable: "不适用",
};
export const periodLabels = {
  cumulative: "累计",
  single_quarter: "单季",
  instant: "时点",
};
export const announcementLabels = {
  pledge: "股份质押",
  bid_award: "中标",
  equity_change: "权益变动",
};
export const formulaLabels = {
  yoy: "同比变动",
  cash_conversion: "经营现金流／归母净利润",
  gross_margin: "毛利率",
  nonrecurring_gap_share: "归母与扣非利润差额绝对占比",
};

export function disclosureView(result, sample = false) {
  if (
    !["structured_extraction", "financial_analysis"].includes(result.task_type)
  )
    throw new Error("仅接受公告提取或财报分析结果");
  const issues = [],
    known = new Set(result.evidence_ids);
  const refs = (item, label) => {
    const usableRefs = (item.evidence_ids || []).filter((id) => known.has(id));
    const missingRefs = (item.evidence_ids || []).filter(
      (id) => !known.has(id),
    );
    if (missingRefs.length)
      issues.push(`${label}引用未在本结果中登记：${missingRefs.join("、")}`);
    return { usableRefs, missingRefs };
  };
  const records = (result.data.records || []).map((record) => ({
    ...record,
    fields: record.fields.map((field) => ({
      ...field,
      ...refs(field, field.field_code),
    })),
  }));
  const facts = (result.data.facts || []).map((fact) => ({
    ...fact,
    ...refs(fact, fact.fact_id),
  }));
  const factMap = new Map(facts.map((fact) => [fact.fact_id, fact]));
  const calculationIds = new Set(
    result.calculations.map((calc) => calc.calculation_id),
  );
  const calculations = result.calculations.map((calc) => ({
    ...calc,
    inputs: calc.input_fact_ids.map((id) => {
      const fact = factMap.get(id);
      if (!fact)
        issues.push(
          `计算 ${calc.calculation_id} 的原始事实 ${id} 不在本结果中；保留 ID，不补造金额`,
        );
      return { id, fact };
    }),
    missingCalculations: calc.input_calculation_ids.filter(
      (id) => !calculationIds.has(id),
    ),
  }));
  for (const calc of calculations)
    if (calc.missingCalculations.length)
      issues.push(
        `计算 ${calc.calculation_id} 缺少上游计算：${calc.missingCalculations.join("、")}`,
      );
  const alerts = (result.data.alerts || []).map((alert) => ({
    ...alert,
    ...refs(alert, alert.alert_code),
    missingFacts: alert.fact_ids.filter((id) => !factMap.has(id)),
  }));
  for (const alert of alerts)
    if (alert.missingFacts.length)
      issues.push(
        `预警 ${alert.alert_code} 缺少关联事实：${alert.missingFacts.join("、")}`,
      );
  return { result, sample, records, facts, calculations, alerts, issues };
}

export const showValue = (value) =>
  value === null || value === undefined ? "未确定" : String(value);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
const cell = (value) => {
  let text =
    value === null || value === undefined
      ? ""
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  if (/^[\s\uFEFF]*[=+\-@]/u.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
};
function rows(view) {
  const result = [];
  if (!(view.records.length || view.facts.length))
    result.push({
      section: "empty",
      reason: "未返回数据，需检查输入资料与复核项",
    });
  for (const record of view.records)
    for (const field of record.fields)
      result.push({
        section: "extraction",
        id: record.event_id,
        document_id: record.document_id,
        company_code: record.company_code,
        event_date: record.event_date,
        announcement_type: record.announcement_type,
        ...field,
        field_label: fieldLabels[field.field_code] || field.field_code,
      });
  for (const fact of view.facts)
    result.push({
      section: "fact",
      id: fact.fact_id,
      field_label: fieldLabels[fact.metric] || fact.metric,
      ...fact,
    });
  for (const calc of view.calculations)
    result.push({
      section: "calculation",
      id: calc.calculation_id,
      ...calc,
      field_label: formulaLabels[calc.formula_code] || calc.formula_code,
      inputs: undefined,
    });
  for (const alert of view.alerts)
    result.push({ section: "alert", id: alert.alert_code, ...alert });
  for (const warning of view.result.warnings)
    result.push({ section: "warning", reason: warning });
  for (const issue of view.issues)
    result.push({ section: "display_warning", reason: issue });
  for (const review of view.result.review_items)
    result.push({ section: "review", id: review.review_id, ...review });
  return result;
}
export function disclosureCSV(view) {
  const columns = [
    "run_id",
    "task_type",
    "api_version",
    "generated_at",
    "result_status",
    "synthetic",
    "section",
    "id",
    "document_id",
    "company_code",
    "event_date",
    "announcement_type",
    "field_code",
    "metric",
    "field_label",
    "raw_value",
    "value",
    "unit",
    "status",
    "review_status",
    "reason",
    "period_start",
    "period_end",
    "period_type",
    "statement_scope",
    "restated",
    "evidence_ids",
    "formula_code",
    "input_fact_ids",
    "input_calculation_ids",
    "parameters",
    "rounding_rule",
    "description",
    "severity",
    "interpretation_status",
    "fact_ids",
    "target_id",
  ];
  return (
    "\uFEFF" +
    [
      columns.map(cell).join(","),
      ...rows(view).map((row) => {
        const record = {
          ...row,
          run_id: view.result.run_id,
          task_type: view.result.task_type,
          api_version: view.result.api_version,
          generated_at: view.result.generated_at,
          result_status: view.result.status,
          synthetic: view.sample ? true : "not_declared_by_api",
        };
        return columns.map((key) => cell(record[key])).join(",");
      }),
    ].join("\r\n")
  );
}
export function disclosureHTML(view) {
  const notice = view.sample
    ? "全部虚构的接口样例，不代表真实披露或竞赛实测"
    : "实际 API 返回；资料真实性及金融结论仍须审核";
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>来源与字段核查</title>
<style>body{font-family:system-ui;max-width:1000px;margin:2rem auto;padding:1rem}article{border:1px solid #ccd;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
<h1>来源与字段核查</h1><p>${escapeHTML(notice)}</p><p>原文、归一值与计算记录分别保留；空值不表示零；展示不提升核验状态。</p>
<p>${escapeHTML(view.result.run_id)} · ${escapeHTML(view.result.task_type)} · ${escapeHTML(view.result.status)}</p>
${rows(view)
  .map(
    (row) =>
      `<article><h2>${escapeHTML(row.field_label || row.section)}</h2><pre>${escapeHTML(JSON.stringify(row, null, 2))}</pre></article>`,
  )
  .join("")}
<details><summary>完整 TaskResult（含原始元数据与复核项）</summary><pre>${escapeHTML(JSON.stringify(view.result, null, 2))}</pre></details></html>`;
}
