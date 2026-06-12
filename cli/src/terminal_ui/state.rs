use std::collections::VecDeque;

use crate::{
    empty_as_unknown, load_inventory, load_visible_config, CoreResponse, DoctorReport, ModelInventory,
    INPUT_HISTORY_CAPACITY,
};

pub(super) struct TerminalApp {
    pub(super) turn: usize,
    pub(super) panel: PanelView,
    pub(super) status: StatusSnapshot,
    pub(super) messages: Vec<TerminalMessage>,
    pub(super) input_history: VecDeque<String>,
    pub(super) last_query: String,
    pub(super) last_response: Option<CoreResponse>,
    pub(super) activity: String,
    pub(super) scroll_offset: usize,
}

impl TerminalApp {
    pub(super) fn new() -> Self {
        let mut app = Self {
            turn: 0,
            panel: PanelView::Dashboard,
            status: StatusSnapshot::default(),
            messages: Vec::new(),
            input_history: VecDeque::new(),
            last_query: String::new(),
            last_response: None,
            activity: "idle".to_string(),
            scroll_offset: 0,
        };
        app.refresh(None);
        if let Some(project) = crate::active_project_dir() {
            app.push(
                Role::System,
                "Project",
                &format!(
                    "Opened last project: {}\nType a task to run it in this folder. Type @ to tag files.",
                    project.to_string_lossy()
                ),
            );
        } else if let Some(project) = crate::stored_project_dir() {
            app.push(
                Role::Error,
                "Project",
                &format!(
                    "Last project is missing: {}\nUse /project to choose a folder before running tasks.",
                    project.to_string_lossy()
                ),
            );
        } else {
            app.push(
                Role::System,
                "Project",
                "No project selected yet.\nUse /project to choose a folder, or /project . for the launch directory.",
            );
        }
        app
    }

    pub(super) fn refresh(&mut self, doctor: Option<&DoctorReport>) {
        self.status = StatusSnapshot::load(doctor);
    }

    pub(super) fn remember(&mut self, input: &str) {
        let input = input.trim();
        if input.is_empty() || self.input_history.back().map(String::as_str) == Some(input) {
            return;
        }
        if self.input_history.len() >= INPUT_HISTORY_CAPACITY {
            self.input_history.pop_front();
        }
        self.input_history.push_back(input.to_string());
    }

    pub(super) fn push(&mut self, role: Role, label: &str, body: &str) {
        self.jump_to_bottom();
        self.messages.push(TerminalMessage {
            role,
            label: label.to_string(),
            body: body.to_string(),
            meta: Vec::new(),
            details: Vec::new(),
        });
    }

    pub(super) fn push_response(&mut self, response: &CoreResponse) {
        self.jump_to_bottom();
        if !response.events.is_empty() {
            self.messages.push(TerminalMessage {
                role: Role::System,
                label: "Agent Trace".to_string(),
                body: compact_agent_trace(&response.events),
                meta: vec![format!("{} event(s)", response.events.len())],
                details: vec![("Full trace".to_string(), response.events.join("\n"))],
            });
        }
        let body = response
            .answer
            .trim()
            .if_empty_then(response.headline.trim())
            .if_empty_then("(no answer text)");
        let mut message = TerminalMessage {
            role: Role::Assistant,
            label: crate::response_actor(response),
            body: body.to_string(),
            meta: vec![
                format!("status {}", response.status),
                format!("provider {}", empty_as_unknown(&response.provider)),
                format!(
                    "model {}",
                    if response.model.is_empty() {
                        "not selected"
                    } else {
                        response.model.as_str()
                    }
                ),
                format!("{} ms", response.elapsed_ms),
            ],
            details: Vec::new(),
        };
        if !response.file_target.is_empty() {
            message.meta.push(format!("target {}", response.file_target));
        }
        if !response.warning.is_empty() {
            message.meta.push(format!("warning {}", response.warning));
        }
        if !response.thought_process.is_empty() {
            message.details.push(("Core notes".to_string(), response.thought_process.clone()));
        }
        if !response.events.is_empty() {
            message.details.push(("Agent trace".to_string(), response.events.join("\n")));
        }
        if !response.diff.trim().is_empty() {
            message.details.push(("Proposed diff".to_string(), response.diff.clone()));
        }
        if !response.actions.is_empty() {
            message
                .details
                .push(("Approval required".to_string(), format!("{} action payload(s) waiting.", response.actions.len())));
        }
        self.messages.push(message);
    }

    pub(super) fn scroll_up(&mut self, amount: usize) {
        self.scroll_offset = self.scroll_offset.saturating_add(amount).min(100_000);
    }

    pub(super) fn scroll_down(&mut self, amount: usize) {
        self.scroll_offset = self.scroll_offset.saturating_sub(amount);
    }

    pub(super) fn jump_to_bottom(&mut self) {
        self.scroll_offset = 0;
    }
}

fn compact_agent_trace(events: &[String]) -> String {
    let limit = 14usize;
    let mut rows = events
        .iter()
        .take(limit)
        .map(|event| format!("- {event}"))
        .collect::<Vec<_>>();
    if events.len() > limit {
        rows.push(format!("- ...{} more event(s)", events.len() - limit));
    }
    rows.join("\n")
}

trait EmptyFallback<'a> {
    fn if_empty_then(self, fallback: &'a str) -> &'a str;
}

impl<'a> EmptyFallback<'a> for &'a str {
    fn if_empty_then(self, fallback: &'a str) -> &'a str {
        if self.is_empty() { fallback } else { self }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum PanelView {
    Dashboard,
    Project,
    Models,
    Agents,
    Check,
    Config,
    Help,
}

impl PanelView {
    pub(super) fn label(self) -> &'static str {
        match self {
            Self::Dashboard => "dashboard",
            Self::Project => "project",
            Self::Models => "models",
            Self::Agents => "agents",
            Self::Check => "check",
            Self::Config => "config",
            Self::Help => "help",
        }
    }
}

#[derive(Clone, Copy)]
pub(super) enum Role {
    User,
    Assistant,
    Command,
    System,
    Error,
}

pub(super) struct TerminalMessage {
    pub(super) role: Role,
    pub(super) label: String,
    pub(super) body: String,
    pub(super) meta: Vec<String>,
    pub(super) details: Vec<(String, String)>,
}

#[derive(Default)]
pub(super) struct StatusSnapshot {
    pub(super) provider: String,
    pub(super) model: String,
    pub(super) workspace: String,
    pub(super) project_short: String,
    pub(super) project_config_path: String,
    pub(super) project_ready: bool,
    pub(super) config_path: String,
    pub(super) model_summary: String,
    pub(super) provider_summary: String,
    pub(super) runtime: String,
}

impl StatusSnapshot {
    fn load(doctor: Option<&DoctorReport>) -> Self {
        let mut snapshot = Self {
            provider: "unknown".to_string(),
            model: "not selected".to_string(),
            workspace: crate::project_label(),
            project_short: crate::project_short_label(),
            project_config_path: crate::project_config_path().to_string_lossy().to_string(),
            project_ready: crate::active_project_dir().is_some(),
            config_path: "unknown".to_string(),
            model_summary: "model inventory unavailable".to_string(),
            provider_summary: "providers unavailable".to_string(),
            runtime: "runtime not checked".to_string(),
        };
        if let Ok(config) = load_visible_config() {
            snapshot.provider = config.active_provider.clone();
            snapshot.config_path = config.config_path.clone();
            snapshot.model = config
                .providers
                .get(&config.active_provider)
                .map(|provider| {
                    if provider.model.is_empty() {
                        "not selected".to_string()
                    } else {
                        provider.model.clone()
                    }
                })
                .unwrap_or_else(|| "not selected".to_string());
        }
        if let Ok(inventory) = load_inventory() {
            snapshot.model_summary = model_summary(&inventory);
            snapshot.provider_summary = provider_summary(&inventory);
        }
        if let Some(report) = doctor {
            snapshot.runtime = doctor_summary(report);
        }
        snapshot
    }
}

fn model_summary(inventory: &ModelInventory) -> String {
    let total: usize = inventory.providers.iter().map(|provider| provider.models.len()).sum();
    let ready = inventory
        .providers
        .iter()
        .filter(|provider| provider.status == "online" || provider.status == "configured")
        .count();
    format!("{} models across {} providers, {} ready", total, inventory.providers.len(), ready)
}

fn provider_summary(inventory: &ModelInventory) -> String {
    let mut rows = inventory
        .providers
        .iter()
        .take(5)
        .map(|provider| format!("{}: {}, {} model(s)", provider.name, provider.status, provider.models.len()))
        .collect::<Vec<_>>();
    if inventory.providers.len() > rows.len() {
        rows.push(format!("+{} more provider(s)", inventory.providers.len() - rows.len()));
    }
    rows.join(" | ")
}

fn doctor_summary(report: &DoctorReport) -> String {
    let protolink = if report.protolink.installed && report.protolink.agent_ready {
        format!(
            "ProtoLink {} ready, streaming {}",
            empty_as_unknown(&report.protolink.version),
            if report.protolink.streaming_ready { "ready" } else { "unavailable" }
        )
    } else if report.protolink.installed {
        format!("ProtoLink blocked ({})", report.protolink.error)
    } else {
        format!("ProtoLink missing ({})", report.protolink.error)
    };
    format!(
        "{} | Python {} | active {} [{}]",
        protolink,
        report.python,
        if report.active_model.is_empty() {
            report.active_provider.clone()
        } else {
            format!("{} / {}", report.active_provider, report.active_model)
        },
        report.active_provider_status
    )
}
