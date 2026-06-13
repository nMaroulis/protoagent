use serde_json::Value;
use std::fs;
use std::path::PathBuf;

const MAX_VISIBLE_EVENTS: usize = 8;

pub(crate) struct ProgressFile {
    path: PathBuf,
    seen_lines: usize,
}

impl ProgressFile {
    pub(crate) fn new(token: impl std::fmt::Display) -> Self {
        let path = std::env::temp_dir().join(format!(
            "protoagent-progress-{}-{token}.jsonl",
            std::process::id()
        ));
        let _ = fs::remove_file(&path);
        Self { path, seen_lines: 0 }
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
            if let Some(event) = value.get("event").and_then(Value::as_str) {
                let event = event.trim();
                if !event.is_empty() {
                    events.push(event.to_string());
                }
            }
            self.seen_lines += 1;
        }
        events
    }

    pub(crate) fn cleanup(&self) {
        let _ = fs::remove_file(&self.path);
    }
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
    if event.starts_with("Coder ") || event.starts_with("Coder safety net") {
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
    if event.starts_with("Coder safety net") || event.contains("approval action") {
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
    use super::{format_live_progress, latest_progress_message};

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
}
