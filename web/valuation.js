// 仅组织和展示工具记录，不在浏览器重算或提升财务复核状态。
export function valuationView(result, source = null) {
  const sample = source !== null;
  const facts = new Map(
    (source?.facts || []).map((fact) => [fact.fact_id, fact]),
  );
  const evidence = new Map(
    (source?.evidence || []).map((item) => [item.evidence_id, item]),
  );
  const documents = new Map(
    (source?.documents || []).map((item) => [item.document_id, item]),
  );
  const calculations = new Map();
  const problems = [];
  for (const calc of result.calculations) {
    if (calculations.has(calc.calculation_id))
      problems.push("重复计算 ID：" + calc.calculation_id);
    calculations.set(calc.calculation_id, calc);
  }
  const visited = new Set();
  const visit = (id, trail = []) => {
    if (trail.includes(id)) {
      problems.push("计算引用存在循环：" + id);
      return;
    }
    if (visited.has(id)) return;
    const calc = calculations.get(id);
    if (!calc) {
      problems.push("缺少上游计算记录：" + id);
      return;
    }
    for (const ref of calc.input_calculation_ids) visit(ref, [...trail, id]);
    visited.add(id);
  };
  for (const id of calculations.keys()) visit(id);
  const entries = result.calculations.map((calc) => ({
    ...calc,
    label:
      calc.formula_code === "ttm_from_ytd"
        ? "工具计算的滚动12个月利润" +
          (calc.parameters.metric === "net_profit_parent_ttm"
            ? "（归母）"
            : calc.parameters.metric ===
                "net_profit_parent_excl_nonrecurring_ttm"
              ? "（扣非归母）"
              : "")
        : calc.formula_code === "nonrecurring_gap_amount"
          ? "归母减扣非差额（有符号）"
          : calc.formula_code === "nonrecurring_gap_share"
            ? "归母与扣非差额占比（绝对值）"
            : "工具计算：" + calc.formula_code,
    period:
      calc.parameters.period_start && calc.parameters.period_end
        ? `${calc.parameters.period_start} 至 ${calc.parameters.period_end}`
        : null,
    inputs: calc.input_fact_ids.map((id, index) => {
      const fact = facts.get(id);
      return {
        id,
        role:
          calc.formula_code === "ttm_from_ytd"
            ? ["上一年度全年", "本年度累计", "上一年度同期累计"][index] ||
              "额外输入"
            : "原始输入",
        fact: fact || null,
        sources: (fact?.evidence_ids || []).map((eid) => {
          const item = evidence.get(eid);
          return {
            id: eid,
            evidence: item || null,
            document: documents.get(item?.document_id) || null,
          };
        }),
      };
    }),
  }));
  return { result, sample, entries, problems };
}

export function exportRows(view) {
  const rows = [];
  const label = view.sample ? "接口演示样例，全部虚构" : "实际运行";
  const range = view.result.data.valuation_range;
  for (const name of ["low", "base", "high"]) {
    const candidates = view.entries.filter(
      (entry) =>
        entry.parameters.valuation_scenario === name &&
        entry.status === "verified" &&
        entry.value === range[name],
    );
    const calculation = candidates.length === 1 ? candidates[0] : null;
    rows.push({
      context: label,
      kind: "估值结果（计算得出）",
      id: name,
      formula: view.result.data.method,
      value: range[name] ?? "",
      unit: range.unit,
      period: view.result.data.as_of_date,
      status: view.result.status,
      reason:
        range.reason || (calculation ? "" : "未读取唯一的估值场景计算引用"),
      input_fact_ids: "",
      input_calculation_ids: calculation?.calculation_id || "",
      parameters: calculation ? JSON.stringify(calculation.parameters) : "",
      evidence_ids: view.result.evidence_ids.join(" | "),
      sources: "",
    });
  }
  for (const entry of view.entries) {
    rows.push({
      context: label,
      kind: "计算得出",
      id: entry.calculation_id,
      formula: entry.formula_code,
      value: entry.value ?? "",
      unit: entry.unit,
      period: entry.period || "",
      status: entry.status,
      reason: entry.reason || "",
      input_fact_ids: entry.input_fact_ids.join(" | "),
      input_calculation_ids: entry.input_calculation_ids.join(" | "),
      parameters: JSON.stringify(entry.parameters),
      sources: "",
    });
    for (const input of entry.inputs)
      rows.push({
        context: label,
        kind: input.fact ? "原始披露（虚构样例）" : "原始事实引用（尚未读取）",
        id: input.id,
        formula: input.role,
        value: input.fact?.value ?? "",
        unit: input.fact?.unit || "",
        period: input.fact
          ? `${input.fact.period_start} 至 ${input.fact.period_end}`
          : "",
        status: input.fact?.review_status || "unavailable",
        reason: input.fact?.reason || "",
        input_fact_ids: "",
        input_calculation_ids: "",
        evidence_ids: (input.fact?.evidence_ids || []).join(" | "),
        sources: input.sources
          .map(
            (source) =>
              `${source.id}; page=${source.evidence?.page ?? "?"}; bbox=${source.evidence?.bbox.join("/") || "?"}; sha256=${source.document?.sha256 || "?"}`,
          )
          .join(" | "),
      });
  }
  for (const warning of view.result.warnings || [])
    rows.push({
      context: label,
      kind: "运行警告",
      status: view.result.status,
      reason: warning,
      evidence_ids: view.result.evidence_ids.join(" | "),
    });
  for (const review of view.result.review_items || [])
    rows.push({
      context: label,
      kind: "人工复核项",
      id: review.review_id,
      status: review.status,
      reason: review.reason,
      evidence_ids: review.evidence_ids.join(" | "),
    });
  for (const problem of view.problems)
    rows.push({
      context: label,
      kind: "计算链需复核",
      id: "",
      formula: "",
      value: "",
      unit: "",
      period: "",
      status: "needs_review",
      reason: problem,
      input_fact_ids: "",
      input_calculation_ids: "",
      sources: "",
    });
  return rows.map((row) => ({
    context: label,
    kind: "",
    id: "",
    formula: "",
    value: "",
    unit: "",
    period: "",
    status: "",
    reason: "",
    input_fact_ids: "",
    input_calculation_ids: "",
    parameters: "",
    evidence_ids: "",
    sources: "",
    ...row,
    chain_status: view.problems.length
      ? "needs_review"
      : view.sample
        ? "sample_references_preserved"
        : "original_facts_unavailable",
  }));
}

export function valuationCSV(view) {
  const rows = exportRows(view),
    keys = Object.keys(rows[0]);
  const quote = (value) => {
    let text = String(value);
    if (
      /^[=+@\t\r]/.test(text) ||
      (/^-/.test(text) && !/^-\d+(\.\d+)?$/.test(text))
    )
      text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return (
    "\ufeff" +
    [keys, ...rows.map((row) => keys.map((key) => row[key]))]
      .map((row) => row.map(quote).join(","))
      .join("\r\n")
  );
}

export function valuationHTML(view) {
  const escape = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  const rows = exportRows(view),
    keys = Object.keys(rows[0]);
  const heading = view.sample ? "接口演示样例，全部虚构" : "实际运行结果";
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>估值计算链</title><style>body{font:16px sans-serif;padding:24px}table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #bbb;padding:8px;overflow-wrap:anywhere}p{white-space:pre-wrap}</style><h1>${heading}</h1><p>派生利润标为计算得出；工具核算状态不代表可比性人工审核完成。</p><p>${escape(view.problems.join("\n"))}</p><table><thead><tr>${keys.map((key) => `<th>${key}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${keys.map((key) => `<td>${escape(row[key])}</td>`).join("")}</tr>`).join("")}</tbody></table><h2>完整运行记录</h2><pre>${escape(JSON.stringify(view.result, null, 2))}</pre></html>`;
}
