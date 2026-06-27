use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct TimelineItem {
    pub(crate) kind: String,
    pub(crate) actor: String,
    pub(crate) target: String,
    pub(crate) action: String,
    pub(crate) detail: String,
}

impl TimelineItem {
    fn route(&self) -> String {
        if self.target.is_empty() || self.target == self.actor {
            self.actor.clone()
        } else {
            format!("{} -> {}", self.actor, self.target)
        }
    }
}

pub(crate) fn build_timeline(events: &[String]) -> Vec<TimelineItem> {
    let mut items = Vec::new();
    for event in events {
        if let Some(item) = parse_timeline_event(event) {
            items.push(item);
        }
    }
    compact_repeated(items)
}

pub(crate) fn build_timeline_from_run_events(
    run_events: &[Value],
    fallback_events: &[String],
) -> Vec<TimelineItem> {
    let causal = CausalIndex::from_events(run_events);
    let items = compact_repeated(
        run_events
            .iter()
            .filter_map(|event| parse_run_event(event, &causal))
            .collect(),
    );
    if items.is_empty() {
        build_timeline(fallback_events)
    } else {
        items
    }
}

pub(crate) fn format_timeline(events: &[String], limit: usize) -> String {
    let items = build_timeline(events);
    format_items(&items, limit)
}

pub(crate) fn format_timeline_from_run_events(
    run_events: &[Value],
    fallback_events: &[String],
    limit: usize,
) -> String {
    if run_events.is_empty() {
        return format_timeline(fallback_events, limit);
    }
    let items = build_timeline_from_run_events(run_events, fallback_events);
    format_items(&items, limit)
}

fn format_items(items: &[TimelineItem], limit: usize) -> String {
    if items.is_empty() {
        return "No structured timeline events yet.".to_string();
    }

    let mut rows = Vec::new();
    for (idx, item) in items.iter().take(limit).enumerate() {
        rows.push(format!(
            "{:02} {:<8} {:<26} {}",
            idx + 1,
            item.kind,
            truncate_plain(&item.route(), 26),
            item.action
        ));
        if !item.detail.is_empty() {
            rows.push(format!("   {:<8} {:<26} {}", "", "", item.detail));
        }
    }
    if items.len() > limit {
        rows.push(format!("...{} more timeline event(s)", items.len() - limit));
    }
    rows.join("\n")
}

pub(crate) fn panel_rows(events: &[String], limit: usize) -> Vec<String> {
    let items = build_timeline(events);
    panel_rows_for_items(&items, limit)
}

pub(crate) fn panel_rows_from_run_events(
    run_events: &[Value],
    fallback_events: &[String],
    limit: usize,
) -> Vec<String> {
    if run_events.is_empty() {
        return panel_rows(fallback_events, limit);
    }
    let items = build_timeline_from_run_events(run_events, fallback_events);
    panel_rows_for_items(&items, limit)
}

fn panel_rows_for_items(items: &[TimelineItem], limit: usize) -> Vec<String> {
    if items.is_empty() {
        return vec!["No structured timeline yet. Run a task first.".to_string()];
    }
    let mut rows = Vec::new();
    for (idx, item) in items.iter().take(limit).enumerate() {
        rows.push(format!(
            "{:02} {} | {} | {}",
            idx + 1,
            item.kind,
            item.route(),
            item.action
        ));
    }
    if items.len() > limit {
        rows.push(format!(
            "+{} more event(s). Use /timeline for the full view.",
            items.len() - limit
        ));
    }
    rows
}

pub(crate) fn summary(events: &[String]) -> String {
    let items = build_timeline(events);
    summarize_items(&items)
}

pub(crate) fn summary_from_run_events(run_events: &[Value], fallback_events: &[String]) -> String {
    if run_events.is_empty() {
        return summary(fallback_events);
    }
    let items = build_timeline_from_run_events(run_events, fallback_events);
    summarize_items(&items)
}

fn summarize_items(items: &[TimelineItem]) -> String {
    if items.is_empty() {
        return "No timeline events yet.".to_string();
    }
    let sends = items.iter().filter(|item| item.kind == "SEND").count();
    let tools = items.iter().filter(|item| item.kind == "TOOL").count();
    let models = items.iter().filter(|item| item.kind == "MODEL").count();
    let approvals = items.iter().filter(|item| item.kind == "APPROVAL").count();
    format!(
        "{} step(s): {} handoff(s), {} model step(s), {} tool step(s), {} approval step(s)",
        items.len(),
        sends,
        models,
        tools,
        approvals
    )
}

pub(crate) fn format_run_trace(run_events: &[Value], fallback_events: &[String]) -> String {
    if run_events.is_empty() {
        return fallback_events.join("\n");
    }
    let causal = CausalIndex::from_events(run_events);
    let mut rows = Vec::new();
    let mut suppressed = 0usize;
    for event in run_events {
        if is_trace_noise(event) {
            suppressed += 1;
            continue;
        }
        let sequence = event
            .get("sequence")
            .and_then(Value::as_u64)
            .map(|value| format!("{value:02}"))
            .unwrap_or_else(|| "--".to_string());
        let event_type = value_string(event, &["type"]);
        let route = trace_route(event, &causal);
        let summary = trace_summary(event)
            .if_empty_then(|| run_event_summary(event))
            .if_empty_then(|| event_type.clone())
            .if_empty_then(|| "runtime event".to_string());
        rows.push(format!(
            "{} {:<18} {:<22} {}",
            sequence,
            truncate_plain(&event_type, 18),
            truncate_plain(&route, 22),
            summary
        ));
    }
    if rows.is_empty() && !fallback_events.is_empty() {
        return fallback_events.join("\n");
    }
    if suppressed > 0 {
        rows.push(format!(
            "suppressed {suppressed} low-level stream/metric event(s)"
        ));
    }
    rows.join("\n")
}

#[derive(Debug, Clone)]
struct CausalNode {
    actor: String,
    target: String,
    parent_id: String,
}

#[derive(Debug, Default)]
struct CausalIndex {
    nodes: HashMap<String, CausalNode>,
}

impl CausalIndex {
    fn from_events(events: &[Value]) -> Self {
        let mut index = Self::default();
        for event in events {
            let node = CausalNode {
                actor: run_event_actor(event),
                target: event_target(event),
                parent_id: event_parent_id(event),
            };
            for id in event_causal_ids(event) {
                index.nodes.entry(id).or_insert_with(|| node.clone());
            }
        }
        index
    }

    fn route_for_event(&self, event: &Value) -> Vec<String> {
        let mut route = Vec::new();
        if let Some(parent_id) = nonempty(event_parent_id(event)) {
            self.append_route_for_id(&parent_id, &mut route, 0);
        }
        push_route_part(&mut route, run_event_actor(event));
        let target = event_target(event);
        if route_target_is_visible(event) {
            push_route_part(&mut route, target);
        }
        route
    }

    fn append_route_for_id(&self, id: &str, route: &mut Vec<String>, depth: usize) {
        if depth > 8 {
            return;
        }
        let Some(node) = self.nodes.get(id) else {
            return;
        };
        if let Some(parent_id) = nonempty(node.parent_id.clone()) {
            self.append_route_for_id(&parent_id, route, depth + 1);
        }
        push_route_part(route, node.actor.clone());
        push_route_part(route, node.target.clone());
    }
}

fn parse_run_event(event: &Value, _causal: &CausalIndex) -> Option<TimelineItem> {
    let event_type = value_string(event, &["type"]);
    let payload = event.get("payload").unwrap_or(&Value::Null);
    let llm_type = value_string(payload, &["llm_event_type"]);
    let actor = run_event_actor(event);
    let target = event_target(event);
    let summary = run_event_summary(event);

    match event_type.as_str() {
        "action.requested" => Some(item(
            "ACTION",
            &actor,
            "",
            "requested runtime action",
            action_name(payload).if_empty(summary),
        )),
        "action.policy" => Some(item(
            "POLICY",
            &actor,
            "Policy",
            "evaluated runtime action",
            decision_effect(payload).if_empty(summary),
        )),
        "approval.required" => Some(item(
            "APPROVAL",
            &actor,
            "Human",
            "requested runtime approval",
            action_name(payload).if_empty(summary),
        )),
        "approval.decided" => {
            let approved = payload
                .get("decision")
                .and_then(|decision| decision.get("approved"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            Some(item(
                "APPROVAL",
                &actor,
                "Human",
                if approved {
                    "approved runtime action"
                } else {
                    "denied runtime action"
                },
                summary,
            ))
        }
        "action.started" => {
            if llm_type == "agent_call_start" {
                return Some(item(
                    "SEND",
                    &actor,
                    &target,
                    "delegated task",
                    action_mode(payload),
                ));
            }
            Some(item(
                "TOOL",
                &actor,
                &target,
                "started runtime action",
                summary,
            ))
        }
        "action.completed" => {
            if llm_type == "agent_call_result" {
                return Some(item("RETURN", &target, &actor, "returned delegated result", ""));
            }
            Some(item(
                "TOOL",
                &actor,
                &target,
                "completed runtime action",
                summary,
            ))
        }
        "action.denied" => Some(item(
            "POLICY",
            &actor,
            "Policy",
            "denied runtime action",
            summary,
        )),
        "action.failed" => Some(item("ERROR", &actor, "", "runtime action failed", summary)),
        "context.prepared" => Some(item("CONTEXT", &actor, "Model", "prepared model context", summary)),
        "budget.warning" => Some(item("BUDGET", &actor, "Policy", "budget warning", summary)),
        "budget.exceeded" => Some(item("BUDGET", &actor, "Policy", "budget exceeded", summary)),
        "task.status" => Some(item(
            "TASK",
            "ProtoLink",
            &actor,
            "task state changed",
            summary,
        )),
        "task.progress" => Some(item("TASK", "ProtoLink", &actor, "progress update", summary)),
        "task.error" => Some(item("ERROR", &actor, "", "task failed", summary)),
        "llm.stream" => parse_llm_run_event(&actor, &llm_type, payload, summary),
        _ => None,
    }
}

fn is_trace_noise(event: &Value) -> bool {
    value_string(event, &["type"]) == "context.prepared"
        || matches!(
            value_string(event.get("payload").unwrap_or(&Value::Null), &["llm_event_type"]).as_str(),
            "context_prepared" | "llm_chunk" | "llm_context" | "llm_call_metrics"
        )
}

fn trace_route(event: &Value, causal: &CausalIndex) -> String {
    let payload = event.get("payload").unwrap_or(&Value::Null);
    let actor = run_event_actor(event);
    let target = event_target(event);
    match (
        value_string(event, &["type"]).as_str(),
        value_string(payload, &["llm_event_type"]).as_str(),
    ) {
        ("action.started", "agent_call_start") if !target.is_empty() => {
            format!("{actor} -> {target}")
        }
        ("action.completed", "agent_call_result") if !target.is_empty() => {
            format!("{target} -> {actor}")
        }
        ("action.started", "tool_start") => {
            let route = causal.route_for_event(event);
            if route.len() > 1 {
                route.join(" -> ")
            } else {
                actor
            }
        }
        _ => {
            let route = causal.route_for_event(event);
            if route.len() > 1 {
                route.join(" -> ")
            } else {
                actor
            }
        }
    }
}

fn trace_summary(event: &Value) -> String {
    let payload = event.get("payload").unwrap_or(&Value::Null);
    let metadata = payload.get("metadata").unwrap_or(&Value::Null);
    match (
        value_string(event, &["type"]).as_str(),
        value_string(payload, &["llm_event_type"]).as_str(),
    ) {
        ("action.started", "agent_call_start") => {
            let target = event_target(event);
            let mode = value_string(metadata, &["action"]);
            if target.is_empty() {
                String::new()
            } else if mode.is_empty() {
                format!("delegated task to {target}")
            } else {
                format!("delegated {mode} task")
            }
        }
        ("action.completed", "agent_call_result") => "returned delegated result".to_string(),
        ("action.started", "tool_start") => {
            let tool = event_target(event);
            if tool.is_empty() {
                String::new()
            } else {
                format!("calling tool {tool}")
            }
        }
        ("action.completed", "tool_result") => {
            let tool = event_target(event);
            if tool.is_empty() {
                String::new()
            } else {
                format!("tool {tool} returned")
            }
        }
        ("llm.stream", "llm_final") => "final response produced".to_string(),
        _ => String::new(),
    }
}

trait EmptyString {
    fn if_empty(self, fallback: String) -> String;
    fn if_empty_then(self, fallback: impl FnOnce() -> String) -> String;
    fn if_empty_else(self, fallback: impl FnOnce() -> String) -> String;
}

impl EmptyString for String {
    fn if_empty(self, fallback: String) -> String {
        if self.is_empty() { fallback } else { self }
    }

    fn if_empty_then(self, fallback: impl FnOnce() -> String) -> String {
        if self.is_empty() { fallback() } else { self }
    }

    fn if_empty_else(self, fallback: impl FnOnce() -> String) -> String {
        if self.is_empty() { fallback() } else { self }
    }
}

fn run_event_actor(event: &Value) -> String {
    let payload = event.get("payload").unwrap_or(&Value::Null);
    display_agent(
        &value_string(event, &["agent_name"])
            .if_empty_else(|| value_string(payload, &["agent_name"]))
            .if_empty_else(|| metadata_string(payload, "agent"))
            .if_empty_else(|| "ProtoLink".to_string()),
    )
}

fn run_event_summary(event: &Value) -> String {
    value_string(event, &["summary"]).trim().to_string()
}

fn action_name(payload: &Value) -> String {
    value_string(payload, &["action", "name"])
        .if_empty_else(|| value_string(payload, &["request", "action", "name"]))
        .if_empty_else(|| value_string(payload, &["metadata", "action", "name"]))
}

fn action_mode(payload: &Value) -> String {
    value_string(payload, &["action", "payload", "action"])
        .if_empty_else(|| value_string(payload, &["action", "payload", "mode"]))
        .if_empty_else(|| metadata_string(payload, "action"))
}

fn decision_effect(payload: &Value) -> String {
    value_string(payload, &["decision", "effect"])
        .if_empty_else(|| value_string(payload, &["metadata", "decision", "effect"]))
}

fn metadata_string(payload: &Value, key: &str) -> String {
    payload
        .get("metadata")
        .and_then(|metadata| metadata.get(key))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn event_target(event: &Value) -> String {
    let payload = event.get("payload").unwrap_or(&Value::Null);
    display_agent(
        &action_name(payload)
            .if_empty_else(|| value_string(payload, &["action", "kind"]))
            .if_empty_else(|| metadata_string(payload, "agent"))
            .if_empty_else(|| metadata_string(payload, "tool")),
    )
}

fn event_parent_id(event: &Value) -> String {
    value_string(event, &["parent_span_id"]).if_empty_else(|| value_string(event, &["parent_action_id"]))
}

fn event_causal_ids(event: &Value) -> Vec<String> {
    let mut ids = Vec::new();
    for key in ["span_id", "action_id", "delegation_id"] {
        if let Some(id) = nonempty(value_string(event, &[key])) {
            ids.push(id);
        }
    }
    ids
}

fn route_target_is_visible(event: &Value) -> bool {
    let payload = event.get("payload").unwrap_or(&Value::Null);
    matches!(
        (
            value_string(event, &["type"]).as_str(),
            value_string(payload, &["llm_event_type"]).as_str(),
        ),
        ("action.started", "agent_call_start") | ("action.started", "tool_start")
    )
}

fn push_route_part(route: &mut Vec<String>, value: String) {
    let value = display_agent(&value);
    if value.is_empty() || route.last() == Some(&value) {
        return;
    }
    route.push(value);
}

fn nonempty(value: String) -> Option<String> {
    let value = value.trim().to_string();
    if value.is_empty() { None } else { Some(value) }
}

fn value_string(value: &Value, path: &[&str]) -> String {
    let mut current = value;
    for key in path {
        current = current.get(*key).unwrap_or(&Value::Null);
    }
    current.as_str().unwrap_or("").to_string()
}

fn parse_llm_run_event(
    actor: &str,
    llm_type: &str,
    payload: &Value,
    summary: String,
) -> Option<TimelineItem> {
    match llm_type {
        "llm_step" => Some(item("MODEL", actor, "", agent_work_label(actor), "")),
        "llm_response" => Some(item("MODEL", actor, "", "received model response", "")),
        "llm_action" => Some(item(
            "ACTION",
            actor,
            "",
            "selected next action",
            value_string(payload, &["action"]).if_empty(summary),
        )),
        "llm_final" => Some(item("DONE", actor, "CLI", "returned final answer", "")),
        "llm_parse_error" | "llm_retry" => Some(item(
            "MODEL",
            actor,
            "",
            "retrying model action",
            summary,
        )),
        "llm_error" => Some(item("ERROR", actor, "", "model call failed", summary)),
        "context_prepared" | "llm_context" | "llm_call_metrics" | "llm_chunk" => None,
        _ => None,
    }
}

fn parse_timeline_event(event: &str) -> Option<TimelineItem> {
    if event.starts_with("AgentClient opened a streaming task channel to Architect")
        || event.starts_with("AgentClient sent the user task to Architect")
    {
        return Some(item("SEND", "CLI", "Architect", "sent user task", ""));
    }

    if event.starts_with("Architect discovery sees:") {
        return Some(item(
            "DISCOVER",
            "Architect",
            "Registry",
            "resolved available agents",
            clean_sentence_tail(event.strip_prefix("Architect discovery sees:").unwrap_or("")),
        ));
    }

    if event.starts_with("Architect received the request") {
        return Some(item("MODEL", "Architect", "", "loaded provider configuration", ""));
    }

    if event.starts_with("Explorer built a read-only context map") {
        return Some(item("CONTEXT", "Explorer", "Workspace", "mapped project context", ""));
    }

    if event.starts_with("Coder tools are registered") {
        return Some(item("RUNTIME", "Coder", "Approval", "registered approval-safe tools", ""));
    }

    if event.starts_with("Architect is processing") {
        return Some(item("MODEL", "Architect", "", "planning", ""));
    }

    if event.starts_with("Architect returned a final task response")
        || event.contains(": final response produced")
    {
        let actor = agent_prefix(event).unwrap_or_else(|| "Architect".to_string());
        return Some(item("DONE", &actor, "CLI", "returned final answer", ""));
    }

    if event.contains("Approval required") || event.starts_with("Approval ") || event.contains(": Approval ") {
        let actor = agent_prefix(event).unwrap_or_else(|| "Coder".to_string());
        let action = if event.to_ascii_lowercase().contains("approved") {
            "approved runtime action"
        } else if event.to_ascii_lowercase().contains("denied") {
            "denied runtime action"
        } else {
            "requested runtime approval"
        };
        return Some(item("APPROVAL", &actor, "Human", action, ""));
    }

    if event.contains("Policy decision:") {
        let actor = agent_prefix(event).unwrap_or_else(|| "Runtime".to_string());
        return Some(item("POLICY", &actor, "Policy", "evaluated runtime action", clean_sentence_tail(event)));
    }

    if let Some(actor) = agent_prefix(event) {
        if let Some(target) = text_after(event, ": delegating to ") {
            return Some(item(
                "SEND",
                &actor,
                &clean_agent_target(target),
                "delegated task",
                clean_parenthetical(target),
            ));
        }
        if let Some(source) = text_after(event, ": delegation from ") {
            return Some(item(
                "RETURN",
                &clean_return_source(source),
                &actor,
                "returned delegated result",
                "",
            ));
        }
        if let Some(tool) = text_after(event, ": calling tool ") {
            return Some(item(
                "TOOL",
                &actor,
                &clean_sentence_tail(tool),
                "started tool call",
                "",
            ));
        }
        if let Some(tool) = text_after(event, ": tool ") {
            if event.contains(" returned") {
                return Some(item(
                    "TOOL",
                    &actor,
                    &clean_return_source(tool),
                    "tool returned",
                    "",
                ));
            }
        }
        if event.contains(": LLM step started") {
            return Some(item("MODEL", &actor, "", agent_work_label(&actor), ""));
        }
        if event.contains(": LLM response") {
            return Some(item("MODEL", &actor, "", "received model response", ""));
        }
        if let Some(action) = text_after(event, ": selected action ") {
            return Some(item(
                "ACTION",
                &actor,
                "",
                "selected next action",
                clean_sentence_tail(action),
            ));
        }
    }

    if event.starts_with("Task state:") {
        return Some(item(
            "TASK",
            "ProtoLink",
            "",
            "task state changed",
            clean_sentence_tail(event.strip_prefix("Task state:").unwrap_or("")),
        ));
    }
    if event.starts_with("Progress:") {
        return Some(item(
            "TASK",
            "ProtoLink",
            "",
            "progress update",
            clean_sentence_tail(event.strip_prefix("Progress:").unwrap_or("")),
        ));
    }
    if event.starts_with("Registry started") {
        return Some(item("RUNTIME", "Registry", "", "started", ""));
    }
    if event.contains("registered at") {
        let actor = event.split_whitespace().next().unwrap_or("Agent");
        return Some(item("RUNTIME", &display_agent(actor), "Registry", "registered", ""));
    }
    None
}

fn item(kind: &str, actor: &str, target: &str, action: &str, detail: impl Into<String>) -> TimelineItem {
    TimelineItem {
        kind: kind.to_string(),
        actor: display_agent(actor),
        target: display_agent(target),
        action: action.to_string(),
        detail: detail.into(),
    }
}

fn compact_repeated(items: Vec<TimelineItem>) -> Vec<TimelineItem> {
    let mut out: Vec<TimelineItem> = Vec::new();
    for item in items {
        let duplicate = out.last().map(|last| {
            last.kind == item.kind
                && last.actor == item.actor
                && last.target == item.target
                && last.action == item.action
                && last.detail == item.detail
        });
        if duplicate == Some(true) {
            continue;
        }
        out.push(item);
    }
    out
}

fn agent_prefix(event: &str) -> Option<String> {
    let head = event.split(':').next()?.trim();
    let agent = head.split(" step ").next().unwrap_or(head).trim();
    match agent.to_ascii_lowercase().as_str() {
        "architect" => Some("Architect".to_string()),
        "explorer" => Some("Explorer".to_string()),
        "coder" => Some("Coder".to_string()),
        "agent" => Some("Agent".to_string()),
        _ => None,
    }
}

fn display_agent(value: &str) -> String {
    let value = value.trim();
    match value.to_ascii_lowercase().as_str() {
        "" => String::new(),
        "architect" => "Architect".to_string(),
        "explorer" => "Explorer".to_string(),
        "coder" => "Coder".to_string(),
        "cli" => "CLI".to_string(),
        "human" => "Human".to_string(),
        "registry" => "Registry".to_string(),
        "protolink" => "ProtoLink".to_string(),
        _ => {
            let mut chars = value.chars();
            match chars.next() {
                Some(first) => format!("{}{}", first.to_uppercase(), chars.as_str()),
                None => String::new(),
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

fn clean_agent_target(value: &str) -> String {
    display_agent(value.split(" (").next().unwrap_or(value).trim_end_matches('.'))
}

fn clean_parenthetical(value: &str) -> String {
    value
        .split_once('(')
        .and_then(|(_, tail)| tail.split_once(')').map(|(inner, _)| inner))
        .map(|inner| format!("mode: {inner}"))
        .unwrap_or_default()
}

fn clean_return_source(value: &str) -> String {
    display_agent(
        value
            .split(" returned")
            .next()
            .unwrap_or(value)
            .trim_end_matches('.')
            .trim(),
    )
}

fn clean_sentence_tail(value: &str) -> String {
    value.trim().trim_end_matches('.').trim().to_string()
}

fn truncate_plain(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        return text.to_string();
    }
    if width <= 3 {
        return ".".repeat(width);
    }
    let mut out = text.chars().take(width - 3).collect::<String>();
    out.push_str("...");
    out
}

#[cfg(test)]
mod tests {
    use super::{
        build_timeline, build_timeline_from_run_events, format_run_trace, format_timeline,
        format_timeline_from_run_events,
    };
    use serde_json::json;

    #[test]
    fn builds_agent_handoff_timeline() {
        let events = vec![
            "AgentClient opened a streaming task channel to Architect.".to_string(),
            "Architect step 1: delegating to Explorer (infer).".to_string(),
            "Explorer step 1: calling tool read_file.".to_string(),
            "Architect step 2: delegating to Coder (infer).".to_string(),
            "Coder step 1: LLM step started.".to_string(),
        ];

        let items = build_timeline(&events);
        assert!(
            items
                .iter()
                .any(|item| item.actor == "CLI" && item.target == "Architect")
        );
        assert!(
            items
                .iter()
                .any(|item| item.actor == "Architect" && item.target == "Explorer")
        );
        assert!(
            items
                .iter()
                .any(|item| item.actor == "Architect" && item.target == "Coder")
        );
        assert!(format_timeline(&events, 10).contains("Architect -> Coder"));
    }

    #[test]
    fn builds_fallback_diagnostic_timeline() {
        let events = vec![
            "Architect received the request and loaded provider configuration.".to_string(),
            "Explorer built a read-only context map for the workspace.".to_string(),
            "Coder tools are registered for diff and new-file approval payloads.".to_string(),
        ];

        let timeline = format_timeline(&events, 10);
        assert!(timeline.contains("Architect"));
        assert!(timeline.contains("Explorer -> Workspace"));
        assert!(timeline.contains("Coder -> Approval"));
    }

    #[test]
    fn builds_timeline_from_normalized_run_events() {
        let events = vec![
            json!({
                "type": "context.prepared",
                "agent_name": "architect",
                "summary": "Context prepared: 7000 estimated tokens",
                "payload": {"manifest": {"total_estimated_tokens": 7000}}
            }),
            json!({
                "type": "action.started",
                "agent_name": "architect",
                "summary": "Action started: explorer",
                "payload": {
                    "llm_event_type": "agent_call_start",
                    "metadata": {"agent": "explorer", "action": "infer"}
                }
            }),
            json!({
                "type": "action.started",
                "agent_name": "explorer",
                "summary": "Action started: read_file",
                "payload": {
                    "llm_event_type": "tool_start",
                    "metadata": {"tool": "read_file"}
                }
            }),
            json!({
                "type": "approval.required",
                "agent_name": "coder",
                "summary": "Approval required: replace_file",
                "payload": {"request": {"action": {"name": "replace_file"}}}
            }),
            json!({
                "type": "budget.warning",
                "agent_name": "architect",
                "summary": "Approaching input budget",
                "payload": {"decision": {"message": "Approaching input budget"}}
            }),
            json!({
                "type": "llm.stream",
                "agent_name": "architect",
                "payload": {"llm_event_type": "llm_final"}
            }),
        ];

        let items = build_timeline_from_run_events(&events, &[]);
        assert!(
            items
                .iter()
                .any(|item| item.actor == "Architect" && item.target == "Explorer")
        );
        assert!(
            items
                .iter()
                .any(|item| item.actor == "Explorer" && item.target == "Read_file")
        );
        assert!(items.iter().any(|item| item.kind == "CONTEXT"));
        assert!(items.iter().any(|item| item.kind == "BUDGET"));
        assert!(items.iter().any(|item| item.kind == "APPROVAL"));
        assert!(format_timeline_from_run_events(&events, &[], 10)
            .contains("returned final answer"));
    }

    #[test]
    fn run_trace_suppresses_token_chunks() {
        let trace = format_run_trace(
            &[
                json!({
                    "sequence": 1,
                    "type": "context.prepared",
                    "agent_name": "architect",
                    "summary": "Context prepared",
                    "payload": {"manifest": {"total_estimated_tokens": 7000}}
                }),
                json!({
                    "sequence": 2,
                    "type": "llm.stream",
                    "agent_name": "architect",
                    "summary": "llm_chunk",
                    "payload": {"llm_event_type": "llm_chunk", "content": "hello"}
                }),
                json!({
                    "sequence": 3,
                    "type": "action.started",
                    "agent_name": "architect",
                    "summary": "Action started: explorer",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "metadata": {"agent": "explorer"}
                    }
                }),
            ],
            &[],
        );

        assert!(!trace.contains("llm_chunk"));
        assert!(!trace.contains("context.prepared"));
        assert!(trace.contains("action.started"));
        assert!(trace.contains("Architect -> Explorer"));
        assert!(trace.contains("suppressed 2"));
    }

    #[test]
    fn run_trace_uses_causal_ids_for_nested_routes() {
        let trace = format_run_trace(
            &[
                json!({
                    "sequence": 1,
                    "type": "action.started",
                    "agent_name": "architect",
                    "span_id": "delegate-explorer",
                    "action_id": "delegate-explorer",
                    "delegation_id": "delegate-explorer",
                    "summary": "Action started: explorer",
                    "payload": {
                        "llm_event_type": "agent_call_start",
                        "action": {"name": "explorer", "payload": {"action": "infer"}}
                    }
                }),
                json!({
                    "sequence": 2,
                    "type": "action.started",
                    "agent_name": "explorer",
                    "span_id": "read-file",
                    "parent_span_id": "delegate-explorer",
                    "action_id": "read-file",
                    "parent_action_id": "delegate-explorer",
                    "summary": "Action started: read_file",
                    "payload": {
                        "llm_event_type": "tool_start",
                        "action": {"name": "read_file"}
                    }
                }),
            ],
            &[],
        );

        assert!(trace.contains("Architect -> Explorer"));
        assert!(trace.contains("Architect -> Explor..."));
        assert!(trace.contains("calling tool Read_file"));
    }
}
