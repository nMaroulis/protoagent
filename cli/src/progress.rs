use serde_json::Value;
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;

const MAX_VISIBLE_EVENTS: usize = 8;

pub(crate) struct ProgressFile {
    path: PathBuf,
    seen_lines: usize,
    seen_approvals: HashSet<String>,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ContextUsage {
    latest: Option<ContextSample>,
    peak: Option<ContextSample>,
    observations: Vec<ContextSample>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ContextSample {
    pub(crate) used_tokens: u64,
    pub(crate) window_tokens: Option<u64>,
    pub(crate) used_percent: Option<f64>,
    pub(crate) estimated: bool,
    pub(crate) agent_name: String,
    pub(crate) model: String,
    task_id: String,
    step: Option<u64>,
    finalized: bool,
}

#[derive(Default)]
pub(crate) struct ProgressBatch {
    pub(crate) events: Vec<String>,
    pub(crate) context_samples: Vec<ContextSample>,
}

#[derive(Clone, Debug)]
pub(crate) struct RuntimeApproval {
    pub(crate) request_id: String,
    pub(crate) run_id: String,
    pub(crate) action_name: String,
    pub(crate) description: String,
    pub(crate) target: String,
    pub(crate) diff: String,
    request: Value,
}

impl ProgressFile {
    pub(crate) fn new(token: impl std::fmt::Display) -> Self {
        let path = std::env::temp_dir().join(format!(
            "protoagent-progress-{}-{token}.jsonl",
            std::process::id()
        ));
        let approval_request_path = control_path(&path, "approval-request");
        let approval_decision_path = control_path(&path, "approval-decision");
        let cancel_path = control_path(&path, "cancel");
        for candidate in [&path, &approval_request_path, &approval_decision_path, &cancel_path] {
            let _ = fs::remove_file(candidate);
        }
        Self {
            path,
            seen_lines: 0,
            seen_approvals: HashSet::new(),
        }
    }

    pub(crate) fn path_string(&self) -> String {
        self.path.to_string_lossy().to_string()
    }

    pub(crate) fn read_new(&mut self) -> Vec<String> {
        self.read_new_batch().events
    }

    pub(crate) fn read_new_batch(&mut self) -> ProgressBatch {
        let Ok(text) = fs::read_to_string(&self.path) else {
            return ProgressBatch::default();
        };

        let mut batch = ProgressBatch::default();
        for line in text.lines().skip(self.seen_lines) {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                self.seen_lines += 1;
                continue;
            }

            let Ok(value) = serde_json::from_str::<Value>(trimmed) else {
                break;
            };
            let run_event = value.get("run_event");
            if let Some(sample) = run_event.and_then(context_sample_from_run_event) {
                batch.context_samples.push(sample);
            }
            let event = if run_event.map(is_hidden_run_event).unwrap_or(false) {
                None
            } else {
                run_event
                    .and_then(run_event_summary)
                    .or_else(|| value.get("event").and_then(Value::as_str).map(str::to_string))
            };
            if let Some(event) = event
                .map(|event| event.trim().to_string())
                .filter(|event| !event.is_empty())
            {
                batch.events.push(event);
            }
            self.seen_lines += 1;
        }
        batch
    }

    pub(crate) fn take_approval_request(&mut self) -> Option<RuntimeApproval> {
        let text = fs::read_to_string(self.approval_request_path()).ok()?;
        let request = serde_json::from_str::<Value>(&text).ok()?;
        let approval = RuntimeApproval::from_value(request)?;
        if !self.seen_approvals.insert(approval.request_id.clone()) {
            return None;
        }
        Some(approval)
    }

    pub(crate) fn decide(&self, approval: &RuntimeApproval, approved: bool) -> std::io::Result<()> {
        let decision = serde_json::json!({
            "approved": approved,
            "request_id": approval.request_id,
            "reason": if approved { "Approved in ProtoAgent" } else { "Denied in ProtoAgent" },
            "decided_by": "protoagent-user",
            "metadata": {"interface": "rust-cli"}
        });
        write_json_atomic(&self.approval_decision_path(), &decision)
    }

    pub(crate) fn request_cancel(&self, reason: &str) -> std::io::Result<()> {
        write_json_atomic(
            &self.cancel_path(),
            &serde_json::json!({"reason": reason, "source": "rust-tui"}),
        )
    }

    pub(crate) fn cleanup(&self) {
        for candidate in [
            self.path.clone(),
            self.approval_request_path(),
            self.approval_decision_path(),
            self.cancel_path(),
        ] {
            let _ = fs::remove_file(candidate);
        }
    }

    fn approval_request_path(&self) -> PathBuf {
        control_path(&self.path, "approval-request")
    }

    fn approval_decision_path(&self) -> PathBuf {
        control_path(&self.path, "approval-decision")
    }

    fn cancel_path(&self) -> PathBuf {
        control_path(&self.path, "cancel")
    }
}

impl ContextUsage {
    pub(crate) fn reset(&mut self) {
        self.latest = None;
        self.peak = None;
        self.observations.clear();
    }

    pub(crate) fn observe(&mut self, sample: ContextSample) {
        let existing = self
            .observations
            .iter()
            .rposition(|current| samples_are_same_call(&sample, current));
        match existing {
            Some(index) if sample.finalized || !self.observations[index].finalized => {
                self.observations[index] = sample;
            }
            Some(_) => {}
            None => self.observations.push(sample),
        }
        self.latest = self.observations.last().cloned();
        self.peak = self.observations.iter().cloned().reduce(|peak, candidate| {
            if sample_has_more_pressure(&candidate, &peak) {
                candidate
            } else {
                peak
            }
        });
    }

    pub(crate) fn observe_run_events(&mut self, events: &[Value]) {
        for event in events {
            if let Some(sample) = context_sample_from_run_event(event) {
                self.observe(sample);
            }
        }
    }

    pub(crate) fn latest(&self) -> Option<&ContextSample> {
        self.latest.as_ref()
    }

    pub(crate) fn peak(&self) -> Option<&ContextSample> {
        self.peak.as_ref()
    }
}

impl RuntimeApproval {
    fn from_value(request: Value) -> Option<Self> {
        let request_id = value_string(&request, &["request_id"]);
        if request_id.is_empty() {
            return None;
        }
        let action = request.get("action")?;
        let payload = action.get("payload").unwrap_or(&Value::Null);
        let arguments = payload.get("arguments").unwrap_or(&Value::Null);
        let metadata = action.get("metadata").unwrap_or(&Value::Null);
        let target = value_string(metadata, &["path"])
            .if_empty_then(|| value_string(arguments, &["path"]));
        let diff = action
            .get("artifacts")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter(|artifact| {
                artifact.get("media_type").and_then(Value::as_str) == Some("text/x-diff")
            })
            .flat_map(|artifact| {
                artifact
                    .get("parts")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .filter_map(|part| part.get("content").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join("\n");
        Some(Self {
            request_id,
            run_id: value_string(&request, &["run_id"]),
            action_name: value_string(action, &["name"]),
            description: value_string(action, &["description"]),
            target,
            diff,
            request,
        })
    }

    pub(crate) fn capabilities(&self) -> String {
        self.request
            .get("action")
            .and_then(|action| action.get("capabilities"))
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default()
    }
}

trait EmptyString {
    fn if_empty_then(self, fallback: impl FnOnce() -> String) -> String;
}

impl EmptyString for String {
    fn if_empty_then(self, fallback: impl FnOnce() -> String) -> String {
        if self.is_empty() { fallback() } else { self }
    }
}

fn control_path(path: &PathBuf, suffix: &str) -> PathBuf {
    PathBuf::from(format!("{}.{suffix}.json", path.to_string_lossy()))
}

fn write_json_atomic(path: &PathBuf, value: &Value) -> std::io::Result<()> {
    let temporary = PathBuf::from(format!(
        "{}.{}.tmp",
        path.to_string_lossy(),
        std::process::id()
    ));
    fs::write(&temporary, serde_json::to_vec(value)?)?;
    fs::rename(temporary, path)
}

fn run_event_summary(value: &Value) -> Option<String> {
    let payload = value.get("payload").unwrap_or(&Value::Null);
    let llm_type = value_string(payload, &["llm_event_type"]);
    if llm_type == "llm_chunk" {
        return None;
    }
    let event_type = value_string(value, &["type"]);
    let agent = run_event_agent(value);
    let metadata = payload.get("metadata").unwrap_or(&Value::Null);

    match (event_type.as_str(), llm_type.as_str()) {
        ("action.started", "agent_call_start") => {
            let target = display_agent(&value_string(metadata, &["agent"]));
            let action = value_string(metadata, &["action"]);
            if !target.is_empty() {
                let detail = if action.is_empty() {
                    String::new()
                } else {
                    format!(" ({action})")
                };
                return Some(format!("{agent}: delegating to {target}{detail}."));
            }
        }
        ("action.completed", "agent_call_result") => {
            let target = display_agent(&value_string(metadata, &["agent"]));
            if !target.is_empty() {
                return Some(format!("{agent}: delegation from {target} returned."));
            }
        }
        ("action.started", "tool_start") => {
            let tool = value_string(metadata, &["tool"]);
            if !tool.is_empty() {
                return Some(format!("{agent}: calling tool {tool}."));
            }
        }
        ("action.completed", "tool_result") => {
            let tool = value_string(metadata, &["tool"]);
            if !tool.is_empty() {
                return Some(format!("{agent}: tool {tool} returned."));
            }
        }
        ("action.failed", "tool_error") => {
            let tool = value_string(metadata, &["tool"]);
            if !tool.is_empty() {
                return Some(format!("{agent}: tool {tool} failed."));
            }
        }
        ("llm.stream", "llm_step") => {
            return Some(format!(
                "{agent} step {}: LLM step started.",
                payload.get("step").and_then(Value::as_u64).unwrap_or(0)
            ));
        }
        ("llm.stream", "llm_response") => return Some(format!("{agent}: LLM response.")),
        ("llm.stream", "llm_action") => {
            let action = value_string(payload, &["action"]);
            if !action.is_empty() {
                return Some(format!("{agent}: selected action {action}."));
            }
        }
        ("llm.stream", "llm_final") => return Some(format!("{agent}: final response produced.")),
        ("approval.required", _) => {
            let action = value_string(payload, &["request", "action", "name"]);
            return Some(if action.is_empty() {
                format!("{agent}: Approval required.")
            } else {
                format!("{agent}: Approval required for {action}.")
            });
        }
        ("approval.decided", _) => {
            let approved = payload
                .get("decision")
                .and_then(|decision| decision.get("approved"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            return Some(format!(
                "{agent}: Approval {}.",
                if approved { "approved" } else { "denied" }
            ));
        }
        ("action.policy", _) => {
            let effect = value_string(payload, &["decision", "effect"]);
            if !effect.is_empty() {
                return Some(format!("{agent}: Policy decision: {effect}."));
            }
        }
        _ => {}
    }

    let summary = value.get("summary").and_then(Value::as_str)?.trim();
    if summary.is_empty() {
        return value.get("type").and_then(Value::as_str).map(str::to_string);
    }
    let agent = value.get("agent_name").and_then(Value::as_str).unwrap_or("").trim();
    if agent.is_empty() || summary.to_ascii_lowercase().starts_with(&agent.to_ascii_lowercase()) {
        Some(summary.to_string())
    } else {
        Some(format!("{agent}: {summary}"))
    }
}

fn is_hidden_run_event(value: &Value) -> bool {
    is_context_metric_event(value)
        || matches!(
            value
                .get("payload")
                .and_then(|payload| payload.get("llm_event_type"))
                .and_then(Value::as_str),
            Some("llm_chunk")
        )
}

fn is_context_metric_event(value: &Value) -> bool {
    value_string(value, &["type"]) == "context.prepared"
        || matches!(
            value
                .get("payload")
                .and_then(|payload| payload.get("llm_event_type"))
                .and_then(Value::as_str),
            Some("llm_context" | "llm_call_metrics")
        )
}

fn run_event_agent(value: &Value) -> String {
    let payload = value.get("payload").unwrap_or(&Value::Null);
    display_agent(
        &value_string(value, &["agent_name"])
            .if_empty_then(|| value_string(payload, &["agent_name"]))
            .if_empty_then(|| "ProtoLink".to_string()),
    )
}

fn context_sample_from_run_event(value: &Value) -> Option<ContextSample> {
    let payload = value.get("payload")?;
    if value_string(value, &["type"]) == "context.prepared" {
        let manifest = payload.get("manifest")?;
        let used_tokens = manifest.get("total_estimated_tokens")?.as_u64()?;
        let window_tokens = manifest.get("context_window").and_then(Value::as_u64);
        let used_percent = window_tokens
            .filter(|window| *window > 0)
            .map(|window| used_tokens as f64 * 100.0 / window as f64);
        return Some(ContextSample {
            used_tokens,
            window_tokens,
            used_percent,
            estimated: manifest.get("estimated").and_then(Value::as_bool).unwrap_or(true),
            agent_name: value_string(value, &["agent_name"])
                .if_empty_then(|| value_string(manifest, &["agent_name"])),
            model: value_string(manifest, &["model"]),
            task_id: value_string(value, &["task_id"]),
            step: value.get("step").and_then(Value::as_u64),
            finalized: false,
        });
    }

    let event_type = payload.get("llm_event_type").and_then(Value::as_str)?;
    if !matches!(event_type, "llm_context" | "llm_call_metrics") {
        return None;
    }
    let metadata = payload.get("metadata")?;
    let context = metadata.get("context")?;
    let used_tokens = context.get("used_tokens")?.as_u64()?;
    Some(ContextSample {
        used_tokens,
        window_tokens: context.get("window_tokens").and_then(Value::as_u64),
        used_percent: context.get("used_percent").and_then(Value::as_f64),
        estimated: context.get("estimated").and_then(Value::as_bool).unwrap_or(true),
        agent_name: value_string(payload, &["agent_name"]),
        model: value_string(metadata, &["model"]),
        task_id: value_string(payload, &["task_id"]),
        step: payload.get("step").and_then(Value::as_u64),
        finalized: event_type == "llm_call_metrics",
    })
}

fn samples_are_same_call(left: &ContextSample, right: &ContextSample) -> bool {
    !left.task_id.is_empty()
        && left.task_id == right.task_id
        && left.agent_name == right.agent_name
        && left.step == right.step
}

fn sample_has_more_pressure(candidate: &ContextSample, current: &ContextSample) -> bool {
    match (candidate.used_percent, current.used_percent) {
        (Some(candidate), Some(current)) => candidate > current,
        (Some(_), None) => true,
        (None, Some(_)) => false,
        (None, None) => candidate.used_tokens > current.used_tokens,
    }
}

fn value_string(value: &Value, path: &[&str]) -> String {
    let mut current = value;
    for key in path {
        current = current.get(*key).unwrap_or(&Value::Null);
    }
    current.as_str().unwrap_or("").to_string()
}

pub(crate) fn format_live_progress(events: &[String]) -> String {
    let mut rows = vec!["Live ProtoLink trace".to_string()];
    if events.is_empty() {
        rows.push("[START] Starting local agent runtime.".to_string());
        rows.push("[WAIT] Waiting for Architect to publish the first task event.".to_string());
        return rows.join("\n");
    }

    let start = events.len().saturating_sub(MAX_VISIBLE_EVENTS);
    for event in &events[start..] {
        rows.push(format!("{} {event}", event_badge(event)));
    }
    rows.join("\n")
}

pub(crate) fn progress_activity(events: &[String], tick: usize) -> String {
    let spinner = ["|", "/", "-", "\\"];
    format!("{} {}", spinner[tick % spinner.len()], activity_summary(events).line())
}

pub(crate) fn latest_progress_message(events: &[String]) -> String {
    clip_activity(&activity_summary(events).line())
}

struct ActivitySummary {
    route: String,
    active: String,
    action: String,
}

impl ActivitySummary {
    fn line(&self) -> String {
        format!("[{}] [{}] {}", self.route, self.active, self.action)
    }
}

fn activity_summary(events: &[String]) -> ActivitySummary {
    let Some(latest) = events.last().map(String::as_str) else {
        return ActivitySummary {
            route: "Startup".to_string(),
            active: "Runtime".to_string(),
            action: "starting local runtime".to_string(),
        };
    };

    let route = route_from_event(latest)
        .or_else(|| events.iter().rev().find_map(|event| route_from_event(event)))
        .unwrap_or_else(|| "Runtime".to_string());
    let active = active_agent(latest)
        .or_else(|| route.split(" -> ").last().map(str::to_string))
        .unwrap_or_else(|| "Runtime".to_string());
    let action = action_label(latest, &active);

    ActivitySummary {
        route,
        active,
        action,
    }
}

fn route_from_event(event: &str) -> Option<String> {
    if event.starts_with("AgentClient opened a streaming task channel to Architect")
        || event.starts_with("AgentClient sent the user task to Architect")
    {
        return Some("CLI -> Architect".to_string());
    }

    if let Some(agent) = agent_prefix(event) {
        if let Some(target) = text_after(event, ": delegating to ") {
            let target = target
                .split(" (")
                .next()
                .unwrap_or(target)
                .trim_end_matches('.')
                .trim();
            if !target.is_empty() {
                return Some(format!("{agent} -> {}", display_agent(target)));
            }
        }
        if let Some(source) = text_after(event, ": delegation from ") {
            let source = source
                .split(" returned")
                .next()
                .unwrap_or(source)
                .trim_end_matches('.')
                .trim();
            if !source.is_empty() {
                return Some(format!("{} -> {agent}", display_agent(source)));
            }
        }
    }

    None
}

fn active_agent(event: &str) -> Option<String> {
    if let Some(target) = text_after(event, ": delegating to ") {
        return Some(clean_target(target));
    }
    if event.contains(" to Architect") || event.starts_with("Architect ") {
        return Some("Architect".to_string());
    }
    if event.starts_with("Explorer ") {
        return Some("Explorer".to_string());
    }
    if event.starts_with("Coder ") {
        return Some("Coder".to_string());
    }
    if event.starts_with("Registry") {
        return Some("Registry".to_string());
    }
    if event.starts_with("CLI ") || event.starts_with("Resolving tagged") {
        return Some("CLI".to_string());
    }
    if event.starts_with("Loaded tagged") || event.starts_with("Tagged context") {
        return Some("Explorer".to_string());
    }
    agent_prefix(event)
}

fn action_label(event: &str, active: &str) -> String {
    if text_after(event, ": delegating to ").is_some() {
        return "received delegated task".to_string();
    }
    if let Some(source) = text_after(event, ": delegation from ") {
        return format!("reviewing {} result", clean_return_source(source));
    }
    if let Some(tool) = text_after(event, ": calling tool ") {
        return format!("using {}", clean_sentence_tail(tool));
    }
    if let Some(tool) = text_after(event, ": tool ") {
        if event.contains(" returned") {
            return format!("received {} result", clean_return_source(tool));
        }
    }
    if let Some(action) = text_after(event, ": selected action ") {
        return format!("selected {}", clean_sentence_tail(action));
    }
    if event.contains(": LLM step started") {
        return agent_work_label(active).to_string();
    }
    if event.contains(": LLM response") {
        return "reading model response".to_string();
    }
    if event.contains(": final response produced") || event.starts_with("Architect returned") {
        return "finalizing answer".to_string();
    }
    if event.starts_with("Architect is processing") {
        return "planning".to_string();
    }
    if event.starts_with("AgentClient opened") || event.starts_with("AgentClient sent") {
        return "received user task".to_string();
    }
    if event.starts_with("Task state:") {
        return event
            .strip_prefix("Task state:")
            .map(clean_sentence_tail)
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| "task state changed".to_string());
    }
    if event.starts_with("Progress:") {
        return event
            .strip_prefix("Progress:")
            .map(clean_sentence_tail)
            .filter(|text| !text.is_empty())
            .unwrap_or_else(|| "progress update".to_string());
    }
    if event.starts_with("Architect discovery") {
        return "discovering agents".to_string();
    }
    if event.contains("registered at") {
        return "joining runtime".to_string();
    }
    if event.starts_with("Registry") {
        return "starting registry".to_string();
    }
    if event.starts_with("Resolving tagged") || event.starts_with("Loaded tagged") {
        return "loading tagged context".to_string();
    }
    if event.contains("Approval required") || event.starts_with("Approval ") {
        return "preparing approval".to_string();
    }
    if event.contains("failed") || event.contains("error") {
        return "handling error".to_string();
    }
    clean_sentence_tail(event)
}

fn event_badge(event: &str) -> &'static str {
    if event.contains("failed") || event.contains("error") || event.starts_with("Task error") {
        "[ERROR]"
    } else if event.contains("delegating to") || event.contains("AgentClient") {
        "[SEND]"
    } else if event.contains("calling tool") || event.contains(": tool ") {
        "[TOOL]"
    } else if event.starts_with("Task state") || event.starts_with("Progress") {
        "[TASK]"
    } else if event.starts_with("Architect") || agent_prefix(event).as_deref() == Some("Architect") {
        "[ARCH]"
    } else if event.starts_with("Explorer") || agent_prefix(event).as_deref() == Some("Explorer") {
        "[EXPL]"
    } else if event.starts_with("Coder") || agent_prefix(event).as_deref() == Some("Coder") {
        "[CODE]"
    } else if event.starts_with("Registry") {
        "[REG]"
    } else if event.starts_with("Loaded tagged") || event.starts_with("Tagged context") {
        "[TAG]"
    } else if event.contains("model") || event.contains("LLM") {
        "[MODEL]"
    } else {
        "[INFO]"
    }
}

fn agent_prefix(event: &str) -> Option<String> {
    let head = event.split(':').next()?.trim();
    let agent = head.split(" step ").next().unwrap_or(head).trim();
    match agent.to_ascii_lowercase().as_str() {
        "architect" => Some("Architect".to_string()),
        "explorer" => Some("Explorer".to_string()),
        "coder" => Some("Coder".to_string()),
        "registry" => Some("Registry".to_string()),
        "agent" => Some("Agent".to_string()),
        _ => None,
    }
}

fn display_agent(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "architect" => "Architect".to_string(),
        "explorer" => "Explorer".to_string(),
        "coder" => "Coder".to_string(),
        "agent" => "Agent".to_string(),
        other if other.is_empty() => "Agent".to_string(),
        _ => {
            let mut chars = value.trim().chars();
            match chars.next() {
                Some(first) => format!("{}{}", first.to_uppercase(), chars.as_str()),
                None => "Agent".to_string(),
            }
        }
    }
}

fn agent_work_label(agent: &str) -> &'static str {
    match agent {
        "Architect" => "planning",
        "Explorer" => "mapping workspace",
        "Coder" => "coding",
        _ => "processing",
    }
}

fn text_after<'a>(value: &'a str, marker: &str) -> Option<&'a str> {
    value.split_once(marker).map(|(_, tail)| tail)
}

fn clean_target(value: &str) -> String {
    let target = value
        .split(" (")
        .next()
        .unwrap_or(value)
        .trim_end_matches('.')
        .trim();
    display_agent(target)
}

fn clean_return_source(value: &str) -> String {
    value
        .split(" returned")
        .next()
        .unwrap_or(value)
        .trim_end_matches('.')
        .trim()
        .to_string()
}

fn clean_sentence_tail(value: &str) -> String {
    value.trim().trim_end_matches('.').trim().to_string()
}

fn clip_activity(value: &str) -> String {
    const LIMIT: usize = 54;
    if value.chars().count() <= LIMIT {
        return value.to_string();
    }
    let mut clipped = value.chars().take(LIMIT.saturating_sub(3)).collect::<String>();
    clipped.push_str("...");
    clipped
}

#[cfg(test)]
mod tests {
    use super::{format_live_progress, latest_progress_message, ContextUsage, ProgressFile};
    use serde_json::{json, Value};
    use std::fs;

    #[test]
    fn keeps_last_delegation_route_for_agent_work() {
        let events = vec![
            "AgentClient opened a streaming task channel to Architect.".to_string(),
            "Architect step 1: delegating to Coder (infer).".to_string(),
            "Coder step 1: LLM step started.".to_string(),
        ];

        let message = latest_progress_message(&events);
        assert!(message.contains("[Architect -> Coder]"));
        assert!(message.contains("[Coder]"));
        assert!(message.contains("coding"));
    }

    #[test]
    fn badges_live_trace_events() {
        let events = vec![
            "Architect step 1: delegating to Explorer (infer).".to_string(),
            "Explorer step 1: calling tool read_file.".to_string(),
        ];

        let progress = format_live_progress(&events);
        assert!(progress.contains("[SEND]"));
        assert!(progress.contains("[TOOL]"));
    }

    #[test]
    fn prefers_normalized_run_event_summaries() {
        let mut progress = ProgressFile::new("normalized-event-test");
        fs::write(
            &progress.path,
            serde_json::to_string(&json!({
                "event": "legacy fallback",
                "run_event": {
                    "type": "action.started",
                    "agent_name": "coder",
                    "summary": "Action started: replace_file"
                }
            }))
            .unwrap(),
        )
        .unwrap();

        assert_eq!(progress.read_new(), vec!["coder: Action started: replace_file"]);
        progress.cleanup();
    }

    #[test]
    fn renders_normalized_delegation_as_agent_route() {
        let mut progress = ProgressFile::new("normalized-delegation-test");
        fs::write(
            &progress.path,
            serde_json::to_string(&json!({
                "event": "legacy fallback",
                "run_event": {
                    "type": "action.started",
                    "agent_name": "architect",
                    "summary": "Action started: explorer",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "metadata": {"agent": "explorer", "action": "infer"}
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        let events = progress.read_new();
        assert_eq!(events, vec!["Architect: delegating to Explorer (infer)."]);
        assert!(latest_progress_message(&events).contains("[Architect -> Explorer]"));
        assert!(latest_progress_message(&events).contains("[Explorer]"));
        progress.cleanup();
    }

    #[test]
    fn suppresses_stream_chunks_from_live_progress() {
        let mut progress = ProgressFile::new("chunk-suppression-test");
        fs::write(
            &progress.path,
            serde_json::to_string(&json!({
                "event": "llm_chunk",
                "run_event": {
                    "type": "llm.stream",
                    "agent_name": "architect",
                    "summary": "llm_chunk",
                    "payload": {
                        "llm_event_type": "llm_chunk",
                        "content": "token"
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        assert!(progress.read_new().is_empty());
        progress.cleanup();
    }

    #[test]
    fn extracts_context_metrics_without_polluting_the_live_trace() {
        let mut progress = ProgressFile::new("context-metric-test");
        fs::write(
            &progress.path,
            serde_json::to_string(&json!({
                "event": "legacy llm_context",
                "run_event": {
                    "type": "llm.stream",
                    "agent_name": "architect",
                    "summary": "llm_context",
                    "payload": {
                        "type": "task_llm_stream",
                        "task_id": "task-1",
                        "agent_name": "architect",
                        "llm_event_type": "llm_context",
                        "step": 1,
                        "metadata": {
                            "model": "gemma4:e4b",
                            "context": {
                                "used_tokens": 5116,
                                "window_tokens": 8192,
                                "used_percent": 62.451,
                                "available_tokens": 3076,
                                "estimated": true
                            }
                        }
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        let batch = progress.read_new_batch();
        assert!(batch.events.is_empty());
        assert_eq!(batch.context_samples.len(), 1);
        assert_eq!(batch.context_samples[0].used_tokens, 5116);
        assert_eq!(batch.context_samples[0].window_tokens, Some(8192));
        assert_eq!(batch.context_samples[0].model, "gemma4:e4b");
        progress.cleanup();
    }

    #[test]
    fn extracts_context_prepared_manifests_without_polluting_the_live_trace() {
        let mut progress = ProgressFile::new("context-manifest-test");
        fs::write(
            &progress.path,
            serde_json::to_string(&json!({
                "event": "context prepared",
                "run_event": {
                    "type": "context.prepared",
                    "agent_name": "architect",
                    "task_id": "task-1",
                    "step": 1,
                    "summary": "Context prepared: 7000 estimated tokens",
                    "payload": {
                        "manifest": {
                            "agent_name": "architect",
                            "model": "gemma4:e4b",
                            "total_estimated_tokens": 7000,
                            "context_window": 8192,
                            "estimated": true
                        }
                    }
                }
            }))
            .unwrap(),
        )
        .unwrap();

        let batch = progress.read_new_batch();
        assert!(batch.events.is_empty());
        assert_eq!(batch.context_samples.len(), 1);
        assert_eq!(batch.context_samples[0].used_tokens, 7000);
        assert_eq!(batch.context_samples[0].window_tokens, Some(8192));
        assert_eq!(batch.context_samples[0].agent_name, "architect");
        assert_eq!(batch.context_samples[0].model, "gemma4:e4b");
        progress.cleanup();
    }

    #[test]
    fn retains_latest_context_and_high_water_mark() {
        let events = vec![
            json!({
                "payload": {
                    "task_id": "task-1",
                    "agent_name": "architect",
                    "llm_event_type": "llm_context",
                    "step": 1,
                    "metadata": {"context": {"used_tokens": 7000, "window_tokens": 8192, "used_percent": 85.45}}
                }
            }),
            json!({
                "payload": {
                    "task_id": "task-2",
                    "agent_name": "coder",
                    "llm_event_type": "llm_context",
                    "step": 1,
                    "metadata": {"context": {"used_tokens": 2400, "window_tokens": 8192, "used_percent": 29.3}}
                }
            }),
        ];
        let mut usage = ContextUsage::default();
        usage.observe_run_events(&events);

        assert_eq!(usage.latest().unwrap().agent_name, "coder");
        assert_eq!(usage.latest().unwrap().used_tokens, 2400);
        assert_eq!(usage.peak().unwrap().used_tokens, 7000);
    }

    #[test]
    fn exact_call_metrics_replace_the_provisional_peak() {
        let events = vec![
            json!({
                "payload": {
                    "task_id": "task-1",
                    "agent_name": "architect",
                    "llm_event_type": "llm_context",
                    "step": 1,
                    "metadata": {"context": {
                        "used_tokens": 8520,
                        "window_tokens": 8192,
                        "used_percent": 104.0,
                        "estimated": true
                    }}
                }
            }),
            json!({
                "payload": {
                    "task_id": "task-1",
                    "agent_name": "architect",
                    "llm_event_type": "llm_call_metrics",
                    "step": 1,
                    "metadata": {"context": {
                        "used_tokens": 6200,
                        "window_tokens": 8192,
                        "used_percent": 75.684,
                        "estimated": false
                    }}
                }
            }),
        ];
        let mut usage = ContextUsage::default();
        usage.observe_run_events(&events);

        assert_eq!(usage.latest().unwrap().used_tokens, 6200);
        assert_eq!(usage.peak().unwrap().used_tokens, 6200);
        assert!(!usage.peak().unwrap().estimated);
    }

    #[test]
    fn exchanges_typed_approval_and_cancel_controls() {
        let mut progress = ProgressFile::new("runtime-control-test");
        let request = json!({
            "request_id": "approval_123",
            "run_id": "run_123",
            "action": {
                "name": "replace_file",
                "description": "Replace src/lib.rs",
                "capabilities": ["workspace.write"],
                "payload": {"arguments": {"path": "src/lib.rs"}},
                "metadata": {"path": "src/lib.rs"},
                "artifacts": [{
                    "media_type": "text/x-diff",
                    "parts": [{"type": "text", "content": "--- a/src/lib.rs\n+++ b/src/lib.rs\n"}]
                }]
            }
        });
        fs::write(progress.approval_request_path(), serde_json::to_vec(&request).unwrap()).unwrap();

        let approval = progress.take_approval_request().unwrap();
        assert_eq!(approval.target, "src/lib.rs");
        assert_eq!(approval.capabilities(), "workspace.write");
        assert!(approval.diff.contains("+++ b/src/lib.rs"));
        progress.decide(&approval, true).unwrap();
        progress.request_cancel("test cancellation").unwrap();

        let decision: Value = serde_json::from_slice(
            &fs::read(progress.approval_decision_path()).unwrap(),
        )
        .unwrap();
        let cancellation: Value =
            serde_json::from_slice(&fs::read(progress.cancel_path()).unwrap()).unwrap();
        assert_eq!(decision["request_id"], "approval_123");
        assert_eq!(decision["approved"], true);
        assert_eq!(cancellation["reason"], "test cancellation");
        progress.cleanup();
    }
}
