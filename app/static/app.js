const form = document.querySelector("#run-form");
const question = document.querySelector("#question");
const submitButton = document.querySelector("#submit-button");
const liveState = document.querySelector("#live-state");
const liveMessage = document.querySelector("#live-message");
const results = document.querySelector("#results");
const evalButton = document.querySelector("#eval-button");
const cachePolicy = document.querySelector("#cache-policy");
const executionMode = document.querySelector("#execution-mode");
const cancelJobButton = document.querySelector("#cancel-job");
const jobPanel = document.querySelector("#job-panel");
const approvalPanel = document.querySelector("#approval-panel");
const approveApprovalButton = document.querySelector("#approve-approval");
const denyApprovalButton = document.querySelector("#deny-approval");
let pendingApproval = null;
let activeJobId = null;
let activeEventSource = null;
let lastJobEventId = 0;

const backtestMetricDefinitions = [
  ["total_return_pct", "Total return", "%"],
  ["max_drawdown_pct", "Max drawdown", "%"],
  ["sharpe_ratio", "Sharpe", ""],
  ["trade_count", "Trades", ""],
  ["win_rate_pct", "Win rate", "%"],
];

const marketMetricDefinitions = [
  ["returned_count", "Bars", ""],
  ["period_high", "Period high", ""],
  ["period_low", "Period low", ""],
  ["change_pct", "Close change", "%"],
  ["average_volume", "Avg volume", ""],
];

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.textContent = value;
}

async function loadRuntime() {
  try {
    const [healthResponse, skillsResponse] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/skills"),
    ]);
    const health = await healthResponse.json();
    const skills = await skillsResponse.json();
    setText("#health-label", `API v${health.version}`);
    setText("#skill-name", skills.length ? `${skills.length} · ${skills.map((skill) => skill.name).join(" / ")}` : "none");
    setText(
      "#router-name",
      health.router_llm_configured
        ? `LLM primary · ${health.router_model}`
        : "rule fallback · LLM unconfigured",
    );
    setText("#planner-name", health.planner || "unavailable");
    setText("#interpreter-mode", health.llm_configured ? "LLM + validated fallback" : "deterministic fallback");
    setText("#data-source", health.data_source);
    setText("#search-provider", health.search_provider);
    setText(
      "#cache-runtime",
      health.artifact_cache_enabled
        ? `${health.artifact_cache_entries}/${health.artifact_cache_max_entries} active · SQLite`
        : "disabled",
    );
    setText(
      "#mcp-client",
      `${health.mcp_client_connected_servers} server · ${health.mcp_client_discovered_tools} namespaced tools`,
    );
    setText(
      "#async-runtime",
      `${health.async_job_transport} · ${health.async_job_consumer_group}`,
    );
  } catch {
    setText("#health-label", "API unavailable");
  }
}

function renderEvalReport(report) {
  const gate = document.querySelector("#eval-gate");
  const cases = document.querySelector("#eval-cases");
  gate.textContent = report.passed ? "PASS" : "FAIL";
  gate.className = `gate ${report.passed ? "pass" : "fail"}`;
  setText("#eval-score", `${report.score}`);
  setText(
    "#eval-meta",
    `${report.dataset_version} · ${report.passed_cases}/${report.total_cases} cases · ${report.duration_ms} ms`,
  );
  cases.replaceChildren();
  report.results.forEach((result) => {
    const item = document.createElement("li");
    const marker = document.createElement("span");
    marker.className = result.passed ? "case-pass" : "case-fail";
    marker.textContent = result.passed ? "PASS" : "FAIL";
    const name = document.createElement("span");
    name.textContent = result.case_id;
    const score = document.createElement("strong");
    score.textContent = result.score.toFixed(2);
    item.append(marker, name, score);
    cases.append(item);
  });
  cases.classList.remove("hidden");
}

async function loadLatestEval() {
  try {
    const response = await fetch("/api/evals/latest");
    if (response.status === 404) return;
    if (!response.ok) throw new Error("Eval API unavailable");
    renderEvalReport(await response.json());
  } catch {
    setText("#eval-meta", "无法读取最近一次评测");
  }
}

function renderMetrics(metrics, definitions) {
  const container = document.querySelector("#metrics");
  container.replaceChildren();
  if (!metrics) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  definitions.forEach(([key, label, suffix]) => {
    const card = document.createElement("div");
    card.className = "metric";
    const title = document.createElement("span");
    title.textContent = label;
    const value = document.createElement("strong");
    value.textContent = `${metrics[key]}${suffix}`;
    card.append(title, value);
    container.append(card);
  });
}

function renderChart(points) {
  const svg = document.querySelector("#equity-chart");
  const panel = document.querySelector("#chart-panel");
  svg.replaceChildren();
  if (!points || points.length < 2) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const namespace = "http://www.w3.org/2000/svg";
  const values = points.map((point) => point.equity);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || 1;
  const width = 760;
  const height = 260;
  const padding = 24;

  for (let index = 0; index < 4; index += 1) {
    const line = document.createElementNS(namespace, "line");
    const y = padding + ((height - padding * 2) * index) / 3;
    line.setAttribute("x1", padding);
    line.setAttribute("x2", width - padding);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "#25292d");
    line.setAttribute("stroke-width", "1");
    svg.append(line);
  }

  const coordinates = points.map((point, index) => {
    const x = padding + (index / (points.length - 1)) * (width - padding * 2);
    const y = height - padding - ((point.equity - minimum) / spread) * (height - padding * 2);
    return [x, y];
  });
  const area = document.createElementNS(namespace, "path");
  const areaPath = `M ${coordinates[0][0]} ${height - padding} L ${coordinates.map(([x, y]) => `${x} ${y}`).join(" L ")} L ${coordinates.at(-1)[0]} ${height - padding} Z`;
  area.setAttribute("d", areaPath);
  area.setAttribute("fill", "rgba(214,169,74,0.09)");
  svg.append(area);

  const path = document.createElementNS(namespace, "polyline");
  path.setAttribute("points", coordinates.map(([x, y]) => `${x},${y}`).join(" "));
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#d6a94a");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(path);
}

function renderSources(sources) {
  const list = document.querySelector("#source-list");
  list.replaceChildren();
  if (!sources || sources.length === 0) {
    list.classList.add("hidden");
    return;
  }
  sources.forEach((source) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.title;
    const snippet = document.createElement("p");
    snippet.textContent = source.snippet;
    item.append(link, snippet);
    list.append(item);
  });
  list.classList.remove("hidden");
}

function renderTrace(events) {
  const trace = document.querySelector("#trace");
  trace.replaceChildren();
  events.forEach((event) => {
    const item = document.createElement("li");
    const index = document.createElement("span");
    index.className = "trace-index";
    index.textContent = String(event.sequence).padStart(2, "0");
    const stage = document.createElement("span");
    stage.className = "trace-stage";
    stage.textContent = event.stage;
    const message = document.createElement("span");
    message.className = "trace-message";
    message.textContent = event.message;
    const duration = document.createElement("span");
    duration.className = "trace-duration";
    duration.textContent = `${event.duration_ms} ms`;
    item.append(index, stage, message, duration);
    trace.append(item);
  });
}

function renderAudit(entries) {
  const audit = document.querySelector("#tool-audit");
  audit.replaceChildren();
  if (!entries || entries.length === 0) {
    const empty = document.createElement("li");
    empty.className = "audit-empty";
    empty.textContent = "本次 Run 未调用 Tool，或在 Tool 前被安全拒绝。";
    audit.append(empty);
    return;
  }
  entries.forEach((entry, index) => {
    const item = document.createElement("li");
    const sequence = document.createElement("span");
    sequence.className = "audit-index";
    sequence.textContent = String(index + 1).padStart(2, "0");
    const phase = document.createElement("strong");
    phase.textContent = entry.phase;
    const tool = document.createElement("code");
    tool.textContent = entry.tool;
    const outcome = document.createElement("span");
    outcome.className = `audit-outcome ${entry.decision || entry.outcome || "neutral"}`;
    outcome.textContent = entry.decision || entry.outcome || "observed";
    const details = document.createElement("small");
    details.textContent = [
      entry.effect && `effect ${entry.effect}`,
      entry.risk && `risk ${entry.risk}`,
      entry.reason && `reason ${entry.reason}`,
      entry.approval_id && `approval ${entry.approval_id}`,
      entry.call && `call ${entry.call}`,
    ].filter(Boolean).join(" · ");
    item.append(sequence, phase, tool, outcome, details);
    audit.append(item);
  });
}

function renderPlan(plan) {
  const list = document.querySelector("#execution-plan");
  list.replaceChildren();
  if (!plan) {
    setText("#plan-meta", "请求在生成执行计划前结束");
    return;
  }
  setText(
    "#plan-meta",
    `${plan.planner} · ${plan.skill}@${plan.skill_version} · plan ${plan.plan_id} · tools ${plan.planned_tool_calls}/${plan.max_tool_calls}`,
  );
  const completed = new Set(plan.completed_steps || []);
  const skipped = new Set(plan.skipped_steps || []);
  plan.steps.forEach((step) => {
    const item = document.createElement("li");
    const state = document.createElement("span");
    const status = completed.has(step.step_id)
      ? "completed"
      : step.step_id === plan.failed_step
        ? "failed"
        : step.step_id === plan.rejected_step
          ? "rejected"
          : step.step_id === plan.paused_step
            ? "waiting"
            : skipped.has(step.step_id)
              ? "skipped"
              : "planned";
    state.className = `plan-state ${status}`;
    state.textContent = status.toUpperCase();
    const name = document.createElement("strong");
    name.textContent = step.step_id;
    const binding = document.createElement("span");
    binding.textContent = step.tool_name ? `Tool · ${step.tool_name}` : "Control step";
    item.append(state, name, binding);
    list.append(item);
  });
}

function renderApproval(run, jobId = null) {
  if (run.status !== "pending_approval" || !run.approval) {
    pendingApproval = null;
    approvalPanel.classList.add("hidden");
    return;
  }
  pendingApproval = {
    runId: run.run_id,
    approvalId: run.approval.approval_id,
    jobId,
  };
  setText("#approval-tool", run.approval.tool_name);
  setText("#approval-risk", `${run.approval.effect} / ${run.approval.risk}`);
  setText("#approval-expiry", new Date(run.approval.expires_at).toLocaleString());
  setText("#approval-digest", run.approval.arguments_digest);
  setText("#approval-reason", run.approval.reason);
  approvalPanel.classList.remove("hidden");
}

async function readResponse(response, fallbackMessage) {
  const payload = await response.json();
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : fallbackMessage;
    throw new Error(detail);
  }
  return payload;
}

function renderWarnings(warnings) {
  const box = document.querySelector("#warning-box");
  if (!warnings || warnings.length === 0) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  box.textContent = warnings.join(" · ");
  box.classList.remove("hidden");
}

function renderCache(cache, status) {
  const box = document.querySelector("#cache-box");
  if (!cache) {
    box.classList.add("hidden");
    box.textContent = "";
    return;
  }
  const parts = [status.toUpperCase()];
  if (cache.fingerprint) parts.push(`fingerprint ${cache.fingerprint}`);
  if (cache.source_run_id) parts.push(`source ${cache.source_run_id}`);
  if (cache.similarity_score !== null && cache.similarity_score !== undefined) {
    parts.push(`similarity ${cache.similarity_score}`);
  }
  if (cache.saved_tool_calls) parts.push(`saved ${cache.saved_tool_calls} tool calls`);
  if (cache.saved_latency_ms) parts.push(`saved ${cache.saved_latency_ms} ms tool time`);
  if (cache.reason) parts.push(cache.reason);
  box.textContent = parts.join(" · ");
  box.classList.remove("hidden");
}

function renderResult(run, jobId = null) {
  results.classList.remove("hidden");
  const intent = run.route ? run.route.intent : "legacy";
  setText("#run-id", `${run.status.toUpperCase()} · ${intent} · ${run.run_id}`);
  setText(
    "#route-decision",
    run.route
      ? `${run.route.router} · ${run.route.skill} · confidence ${run.route.confidence}`
        + `${run.route.fallback_reason ? ` · fallback ${run.route.fallback_reason}` : ""}`
        + ` · ${run.route.reason}`
      : "legacy run",
  );
  setText("#summary", run.summary);
  const artifact = run.strategy || run.market_query || run.research_spec || run.route;
  setText("#strategy-spec", JSON.stringify(artifact, null, 2));
  renderWarnings(run.warnings);
  renderCache(run.cache, run.cache_status);
  renderApproval(run, jobId);
  renderPlan(run.plan);
  renderTrace(run.events || []);
  renderAudit(run.tool_audit || []);
  if (run.metrics) renderMetrics(run.metrics, backtestMetricDefinitions);
  else if (run.market_result) renderMetrics(run.market_result, marketMetricDefinitions);
  else renderMetrics(null, []);
  renderChart(run.equity_curve);
  renderSources(run.research_result ? run.research_result.sources : []);
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

approveApprovalButton.addEventListener("click", async () => {
  const approval = pendingApproval;
  if (!approval) return;
  approveApprovalButton.disabled = true;
  denyApprovalButton.disabled = true;
  liveState.classList.add("running");
  liveMessage.textContent = "正在签发一次性审批凭证并恢复受控 Tool…";
  try {
    const grant = await readResponse(
      await fetch(
        `/api/runs/${approval.runId}/approvals/${approval.approvalId}/approve`,
        {
          method: "POST",
          headers: { "X-AurumLab-Approval-Intent": "approve" },
        },
      ),
      "审批失败",
    );
    const resumePath = approval.jobId
      ? `/api/jobs/${approval.jobId}/resume`
      : `/api/runs/${approval.runId}/resume`;
    const resumed = await readResponse(
      await fetch(resumePath, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_token: grant.approval_token }),
      }),
      "恢复执行失败",
    );
    if (approval.jobId) {
      liveMessage.textContent = "审批已消费，Job 已重新入队";
      await watchJob(approval.jobId, false);
    } else {
      renderResult(resumed);
      liveMessage.textContent = "审批凭证已原子消费，受控 Tool 执行完成";
    }
  } catch (error) {
    liveMessage.textContent = `审批失败：${error.message}`;
  } finally {
    liveState.classList.remove("running");
    approveApprovalButton.disabled = false;
    denyApprovalButton.disabled = false;
  }
});

denyApprovalButton.addEventListener("click", async () => {
  const approval = pendingApproval;
  if (!approval) return;
  approveApprovalButton.disabled = true;
  denyApprovalButton.disabled = true;
  liveState.classList.add("running");
  liveMessage.textContent = "正在拒绝受控 Tool 调用…";
  try {
    const denied = await readResponse(
      await fetch(
        `/api/runs/${approval.runId}/approvals/${approval.approvalId}/deny`,
        {
          method: "POST",
          headers: { "X-AurumLab-Approval-Intent": "deny" },
        },
      ),
      "拒绝审批失败",
    );
    if (approval.jobId) {
      await fetch(`/api/jobs/${approval.jobId}`, { method: "DELETE" });
    }
    renderResult(denied);
    liveMessage.textContent = "审批已拒绝，受控 Tool 未执行";
  } catch (error) {
    liveMessage.textContent = `拒绝失败：${error.message}`;
  } finally {
    liveState.classList.remove("running");
    approveApprovalButton.disabled = false;
    denyApprovalButton.disabled = false;
  }
});

const jobEventTypes = [
  "job_queued",
  "job_started",
  "step_completed",
  "approval_required",
  "approval_resumed",
  "retry_scheduled",
  "cancel_requested",
  "job_completed",
  "job_failed",
  "job_cancelled",
  "job_timed_out",
  "dead_lettered",
  "checkpoint_restored",
];

function appendJobEvent(event) {
  const list = document.querySelector("#job-events");
  const item = document.createElement("li");
  const id = document.createElement("span");
  id.className = "job-event-id";
  id.textContent = `#${event.event_id}`;
  const type = document.createElement("span");
  type.className = "job-event-type";
  type.textContent = event.event_type;
  const message = document.createElement("span");
  message.className = "job-event-message";
  message.textContent = event.message;
  item.append(id, type, message);
  list.append(item);
  lastJobEventId = Math.max(lastJobEventId, event.event_id);
  setText("#job-state", event.status.toUpperCase());
  liveMessage.textContent = event.message;
}

async function loadJobResult(jobId) {
  const job = await readResponse(await fetch(`/api/jobs/${jobId}`), "读取 Job 失败");
  if (job.run_id) {
    const run = await readResponse(await fetch(`/api/runs/${job.run_id}`), "读取 Run 失败");
    renderResult(run, job.status === "waiting_approval" ? jobId : null);
  }
  return job;
}

function watchJob(jobId, resetEvents = true) {
  if (activeEventSource) activeEventSource.close();
  activeJobId = jobId;
  jobPanel.classList.remove("hidden");
  cancelJobButton.classList.remove("hidden");
  setText("#job-title", `任务事件流 · ${jobId}`);
  if (resetEvents) document.querySelector("#job-events").replaceChildren();
  if (resetEvents) lastJobEventId = 0;
  return new Promise((resolve, reject) => {
    const source = new EventSource(`/api/jobs/${jobId}/stream?after=${lastJobEventId}`);
    activeEventSource = source;
    jobEventTypes.forEach((eventType) => {
      source.addEventListener(eventType, async (message) => {
        try {
          const event = JSON.parse(message.data);
          appendJobEvent(event);
          if (event.event_type === "approval_required") {
            source.close();
            cancelJobButton.classList.add("hidden");
            await loadJobResult(jobId);
            resolve();
          } else if (["job_completed", "job_failed", "job_cancelled", "job_timed_out", "dead_lettered"].includes(event.event_type)) {
            source.close();
            cancelJobButton.classList.add("hidden");
            await loadJobResult(jobId);
            resolve();
          }
        } catch (error) {
          source.close();
          reject(error);
        }
      });
    });
  });
}

cancelJobButton.addEventListener("click", async () => {
  if (!activeJobId) return;
  cancelJobButton.disabled = true;
  try {
    const job = await readResponse(
      await fetch(`/api/jobs/${activeJobId}`, { method: "DELETE" }),
      "取消 Job 失败",
    );
    liveMessage.textContent = `取消状态：${job.status}`;
  } catch (error) {
    liveMessage.textContent = `取消失败：${error.message}`;
  } finally {
    cancelJobButton.disabled = false;
  }
});

document.querySelectorAll(".example").forEach((button) => {
  button.addEventListener("click", () => {
    question.value = button.dataset.query;
    question.focus();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const statusMessages = [
    "正在执行安全预检与 LLM 意图路由…",
    "正在生成并校验 Bounded Plan…",
    "正在加载版本化 Skill 与 Tool Policy…",
    "正在通过 Tool Gateway 执行任务…",
    "正在生成结构化反馈与审计轨迹…",
  ];
  let messageIndex = 0;
  liveState.classList.add("running");
  liveMessage.textContent = statusMessages[messageIndex];
  submitButton.disabled = true;
  let timer = window.setInterval(() => {
    messageIndex = Math.min(messageIndex + 1, statusMessages.length - 1);
    liveMessage.textContent = statusMessages[messageIndex];
  }, 450);

  try {
    if (executionMode.value === "async") {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() || `web-${Date.now()}`;
      const job = await readResponse(
        await fetch("/api/jobs", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify({
            question: question.value,
            cache_policy: cachePolicy.value,
          }),
        }),
        "异步 Job 创建失败",
      );
      window.clearInterval(timer);
      timer = null;
      liveMessage.textContent = `Job ${job.job_id} 已排队，等待 Worker…`;
      await watchJob(job.job_id);
      return;
    }
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question.value, cache_policy: cachePolicy.value }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : "请求未通过校验";
      throw new Error(detail);
    }
    renderResult(payload);
    liveMessage.textContent = payload.status === "completed"
      ? payload.cache_status === "exact_hit"
        ? "命中 Artifact Memory，已跳过领域 Tool 链"
        : "任务完成，结构化反馈已生成并可复用"
      : payload.summary;
  } catch (error) {
    liveMessage.textContent = `请求失败：${error.message}`;
  } finally {
    if (timer) window.clearInterval(timer);
    liveState.classList.remove("running");
    submitButton.disabled = false;
  }
});

evalButton.addEventListener("click", async () => {
  evalButton.disabled = true;
  setText("#eval-gate", "RUNNING");
  setText("#eval-meta", "正在执行真实 Agent Golden Set…");
  try {
    const response = await fetch("/api/evals/run", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error("评测执行失败");
    renderEvalReport(payload);
  } catch (error) {
    const gate = document.querySelector("#eval-gate");
    gate.textContent = "ERROR";
    gate.className = "gate fail";
    setText("#eval-meta", error.message);
  } finally {
    evalButton.disabled = false;
  }
});

loadRuntime();
loadLatestEval();
