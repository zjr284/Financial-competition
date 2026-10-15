import { valuationView, valuationHTML, valuationCSV } from "./valuation.js";
import {
  disclosureView,
  disclosureHTML,
  disclosureCSV,
  fieldLabels,
  statusLabels,
  unitLabels,
  scopeLabels,
  periodLabels,
  announcementLabels,
  formulaLabels,
  showValue,
} from "./disclosures.js";
import {
  synthesisView,
  synthesisHTML,
  synthesisCSV,
  kindLabels,
} from "./synthesis.js";
const $ = (id) => document.getElementById(id);
const state = {
  mode: "sample",
  data: null,
  result: null,
  base: "",
  poll: null,
  epoch: 0,
  evidenceEpoch: 0,
  urls: [],
  eventRun: null,
  eventPage: null,
  valuation: null,
  synthesis: null,
  disclosure: null,
};
const names = {
  structured_extraction: "公告结构化提取",
  financial_analysis: "财报分析",
  industry_impact: "产业链影响",
  valuation: "估值",
  research_verification: "研报核查",
  investment_memo: "投资备忘录",
  report_quality: "报告质量",
};
const inputs = {
  structured_extraction: {
    announcement_types: ["pledge", "bid_award", "equity_change"],
  },
  financial_analysis: {
    company_code: "999999",
    periods: ["2025-12-31"],
    statement_scope: "consolidated",
  },
  industry_impact: {
    focus_company_code: "999999",
    event_description: "填写实际事件",
    scenario_ids: ["base"],
  },
  valuation: {
    company_code: "999999",
    method: "relative_pe",
    as_of_date: "2026-10-10",
    statement_scope: "consolidated",
  },
  research_verification: {
    draft_document_id: "doc_REPLACE",
    company_code: "999999",
  },
  investment_memo: { company_code: "999999", as_of_date: "2026-10-10" },
  report_quality: { report_document_ids: ["doc_REPLACE"] },
};
const labels = {
  correct: "核验通过",
  incorrect: "明确错误",
  needs_review: "待复核",
  succeeded: "已完成",
  running: "运行中",
  queued: "排队中",
  failed: "运行失败",
  cancelled: "已取消",
};
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function status(message) {
  $("status").textContent = message;
}
function reportError(error) {
  status("操作失败：" + error.message);
}
function reset() {
  state.epoch++;
  state.evidenceEpoch++;
  clearTimeout(state.poll);
  state.poll = null;
  $("stop").disabled = true;
  $("run").disabled = false;
  $("load-existing").disabled = false;
  state.urls.forEach(URL.revokeObjectURL);
  state.urls = [];
  state.result = null;
  state.valuation = null;
  state.synthesis = null;
  state.disclosure = null;
  $("export-disclosure-html").hidden = true;
  $("export-disclosure-csv").hidden = true;
  $("export-synthesis-html").hidden = true;
  $("export-synthesis-csv").hidden = true;
  $("export-html").hidden = true;
  $("export-csv").hidden = true;
  state.eventRun = null;
  state.eventPage = null;
  $("more-events").hidden = true;
  $("more-events").disabled = false;
  $("export").disabled = true;
  for (const id of [
    "results",
    "summary",
    "calculations",
    "events",
    "raw",
    "evidence-text",
    "evidence-meta",
    "location-note",
    "text-page",
  ])
    $(id).replaceChildren();
  $("bbox").hidden = true;
  $("pdf-canvas").hidden = true;
  $("source-link").hidden = true;
  $("reviews").replaceChildren(node("h3", "人工复核"));
}
async function request(path, options = {}) {
  const response = await fetch(state.base + path, options);
  if (!response.ok) {
    let detail;
    try {
      detail = await response.json();
    } catch {}
    throw new Error(detail?.message || `HTTP ${response.status}`);
  }
  return response;
}
async function jsonRequest(path, options = {}) {
  return (await request(path, options)).json();
}
function renderEvents() {
  $("events").textContent = JSON.stringify(
    { run: state.eventRun, event_page: state.eventPage },
    null,
    2,
  );
  $("more-events").hidden = state.eventPage.next_cursor === null;
}
async function loadEvents(run, epoch) {
  const events = await jsonRequest(
    `/runs/${encodeURIComponent(run.run_id)}/events?limit=50`,
  );
  if (epoch !== state.epoch) return;
  state.eventRun = run;
  state.eventPage = events;
  renderEvents();
}
$("more-events").onclick = async () => {
  const epoch = state.epoch;
  const run = state.eventRun;
  const cursor = state.eventPage?.next_cursor;
  if (!run || cursor == null) return;
  $("more-events").disabled = true;
  try {
    const next = await jsonRequest(
      `/runs/${encodeURIComponent(run.run_id)}/events?limit=50&cursor=${encodeURIComponent(cursor)}`,
    );
    if (epoch !== state.epoch) return;
    if (next.next_cursor === cursor)
      throw new Error("事件分页游标未前进，请检查后端");
    const known = new Set(
      state.eventPage.events.map((event) => event.event_id),
    );
    state.eventPage = {
      events: [
        ...state.eventPage.events,
        ...next.events.filter((event) => !known.has(event.event_id)),
      ],
      next_cursor: next.next_cursor,
    };
    renderEvents();
  } catch (error) {
    if (epoch === state.epoch) reportError(error);
  } finally {
    if (epoch === state.epoch) $("more-events").disabled = false;
  }
};
function evidenceButton(eid, label) {
  const b = node("button", label);
  b.dataset.evidenceId = eid;
  b.onclick = () => showEvidence(eid).catch(reportError);
  return b;
}
function render(result, run = null) {
  state.result = result;
  if (state.mode === "live") {
    $("mode-badge").textContent = "团队 API · 实际运行结果";
    $("connection").textContent =
      "已读取本次运行结果，审核状态以结果与复核项为准";
  }
  $("export").disabled = false;
  status(
    `${state.mode === "sample" ? "接口演示样例 · " : ""}${labels[result.status] || result.status} · ${result.run_id}`,
  );
  $("results").replaceChildren();
  $("summary").replaceChildren();
  if (result.data.checks) {
    for (const decision of ["correct", "incorrect", "needs_review"]) {
      const tile = node("div", undefined, "metric");
      tile.append(
        node("strong", String(result.data.summary[decision])),
        node("span", labels[decision]),
      );
      $("summary").append(tile);
    }
    for (const check of result.data.checks) {
      const card = node("article", undefined, "card " + check.decision);
      const top = node("div", undefined, "card-top");
      top.append(
        node("span", labels[check.decision]),
        node("span", check.error_type),
      );
      card.append(
        top,
        node("p", check.sentence),
        node("p", check.reason, "reason"),
      );
      if (check.suggested_edit)
        card.append(node("p", "建议：" + check.suggested_edit, "suggestion"));
      const buttons = node("div", undefined, "evidence-buttons");
      buttons.append(evidenceButton(check.draft_evidence_id, "原句定位"));
      check.source_evidence_ids.forEach((eid, i) =>
        buttons.append(evidenceButton(eid, "来源 " + (i + 1))),
      );
      if (check.calculation_id)
        card.append(node("p", "重算 ID：" + check.calculation_id, "reason"));
      card.append(buttons);
      $("results").append(card);
    }
  } else if (result.data.report_scores) {
    for (const score of result.data.report_scores) {
      const card = node("article", undefined, "card needs_review");
      card.append(
        node("h3", `规则暂定评分 ${score.score}/100`),
        node("p", score.document_id),
      );
      for (const d of score.deductions) {
        card.append(
          node("p", `−${d.points} / ${d.dimension}：${d.reason}`, "reason"),
        );
        const buttons = node("div", undefined, "evidence-buttons");
        d.evidence_ids.forEach((eid, i) =>
          buttons.append(evidenceButton(eid, "证据 " + (i + 1))),
        );
        card.append(buttons);
      }
      $("results").append(card);
    }
  } else if (
    ["structured_extraction", "financial_analysis"].includes(result.task_type)
  ) {
    renderDisclosure(result);
  } else if (result.task_type === "valuation") {
    renderValuation(result);
  } else if (
    ["industry_impact", "investment_memo"].includes(result.task_type)
  ) {
    renderSynthesis(result, run);
  } else {
    $("results").append(node("pre", JSON.stringify(result.data, null, 2)));
    result.evidence_ids.forEach((eid, i) =>
      $("results").append(evidenceButton(eid, "证据 " + (i + 1))),
    );
  }
  for (const warning of result.warnings)
    $("results").append(node("p", warning, "reason"));
  $("calculations").textContent = JSON.stringify(result.calculations, null, 2);
  $("raw").textContent = JSON.stringify(result, null, 2);
  renderReviews(result.review_items);
}
function renderDisclosure(result) {
  const view = disclosureView(result, state.mode === "sample");
  state.disclosure = view;
  $("export-disclosure-html").hidden = false;
  $("export-disclosure-csv").hidden = false;
  $("summary").append(
    node(
      "p",
      `${view.sample ? "全部虚构的接口演示样例。" : "实际 API 返回结果。"}原文与归一值分别保留；空值不表示零；计算值不作为财报直接披露值。`,
    ),
  );
  const addRefs = (container, item) => {
    const buttons = node("div", undefined, "evidence-buttons");
    item.usableRefs.forEach((id, index) =>
      buttons.append(evidenceButton(id, "查看字段来源 " + (index + 1))),
    );
    if (item.missingRefs.length)
      container.append(node("p", "引用未在本结果登记，已停止跳转。", "reason"));
    if (!item.usableRefs.length)
      container.append(node("p", "无可用原文引用。", "reason"));
    container.append(buttons);
  };
  for (const record of view.records) {
    const section = node("section", undefined, "disclosure-record");
    section.append(
      node(
        "h3",
        `${announcementLabels[record.announcement_type] || record.announcement_type} · ${record.company_code}`,
      ),
      node(
        "p",
        `事件日期：${showValue(record.event_date)} · ${record.event_id}`,
        "reason",
      ),
      node("p", `固定文档：${record.document_id}`, "reason"),
    );
    const metadata = new Set(["document_id", "company_code", "event_date"]);
    const orderedFields = [
      ...record.fields.filter((field) => !metadata.has(field.field_code)),
      ...record.fields.filter((field) => metadata.has(field.field_code)),
    ];
    for (const field of orderedFields) {
      const card = node("article", undefined, "card field-card");
      card.dataset.field = field.field_code;
      card.append(
        node("h4", fieldLabels[field.field_code] || field.field_code),
        node(
          "span",
          `${statusLabels[field.status] || field.status} · ${field.status}`,
          "tag",
        ),
        node("p", "原文写法：" + showValue(field.raw_value)),
        node(
          "p",
          `归一值：${showValue(field.value)} · ${unitLabels[field.unit] || field.unit} (${field.unit})`,
        ),
      );
      if (field.reason) card.append(node("p", field.reason, "reason"));
      if (field.field_code === "document_id" && !field.evidence_ids.length)
        card.append(node("p", "运行文档元数据，不是财报披露事实。", "reason"));
      else addRefs(card, field);
      section.append(card);
    }
    $("results").append(section);
  }
  for (const fact of view.facts) {
    const card = node("article", undefined, "card financial-fact");
    card.id = "fact-" + fact.fact_id;
    card.dataset.metric = fact.metric;
    card.append(
      node("h3", fieldLabels[fact.metric] || fact.metric),
      node(
        "p",
        `后端事实：${showValue(fact.value)} · ${unitLabels[fact.unit] || fact.unit} (${fact.unit})`,
      ),
      node(
        "p",
        `${fact.company_code} · ${scopeLabels[fact.statement_scope] || fact.statement_scope} · ${periodLabels[fact.period_type] || fact.period_type} · ${fact.period_start} 至 ${fact.period_end}`,
      ),
      node(
        "p",
        `${statusLabels[fact.review_status] || fact.review_status} · ${fact.review_status} · ${fact.restated ? "追溯调整" : "非追溯调整"}`,
        "reason",
      ),
      node("p", fact.fact_id, "reason"),
    );
    if (fact.reason) card.append(node("p", fact.reason, "reason"));
    card.append(
      node("p", "接口未提供原文数值写法；点击来源核对原件。", "reason"),
    );
    addRefs(card, fact);
    $("results").append(card);
  }
  const factButton = (id) => {
    const button = node("button", "查看输入事实 " + id);
    button.onclick = () =>
      document
        .getElementById("fact-" + id)
        ?.scrollIntoView({ block: "center" });
    return button;
  };
  for (const calc of view.calculations) {
    const card = node("article", undefined, "card financial-calculation");
    card.id = "calculation-" + calc.calculation_id;
    card.append(
      node(
        "h3",
        `工具计算 · ${formulaLabels[calc.formula_code] || calc.formula_code}`,
      ),
      node(
        "p",
        `计算值：${showValue(calc.value)} · ${unitLabels[calc.unit] || calc.unit} (${calc.unit})`,
      ),
      node(
        "p",
        `${statusLabels[calc.status] || calc.status} · ${calc.status} · 舍入：${calc.rounding_rule}`,
        "reason",
      ),
      node("p", calc.calculation_id, "reason"),
    );
    if (calc.reason) card.append(node("p", calc.reason, "reason"));
    for (const input of calc.inputs) {
      if (input.fact) card.append(factButton(input.id));
      else
        card.append(
          node("p", "输入事实未在本结果提供，仅保留 ID：" + input.id, "reason"),
        );
    }
    for (const id of calc.input_calculation_ids) {
      const target = view.calculations.find(
        (item) => item.calculation_id === id,
      );
      if (!target) card.append(node("p", "上游计算缺失：" + id, "reason"));
      else {
        const button = node("button", "查看上游计算 " + id);
        button.onclick = () =>
          document
            .getElementById("calculation-" + id)
            ?.scrollIntoView({ block: "center" });
        card.append(button);
      }
    }
    card.append(node("pre", JSON.stringify(calc.parameters, null, 2)));
    $("results").append(card);
  }
  for (const alert of view.alerts) {
    const card = node("article", undefined, "card financial-alert");
    const interpretations = {
      supported: "后端标记有资料支持，仍需核对解释",
      hypothesis: "待验证假设",
      unresolved: "原因尚未确定",
    };
    card.append(
      node("h3", "核查提示 · " + alert.alert_code),
      node("p", alert.description),
      node(
        "p",
        `级别：${alert.severity} · ${interpretations[alert.interpretation_status] || alert.interpretation_status}`,
        "reason",
      ),
    );
    for (const id of alert.fact_ids) {
      if (view.facts.some((fact) => fact.fact_id === id))
        card.append(factButton(id));
      else card.append(node("p", "关联事实未提供：" + id, "reason"));
    }
    addRefs(card, alert);
    $("results").append(card);
  }
  if (!(view.records.length || view.facts.length))
    $("results").append(
      node(
        "p",
        "未返回公告记录或财务事实；请核对资料范围及人工复核项。",
        "reason",
      ),
    );
  for (const issue of view.issues)
    $("results").append(node("p", issue, "reason"));
}
function renderSynthesis(result, run = null) {
  const view = synthesisView(result, state.mode === "sample");
  state.synthesis = view;
  $("export-synthesis-html").hidden = false;
  $("export-synthesis-csv").hidden = false;
  $("summary").append(
    node(
      "p",
      `${view.sample ? "全部虚构的接口协议样例，不代表模型效果。" : run?.model_id === "local-ai-engineering-draft" ? "本地 AI 工程修订稿，不代表云端生成质量通过或真人裁决。" : "模型生成候选，须人工审核。"}事实性陈述仍待核实；引用存在不等于结论成立。`,
    ),
  );
  if (result.task_type === "industry_impact") {
    const nodes = node("div", undefined, "industry-nodes");
    const stages = {
      upstream: "上游",
      midstream: "中游",
      downstream: "下游",
      company: "目标公司",
    };
    for (const item of result.data.nodes)
      nodes.append(
        node(
          "span",
          `${item.name} · ${stages[item.stage] || item.stage}`,
          "tag",
        ),
      );
    $("results").append(nodes);
  }
  for (const group of view.groups) {
    const section = node("section", undefined, "semantic-section");
    section.dataset.section = group.id;
    section.append(node("h3", group.title));
    if (!group.items.length)
      section.append(node("p", "暂无内容，须检查来源与任务复核项。", "reason"));
    for (const item of group.items) {
      const card = node("article", undefined, "card semantic-card");
      card.append(
        node("span", kindLabels[item.kind] || item.kind, "tag"),
        node("p", item.text),
      );
      if (item.direction)
        card.append(node("p", "模型标注方向：" + item.direction, "reason"));
      for (const [title, values] of [
        ["假设", item.assumptions],
        ["限制", item.limitations],
      ]) {
        card.append(
          node(
            "p",
            `${title}：${values.length ? values.join("；") : "未提供"}`,
            "reason",
          ),
        );
      }
      const buttons = node("div", undefined, "evidence-buttons");
      item.usableRefs.forEach((id, index) =>
        buttons.append(evidenceButton(id, "查看依据 " + (index + 1))),
      );
      if (!item.refs.length)
        card.append(node("p", "无逐项来源，需人工核对。", "reason"));
      if (item.missingRefs.length)
        card.append(
          node("p", "引用未在本结果中登记，已停止来源跳转。", "reason"),
        );
      card.append(buttons);
      section.append(card);
    }
    $("results").append(section);
  }
  for (const issue of view.issues)
    $("results").append(node("p", issue, "reason"));
}
function renderValuation(result) {
  let source = state.mode === "sample" ? state.data.package : null;
  if (source && $("sample-result").value === "4")
    source = { ...source, facts: source.ttm_pending_facts };
  const view = valuationView(result, source);
  state.valuation = view;
  $("export-html").hidden = false;
  $("export-csv").hidden = false;
  const range = result.data.valuation_range;
  for (const [key, label] of [
    ["low", "低值"],
    ["base", "基准"],
    ["high", "高值"],
  ]) {
    const tile = node("div", undefined, "metric");
    tile.append(
      node("strong", range[key] === null ? "暂无结果" : range[key]),
      node("span", `${label} · ${range.unit}`),
    );
    $("summary").append(tile);
  }
  $("results").append(
    node(
      "p",
      `${view.sample ? "接口演示样例，全部数据虚构。" : ""}估值日 ${result.data.as_of_date}。工具已核算与可比性仍待人工审查分别记录；计算得出的利润不是财报直接披露值。`,
      "reason",
    ),
  );
  if (range.reason) $("results").append(node("p", range.reason, "reason"));
  for (const problem of view.problems)
    $("results").append(
      node("p", problem + "；计算链不完整，须复核。", "reason"),
    );
  for (const entry of view.entries) {
    const ttm = entry.formula_code === "ttm_from_ytd";
    const card = node(
      ttm ? "article" : "details",
      undefined,
      ttm ? "card ttm-card" : "calculation-card",
    );
    card.id = "calculation-" + entry.calculation_id;
    card.append(node(ttm ? "h3" : "summary", entry.label));
    card.append(
      node(
        "p",
        `计算得出 · ${entry.status === "verified" ? (view.sample ? "样例工具已核算" : "工具已核算") : "工具待复核"} · ${entry.status === "verified" && entry.value !== null ? `${entry.value} ${entry.unit}` : "暂无核算结果"}`,
      ),
    );
    if (ttm) card.append(node("p", "上年全年 ＋ 本年累计 − 上年同期累计"));
    if (entry.period) card.append(node("p", entry.period));
    card.append(node("p", entry.calculation_id, "reason"));
    if (entry.reason) card.append(node("p", entry.reason, "reason"));
    for (const input of entry.inputs) {
      const row = node("div", undefined, "fact-input");
      row.append(node("strong", input.role), node("p", input.id, "reason"));
      if (input.fact) {
        row.append(
          node(
            "p",
            `原始披露（虚构样例）：${input.fact.value ?? "缺失"} ${input.fact.unit} · ${input.fact.review_status}`,
          ),
        );
        row.append(
          node(
            "p",
            `${input.fact.company_code} · ${input.fact.metric} · ${input.fact.statement_scope} · ${input.fact.period_start} 至 ${input.fact.period_end}`,
            "reason",
          ),
        );
        for (const source of input.sources)
          row.append(evidenceButton(source.id, "查看原始证据"));
      } else
        row.append(
          node(
            "p",
            "原始事实接口尚待评审；当前仅保留 Fact ID，未读取原始金额。",
            "reason",
          ),
        );
      card.append(row);
    }
    for (const ref of entry.input_calculation_ids) {
      const button = node("button", "查看上游计算 " + ref);
      button.onclick = () => {
        const target = document.getElementById("calculation-" + ref);
        if (!target) {
          reportError(new Error("上游计算记录缺失"));
          return;
        }
        if (target.tagName === "DETAILS") target.open = true;
        target.scrollIntoView({ block: "center", behavior: "smooth" });
      };
      card.append(button);
    }
    $("results").append(card);
  }
  const sources = node("div", undefined, "evidence-buttons");
  result.evidence_ids.forEach((id, index) =>
    sources.append(evidenceButton(id, "估值来源 " + (index + 1))),
  );
  $("results").append(sources);
}
function renderReviews(reviews) {
  $("reviews").replaceChildren(node("h3", "人工复核"));
  for (const r of reviews) {
    const card = node("div", undefined, "review-card");
    card.append(
      node("p", r.reason),
      node("small", `${r.review_id} · ${r.status}`),
    );
    if (state.mode === "live" && r.status === "pending") {
      const reviewer = node("input");
      reviewer.placeholder = "复核人";
      reviewer.setAttribute("aria-label", "复核人");
      const note = node("textarea");
      note.placeholder = "复核依据";
      note.setAttribute("aria-label", "复核依据");
      const value = node("input");
      value.placeholder = "修正值（仅 correct 必填）";
      value.setAttribute("aria-label", "修正值");
      const canCorrect = state.result.task_type === "structured_extraction";
      value.hidden = !canCorrect;
      const decision = node("select");
      for (const d of canCorrect
        ? ["accept", "reject", "correct"]
        : ["accept", "reject"]) {
        const option = node("option", d);
        option.value = d;
        decision.append(option);
      }
      const submit = node("button", "提交人工决定");
      submit.onclick = async () => {
        const epoch = state.epoch;
        try {
          if (!reviewer.value.trim() || !note.value.trim())
            throw new Error("请填写复核人与依据");
          if (decision.value === "correct" && !value.value.trim())
            throw new Error("修正决定需要修正值");
          submit.disabled = true;
          const updated = await jsonRequest(
            `/reviews/${encodeURIComponent(r.review_id)}/decision`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                decision: decision.value,
                reviewer: reviewer.value.trim(),
                note: note.value.trim(),
                corrected_value:
                  decision.value === "correct" ? value.value.trim() : null,
              }),
            },
          );
          if (epoch !== state.epoch) return;
          r.status = updated.status;
          renderReviews(state.result.review_items);
          status("人工决定已保存；结果是否重跑由后端状态机决定");
        } catch (e) {
          if (epoch === state.epoch) reportError(e);
        } finally {
          submit.disabled = false;
        }
      };
      card.append(reviewer, note, decision, value, submit);
      if (!canCorrect)
        card.append(
          node(
            "small",
            "此任务记录复核决定；修改草稿或事实后启动新任务，当前结果保持原运行版本。",
          ),
        );
    } else {
      card.append(
        node(
          "p",
          state.mode === "sample"
            ? "样例复核项，不写入实际记录。"
            : "此项已有复核决定。",
          "reason",
        ),
      );
    }
    $("reviews").append(card);
  }
}
async function showEvidence(eid) {
  const epoch = ++state.evidenceEpoch;
  const ev =
    state.mode === "sample"
      ? state.data.package.evidence.find((e) => e.evidence_id === eid)
      : await jsonRequest("/evidence/" + encodeURIComponent(eid));
  if (!ev) throw new Error("证据不存在");
  const doc =
    state.mode === "sample"
      ? state.data.package.documents.find(
          (d) => d.document_id === ev.document_id,
        )
      : await jsonRequest("/documents/" + encodeURIComponent(ev.document_id));
  if (epoch !== state.evidenceEpoch) return;
  $("evidence-meta").textContent =
    `${doc.filename} · 第 ${ev.page} 页 · bbox [${ev.bbox.join(", ")}] · SHA-256 ${doc.sha256}`;
  $("evidence-text").textContent = ev.text;
  $("pdf-canvas").hidden = true;
  $("bbox").hidden = true;
  $("text-page").replaceChildren();
  $("text-page").hidden = doc.media_type === "application/pdf";
  $("source-link").hidden = true;
  $("page").style.height = "";
  const url =
    state.mode === "sample"
      ? "/fixtures/" + encodeURIComponent(doc.filename)
      : state.base +
        "/documents/" +
        encodeURIComponent(doc.document_id) +
        "/content";
  const response = await fetch(url);
  if (!response.ok) throw new Error("读取固定原件失败");
  const bytes = await response.arrayBuffer();
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
  if (hash !== doc.sha256)
    throw new Error("原件 SHA-256 与登记版本不符，已停止展示");
  if (epoch !== state.evidenceEpoch) return;
  const objectUrl = URL.createObjectURL(
    new Blob([bytes], { type: doc.media_type }),
  );
  state.urls.push(objectUrl);
  $("source-link").href = objectUrl;
  $("source-link").hidden = false;
  if (doc.media_type === "application/pdf") {
    const pdfjs = await import("./node_modules/pdfjs-dist/build/pdf.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc =
      "./node_modules/pdfjs-dist/build/pdf.worker.mjs";
    const pdf = await pdfjs.getDocument({ data: new Uint8Array(bytes) })
      .promise;
    const page = await pdf.getPage(ev.page);
    if (epoch !== state.evidenceEpoch) {
      await pdf.destroy();
      return;
    }
    const raw = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({
      scale: $("page").clientWidth / raw.width,
    });
    const canvas = $("pdf-canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.hidden = false;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport })
      .promise;
    if (epoch !== state.evidenceEpoch) {
      await pdf.destroy();
      return;
    }
    $("page").style.height = viewport.height + "px";
    drawBox(ev.bbox);
    $("location-note").textContent =
      "固定 PDF 原件已校验哈希；框选坐标按旋转后页面归一化。";
    await pdf.destroy();
  } else if (state.mode === "sample") {
    $("page").style.height = "560px";
    for (const item of state.data.package.evidence.filter(
      (e) => e.document_id === ev.document_id,
    )) {
      const line = node("div", item.text, "synthetic-line");
      line.style.top = item.bbox[1] / 10 + "%";
      $("text-page").append(line);
    }
    $("text-page").style.height = "100%";
    drawBox(ev.bbox);
    $("location-note").textContent =
      "合成 TXT 页面定位示意，非真实 PDF/OCR 页；原件文本哈希已校验。";
  } else {
    $("text-page").style.height = "";
    if (doc.media_type === "text/plain" || doc.media_type === "text/csv")
      $("text-page").textContent = new TextDecoder().decode(bytes);
    else
      $("text-page").textContent =
        "此格式请打开原件；页面无法可靠映射 TXT/CSV/XLSX 坐标。";
    $("location-note").textContent =
      "非 PDF 仅展示原件与坐标，不声称完成页面框选。";
  }
}
function drawBox(bbox) {
  const [x0, y0, x1, y1] = bbox;
  Object.assign($("bbox").style, {
    left: x0 / 10 + "%",
    top: y0 / 10 + "%",
    width: (x1 - x0) / 10 + "%",
    height: (y1 - y0) / 10 + "%",
  });
  $("bbox").hidden = false;
}
async function loadSample() {
  reset();
  const epoch = state.epoch;
  state.mode = "sample";
  $("connect").hidden = true;
  $("sample-result").hidden = false;
  $("sample").classList.add("active");
  $("live").classList.remove("active");
  $("mode-badge").textContent = "接口演示样例";
  $("connection").textContent = "合成资料 · 非竞赛实测 · 未接入模型";
  let data;
  try {
    const response = await fetch("demo.json");
    if (!response.ok) throw new Error(`样例读取失败：HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    if (epoch !== state.epoch || state.mode !== "sample") return;
    throw error;
  }
  if (epoch !== state.epoch || state.mode !== "sample") return;
  state.data = data;
  render(state.data.results[Number($("sample-result").value)]);
  $("events").textContent = JSON.stringify(
    { manifest: state.data.manifest, events: state.data.events },
    null,
    2,
  );
}
$("sample").onclick = () => loadSample().catch(reportError);
$("sample-result").onchange = () => loadSample().catch(reportError);
$("live").onclick = () => {
  reset();
  state.mode = "live";
  $("connect").hidden = false;
  $("sample-result").hidden = true;
  $("live").classList.add("active");
  $("sample").classList.remove("active");
  $("mode-badge").textContent = "团队 API · 等待连接";
  $("connection").textContent = "仅在后端响应后展示实际运行结果";
  status("填写 API 地址并检查连接。");
};
function setBase() {
  const url = new URL($("api-base").value.trim(), location.href);
  if (!["http:", "https:"].includes(url.protocol))
    throw new Error("API 地址需使用 HTTP(S)");
  state.base = url.href.replace(/\/$/, "");
}
$("health").onclick = async () => {
  try {
    setBase();
    const health = await jsonRequest("/health");
    if (health.api_version !== "1.0.0") throw new Error("API 版本不匹配");
    $("connection").textContent =
      `${health.status} · 模型${health.model_available ? "可用" : "不可用"}`;
    $("mode-badge").textContent = "团队 API 已连接";
  } catch (e) {
    reportError(e);
  }
};
for (const [value, label] of Object.entries(names)) {
  const option = node("option", label);
  option.value = value;
  $("task-type").append(option);
}
$("task-type").value = "research_verification";
function updateInput() {
  $("task-input").value = JSON.stringify(inputs[$("task-type").value], null, 2);
}
$("task-type").onchange = updateInput;
updateInput();
$("upload-button").onclick = async () => {
  try {
    setBase();
    const file = $("upload").files[0];
    if (!file) throw new Error("请选择文件");
    const body = new FormData();
    body.append("file", file);
    const doc = await jsonRequest("/documents", { method: "POST", body });
    const ids = new Set($("document-ids").value.split(/\s+/).filter(Boolean));
    ids.add(doc.document_id);
    $("document-ids").value = [...ids].join("\n");
    status("文件已登记：" + doc.document_id);
  } catch (e) {
    reportError(e);
  }
};
$("load-existing").onclick = async () => {
  let epoch;
  try {
    const runId = $("saved-run-id").value.trim();
    if (!/^run_[A-Za-z0-9_-]+$/.test(runId))
      throw new Error("填写有效的已有 run_id");
    setBase();
    reset();
    epoch = state.epoch;
    $("load-existing").disabled = true;
    status("正在读取已有运行：" + runId);
    const run = await jsonRequest("/runs/" + encodeURIComponent(runId));
    if (epoch !== state.epoch) return;
    if (run.run_id !== runId) throw new Error("返回运行 ID 与请求不一致");
    if (!["succeeded", "needs_review"].includes(run.status)) {
      await loadEvents(run, epoch);
      if (epoch !== state.epoch) return;
      status(
        `已有运行：${labels[run.status] || run.status}；尚无可读取的完成结果。`,
      );
      return;
    }
    const result = await jsonRequest(
      `/runs/${encodeURIComponent(runId)}/result`,
    );
    if (epoch !== state.epoch) return;
    if (result.run_id !== runId || result.task_type !== run.task_type)
      throw new Error("结果与已有运行不一致");
    render(result, run);
    const provenance = node(
      "p",
      `原运行登记型号：${run.model_id || "未提供"}；登记型号不表示本任务实际调用模型。`,
      "reason archived-provenance",
    );
    provenance.dataset.runModel = run.model_id || "";
    $("summary").append(provenance);
    $("mode-badge").textContent = "团队 API · 已有运行结果";
    $("connection").textContent = "已读取归档结果，未启动新任务或调用模型";
    await loadEvents(run, epoch);
  } catch (error) {
    if (epoch === undefined || epoch === state.epoch) reportError(error);
  } finally {
    if (epoch === state.epoch) $("load-existing").disabled = false;
  }
};
$("run").onclick = async () => {
  try {
    setBase();
    reset();
    const epoch = ++state.epoch;
    const ids = [
      ...new Set($("document-ids").value.split(/\s+/).filter(Boolean)),
    ];
    if (!ids.length) throw new Error("至少提供一个 document_id");
    const taskInput = JSON.parse($("task-input").value);
    $("run").disabled = true;
    const run = await jsonRequest("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_version: "1.0.0",
        task_type: $("task-type").value,
        document_ids: ids,
        task_input: taskInput,
      }),
    });
    if (epoch !== state.epoch) return;
    $("stop").disabled = false;
    const started = Date.now();
    async function poll() {
      if (epoch !== state.epoch) return;
      try {
        const current = await jsonRequest(
          "/runs/" + encodeURIComponent(run.run_id),
        );
        if (epoch !== state.epoch) return;
        status(
          `${labels[current.status] || current.status} · ${current.run_id}`,
        );
        if (["succeeded", "needs_review"].includes(current.status)) {
          const result = await jsonRequest(
            `/runs/${encodeURIComponent(run.run_id)}/result`,
          );
          if (epoch !== state.epoch) return;
          render(result, current);
          await loadEvents(current, epoch);
          if (epoch !== state.epoch) return;
          $("stop").disabled = true;
          $("run").disabled = false;
        } else if (["failed", "cancelled"].includes(current.status)) {
          await loadEvents(current, epoch);
          if (epoch !== state.epoch) return;
          throw new Error(current.error?.message || labels[current.status]);
        } else if (Date.now() - started > 300000) {
          throw new Error("轮询已超过5分钟，请检查后端运行记录");
        } else state.poll = setTimeout(poll, 1000);
      } catch (e) {
        if (epoch === state.epoch) {
          reportError(e);
          $("stop").disabled = true;
          $("run").disabled = false;
        }
      }
    }
    await poll();
  } catch (e) {
    reportError(e);
    $("run").disabled = false;
  }
};
$("stop").onclick = () => {
  state.epoch++;
  clearTimeout(state.poll);
  $("stop").disabled = true;
  $("run").disabled = false;
  status("页面轮询已停止；后端任务仍按其状态机继续。");
};
$("export").onclick = () => {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = node("a");
  a.href = url;
  a.download = state.result.run_id + ".json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
function exportValuation(format) {
  if (!state.valuation) return;
  const raw =
    format === "html"
      ? valuationHTML(state.valuation)
      : valuationCSV(state.valuation);
  const url = URL.createObjectURL(
    new Blob([raw], {
      type:
        format === "html"
          ? "text/html;charset=utf-8"
          : "text/csv;charset=utf-8",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = `${state.mode === "sample" ? "synthetic-" : ""}${state.result.run_id}.${format}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-html").onclick = () => exportValuation("html");
$("export-csv").onclick = () => exportValuation("csv");
function exportSynthesis(format) {
  if (!state.synthesis) return;
  const raw =
    format === "html"
      ? synthesisHTML(state.synthesis)
      : synthesisCSV(state.synthesis);
  const url = URL.createObjectURL(
    new Blob([raw], {
      type:
        format === "html"
          ? "text/html;charset=utf-8"
          : "text/csv;charset=utf-8",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = `${state.mode === "sample" ? "synthetic-" : ""}${state.result.run_id}-review.${format}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-synthesis-html").onclick = () => exportSynthesis("html");
$("export-synthesis-csv").onclick = () => exportSynthesis("csv");
function exportDisclosure(format) {
  if (!state.disclosure) return;
  const raw =
    format === "html"
      ? disclosureHTML(state.disclosure)
      : disclosureCSV(state.disclosure);
  const url = URL.createObjectURL(
    new Blob([raw], {
      type:
        format === "html"
          ? "text/html;charset=utf-8"
          : "text/csv;charset=utf-8",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = `${state.mode === "sample" ? "synthetic-" : ""}${state.result.run_id}-fields.${format}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export-disclosure-html").onclick = () => exportDisclosure("html");
$("export-disclosure-csv").onclick = () => exportDisclosure("csv");
loadSample().catch(reportError);
