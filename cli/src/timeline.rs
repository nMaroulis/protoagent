use serde::{Deserialize, Serialize};

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

pub(crate) fn format_timeline(events: &[String], limit: usize) -> String {
    let items = build_timeline(events);
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
        rows.push(format!("+{} more event(s). Use /timeline for the full view.", items.len() - limit));
    }
    rows
}

pub(crate) fn summary(events: &[String]) -> String {
    let items = build_timeline(events);
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
    use super::{build_timeline, format_timeline};

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
        assert!(items.iter().any(|item| item.actor == "CLI" && item.target == "Architect"));
        assert!(items.iter().any(|item| item.actor == "Architect" && item.target == "Explorer"));
        assert!(items.iter().any(|item| item.actor == "Architect" && item.target == "Coder"));
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
}
