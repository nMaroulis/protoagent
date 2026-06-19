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
        let Ok(text) = fs::read_to_string(&self.path) else {
            return Vec::new();
        };

        let mut events = Vec::new();
        for line in text.lines().skip(self.seen_lines) {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                self.seen_lines += 1;
                continue;
            }

            let Ok(value) = serde_json::from_str::<Value>(trimmed) else {
                break;
            };
            let event = value
                .get("run_event")
                .and_then(run_event_summary)
                .or_else(|| value.get("event").and_then(Value::as_str).map(str::to_string));
            if let Some(event) = event
                .map(|event| event.trim().to_string())
                .filter(|event| !event.is_empty())
            {
                events.push(event);
            }
            self.seen_lines += 1;
        }
        events
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
    use super::{format_live_progress, latest_progress_message, ProgressFile};
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
