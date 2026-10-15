// 展示模型候选与引用，不把模型的 fact 标签提升为已确认金融事实。
export const kindLabels = {
  fact: "事实性陈述（待核实）",
  inference: "条件推论",
  opinion: "观点（待审阅）",
  calculation: "数值陈述（需工具重算）",
  mechanism: "传导机制（待审阅）",
  scenario: "情景推演",
  monitoring: "跟踪建议（无逐项引用字段）",
};
export function synthesisView(result, sample = false) {
  const groups = [],
    issues = [],
    known = new Set(result.evidence_ids);
  const add = (id, title, items) =>
    groups.push({
      id,
      title,
      items: items.map((item, index) => {
        const refs = item.evidence_ids || [];
        const missing = refs.filter((ref) => !known.has(ref));
        if (missing.length)
          issues.push(
            `${title}第${index + 1}项引用未在结果中登记：${missing.join("、")}`,
          );
        if (!refs.length) issues.push(`${title}第${index + 1}项未附来源`);
        if (
          ["inference", "opinion", "scenario"].includes(item.kind) &&
          !(item.assumptions?.length || item.limitations?.length)
        )
          issues.push(`${title}第${index + 1}项缺少假设或限制`);
        if (item.kind === "calculation")
          issues.push(
            `${title}第${index + 1}项为模型数值陈述，不能当作工具已核算`,
          );
        return {
          ...item,
          id: `${id}-${index}`,
          index,
          refs,
          usableRefs: refs.filter((ref) => known.has(ref)),
          missingRefs: missing,
        };
      }),
    });
  if (result.task_type === "investment_memo") {
    for (const [key, title] of [
      ["thesis", "投资论点"],
      ["evidence", "证据陈述"],
      ["risks", "风险"],
      ["disconfirming_conditions", "反证与失效条件"],
    ])
      add(key, title, result.data[key]);
    add(
      "monitoring_indicators",
      "跟踪指标",
      result.data.monitoring_indicators.map((text) => ({
        text,
        kind: "monitoring",
        evidence_ids: [],
        assumptions: [],
        limitations: [],
      })),
    );
  } else if (result.task_type === "industry_impact") {
    const nodes = new Map();
    for (const node of result.data.nodes) {
      if (nodes.has(node.node_id))
        issues.push("产业节点 ID 重复：" + node.node_id);
      nodes.set(node.node_id, node.name);
    }
    add(
      "edges",
      "传导机制",
      result.data.edges.map((edge) => {
        if (!nodes.has(edge.from_id) || !nodes.has(edge.to_id))
          issues.push("传导关系引用未知节点");
        const direction =
          { positive: "正向", negative: "负向", uncertain: "方向不确定" }[
            edge.direction
          ] || edge.direction;
        return {
          ...edge,
          text: `${nodes.get(edge.from_id) || edge.from_id} → ${nodes.get(edge.to_id) || edge.to_id}：${edge.mechanism}`,
          kind: "mechanism",
          assumptions: [],
          limitations: [],
          direction,
        };
      }),
    );
    add(
      "scenarios",
      "情景",
      result.data.scenarios.map((item) => ({
        ...item,
        text: `${item.scenario_id}：${item.impact_text}`,
        kind: "scenario",
        limitations: [],
      })),
    );
    add("conclusions", "条件性结论", result.data.conclusions);
  } else throw new Error("不支持的语义任务类型");
  return { result, sample, groups, issues };
}

export function synthesisRows(view) {
  const rows = [];
  const context = view.sample
    ? "接口协议样例，全部虚构，不代表模型效果"
    : "模型候选，待人工审核";
  for (const group of view.groups)
    for (const item of group.items)
      rows.push({
        context,
        section: group.title,
        id: item.id,
        kind: kindLabels[item.kind] || item.kind,
        text: item.text,
        assumptions: item.assumptions.join(" | "),
        limitations: item.limitations.join(" | "),
        direction: item.direction || "",
        evidence_ids: item.refs.join(" | "),
        status: view.result.status,
        note: item.missingRefs.length
          ? "引用未在结果中登记"
          : item.refs.length
            ? "引用存在不代表结论已核实"
            : "无逐项来源，需人工核对",
      });
  for (const text of view.issues)
    rows.push({
      context,
      section: "结构检查提示",
      text,
      status: "needs_review",
    });
  for (const text of view.result.warnings)
    rows.push({
      context,
      section: "运行警告",
      text,
      status: view.result.status,
    });
  for (const item of view.result.review_items)
    rows.push({
      context,
      section: "人工复核项",
      id: item.review_id,
      text: item.reason,
      evidence_ids: item.evidence_ids.join(" | "),
      status: item.status,
    });
  if (!rows.length)
    rows.push({
      context,
      section: "暂无内容",
      status: view.result.status,
      note: "没有可展示的语义候选，不代表审核通过",
    });
  return rows.map((row) => ({
    run_id: view.result.run_id,
    task_type: view.result.task_type,
    api_version: view.result.api_version,
    generated_at: view.result.generated_at,
    context,
    section: "",
    id: "",
    kind: "",
    text: "",
    assumptions: "",
    limitations: "",
    direction: "",
    evidence_ids: "",
    status: "",
    note: "",
    ...row,
  }));
}
export function synthesisCSV(view) {
  const rows = synthesisRows(view),
    keys = Object.keys(rows[0]);
  const quote = (value) =>
    '"' +
    (/^[=+@\t\r-]/.test(String(value)) ? "'" : "") +
    String(value).replaceAll('"', '""') +
    '"';
  return (
    "\ufeff" +
    [keys, ...rows.map((row) => keys.map((key) => row[key]))]
      .map((row) => row.map(quote).join(","))
      .join("\r\n")
  );
}
export function synthesisHTML(view) {
  const escape = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  const rows = synthesisRows(view),
    keys = Object.keys(rows[0]);
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>语义结果待审稿</title><style>body{font:16px sans-serif;padding:24px}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #bbb;padding:8px;overflow-wrap:anywhere}pre{white-space:pre-wrap}</style><h1>${view.sample ? "全部虚构的接口协议样例" : "模型候选，待人工审核"}</h1><p>模型候选待人工审核。事实性陈述仍待核实；引用存在不代表来源支持该结论。经济主体、条件、风险和反证必须逐项核对。</p><table><thead><tr>${keys.map((key) => `<th>${key}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${keys.map((key) => `<td>${escape(row[key])}</td>`).join("")}</tr>`).join("")}</tbody></table><h2>完整运行记录</h2><pre>${escape(JSON.stringify(view.result, null, 2))}</pre></html>`;
}
