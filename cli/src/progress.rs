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
        rows.push("- Starting local agent runtime.".to_string());
        rows.push("- Waiting for Architect to publish the first task event.".to_string());
        return rows.join("\n");
    }

    let start = events.len().saturating_sub(MAX_VISIBLE_EVENTS);
    for event in &events[start..] {
        rows.push(format!("- {event}"));
    }
    rows.join("\n")
}

pub(crate) fn progress_activity(events: &[String], tick: usize) -> String {
    let spinner = ["|", "/", "-", "\\"];
    let detail = events
        .last()
        .map(String::as_str)
        .unwrap_or("starting ProtoLink runtime");
    format!("{} {}", spinner[tick % spinner.len()], clip_activity(detail))
}

pub(crate) fn latest_progress_message(events: &[String]) -> String {
    events
        .last()
        .map(|event| clip_activity(event))
        .unwrap_or_else(|| "starting ProtoLink runtime".to_string())
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
