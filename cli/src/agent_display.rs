//! Agent availability, shared by the shell and the cached TUI transcript.

use crate::summary::{Segment, Tone};
use crate::AgentSettings;
use unicode_width::UnicodeWidthStr;

pub(crate) struct AgentCard {
    pub(crate) name: &'static str,
    pub(crate) label: &'static str,
    purpose: &'static str,
    pub(crate) required: bool,
    tool_only: bool,
}

pub(crate) const AGENTS: [AgentCard; 7] = [
    AgentCard {
        name: "architect",
        label: "Architect",
        purpose: "Plans and coordinates",
        required: true,
        tool_only: false,
    },
    AgentCard {
        name: "explorer",
        label: "Explorer",
        purpose: "Reads project evidence",
        required: true,
        tool_only: false,
    },
    AgentCard {
        name: "coder",
        label: "Coder",
        purpose: "Prepares and applies edits",
        required: true,
        tool_only: false,
    },
    AgentCard {
        name: "tester",
        label: "Tester",
        purpose: "Suggests useful test cases",
        required: false,
        tool_only: false,
    },
    AgentCard {
        name: "verifier",
        label: "Verifier",
        purpose: "Runs one check pass after edits",
        required: false,
        tool_only: true,
    },
    AgentCard {
        name: "scout",
        label: "Scout",
        purpose: "Searches and reads the web",
        required: false,
        tool_only: true,
    },
    AgentCard {
        name: "mcp",
        label: "MCP",
        purpose: "Connects external tools",
        required: false,
        tool_only: true,
    },
];

impl AgentCard {
    fn compact_purpose(&self) -> &'static str {
        match self.name {
            "architect" => "Plans and routes",
            "explorer" => "Reads project files",
            "coder" => "Edits project files",
            "tester" => "Designs test cases",
            "verifier" => "Checks after edits",
            "scout" => "Searches the web",
            _ => "External tools",
        }
    }

    pub(crate) fn enabled(&self, settings: &AgentSettings) -> bool {
        self.required || settings.is_enabled(self.name)
    }

    pub(crate) fn badge(&self, settings: &AgentSettings) -> (&'static str, Tone) {
        if self.required {
            ("CORE", Tone::Title)
        } else if self.enabled(settings) {
            ("ON", Tone::Added)
        } else {
            ("OFF", Tone::Muted)
        }
    }
}

#[derive(Clone)]
pub(crate) struct View {
    settings: AgentSettings,
    focus: Option<String>,
}

impl View {
    pub(crate) fn new(settings: AgentSettings, focus: Option<String>) -> Self {
        Self { settings, focus }
    }

    pub(crate) fn rows(&self, width: usize) -> Vec<Vec<Segment>> {
        let lines = match self
            .focus
            .as_deref()
            .and_then(|name| AGENTS.iter().find(|a| a.name == name))
        {
            Some(agent) => self.detail(agent),
            None => self.overview(width),
        };
        wrap_words(lines, width.max(1))
    }

    pub(crate) fn plain_text(&self) -> String {
        self.rows(usize::MAX)
            .into_iter()
            .map(|row| row.into_iter().map(|s| s.text).collect::<String>())
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn overview(&self, width: usize) -> Vec<Vec<Segment>> {
        let enabled = AGENTS.iter().filter(|a| a.enabled(&self.settings)).count();
        let profile = &self.settings.prompt_profile.resolved;
        let mut lines = vec![
            vec![
                part("AGENT OVERVIEW", Tone::Title, true),
                part(
                    format!("  {enabled} enabled · {} disabled", AGENTS.len() - enabled),
                    Tone::Muted,
                    false,
                ),
            ],
            vec![part(
                format!(
                    "Profile: {} · /agents profile",
                    if profile.is_empty() { "auto" } else { profile }
                ),
                Tone::Muted,
                false,
            )],
        ];
        for agent in &AGENTS {
            let (badge, tone) = agent.badge(&self.settings);
            lines.push(vec![
                part(format!("{badge:<4}  "), tone, true),
                part(
                    format!("{:<11}", agent.label),
                    if agent.enabled(&self.settings) {
                        Tone::Plain
                    } else {
                        Tone::Muted
                    },
                    true,
                ),
                part(
                    if width < 68 {
                        format!("{:<6}", if agent.tool_only { "tools" } else { "LLM" })
                    } else {
                        format!("{:<10}", if agent.tool_only { "tool-only" } else { "LLM" })
                    },
                    Tone::Muted,
                    false,
                ),
                part(
                    if width < 68 {
                        agent.compact_purpose()
                    } else {
                        agent.purpose
                    },
                    Tone::Plain,
                    false,
                ),
            ]);
        }
        if width < 68 {
            lines.extend([
                vec![part(
                    "CORE = required · ON/OFF = optional",
                    Tone::Muted,
                    false,
                )],
                vec![part(
                    "Runs only when needed · Guide: /help",
                    Tone::Muted,
                    false,
                )],
                vec![part("Inspect: /agents NAME", Tone::Title, false)],
                vec![part("Toggle: /agents NAME on|off", Tone::Muted, false)],
            ]);
            return lines;
        }
        lines.extend([
            vec![part(
                "CORE = required · ON/OFF = optional availability",
                Tone::Muted,
                false,
            )],
            vec![part(
                "Agents run only when needed. Guide: /help QUESTION",
                Tone::Muted,
                false,
            )],
            vec![
                part("Inspect: /agents NAME", Tone::Title, false),
                part(" · Toggle: /agents NAME on|off", Tone::Muted, false),
            ],
        ]);
        lines
    }

    fn detail(&self, agent: &AgentCard) -> Vec<Vec<Segment>> {
        let (badge, tone) = agent.badge(&self.settings);
        let mut lines = vec![
            vec![
                part(agent.label, Tone::Title, true),
                part(format!("  {badge}"), tone, true),
                part(
                    if agent.tool_only {
                        " · tool-only (no LLM)"
                    } else {
                        " · LLM worker"
                    },
                    Tone::Muted,
                    false,
                ),
            ],
            vec![part(agent.purpose, Tone::Plain, false)],
        ];
        if let Some(manifest) = self.settings.agent(agent.name) {
            for (label, value) in [
                ("Role", manifest.role.as_str()),
                ("State", manifest.state.as_str()),
                ("Memory", manifest.memory.as_str()),
                ("Persistence", manifest.persistence.as_str()),
                ("Contract", manifest.contract.as_str()),
            ] {
                if !value.is_empty() {
                    lines.push(vec![
                        part(format!("{label}: "), Tone::Muted, false),
                        part(value, Tone::Plain, false),
                    ]);
                }
            }
            let tools = if manifest.tools.is_empty() {
                "none".to_string()
            } else {
                manifest.tools.join(", ")
            };
            lines.push(vec![
                part("Tools: ", Tone::Muted, false),
                part(tools, Tone::Title, false),
            ]);
        }
        lines.push(vec![]);
        lines.push(vec![
            part(
                format!(
                    "{}: {}",
                    agent.name,
                    if agent.enabled(&self.settings) {
                        "on"
                    } else {
                        "off"
                    }
                ),
                tone,
                true,
            ),
            part(
                if agent.required {
                    " · required"
                } else {
                    " · optional"
                },
                Tone::Muted,
                false,
            ),
        ]);
        if agent.required {
            lines.push(vec![part(
                "Core role · always available when the request needs it.",
                Tone::Muted,
                false,
            )]);
        } else {
            let next = if agent.enabled(&self.settings) {
                "off"
            } else {
                "on"
            };
            lines.push(vec![part(
                format!("Toggle: /agents {} {next}", agent.name),
                Tone::Title,
                true,
            )]);
            lines.push(vec![part(
                format!("Shell: proto-cli agents {} {next}", agent.name),
                Tone::Muted,
                false,
            )]);
            lines.push(vec![part(
                "Saved for future requests; enabled does not mean running.",
                Tone::Muted,
                false,
            )]);
        }
        if agent.name == "verifier" && !agent.enabled(&self.settings) {
            lines.push(vec![part(
                "Checks skipped · edits remain unverified.",
                Tone::Warning,
                false,
            )]);
        }
        if agent.name == "mcp" {
            lines.push(vec![part(
                "Server setup and connection status: /mcp",
                Tone::Muted,
                false,
            )]);
        }
        lines
    }
}

fn part(text: impl Into<String>, tone: Tone, bold: bool) -> Segment {
    Segment {
        text: text.into(),
        tone,
        bold,
    }
}

fn wrap_words(lines: Vec<Vec<Segment>>, width: usize) -> Vec<Vec<Segment>> {
    let mut output = Vec::new();
    for line in lines {
        let mut row: Vec<Segment> = Vec::new();
        let mut used: usize = 0;
        for segment in line {
            for word in segment.text.split_inclusive(' ') {
                if used > 0 && used.saturating_add(word.trim_end().width()) > width {
                    if let Some(last) = row.last_mut() {
                        last.text.truncate(last.text.trim_end().len());
                    }
                    output.push(std::mem::take(&mut row));
                    used = 0;
                }
                if used == 0 && word.trim().is_empty() {
                    continue;
                }
                used = used.saturating_add(word.width());
                row.push(part(word, segment.tone, segment.bold));
            }
        }
        if let Some(last) = row.last_mut() {
            last.text.truncate(last.text.trim_end().len());
        }
        output.push(row);
    }
    // Long paths and tool names still need safe grapheme/cell wrapping.
    crate::summary::wrap(output, width)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roster_preserves_every_role_and_status_at_narrow_widths() {
        let settings: AgentSettings = serde_json::from_value(serde_json::json!({
            "agents": [
                {"name": "verifier", "role": "checks", "enabled": false},
                {"name": "scout", "role": "web", "enabled": true}
            ]
        }))
        .unwrap();
        let view = View::new(settings, None);
        for width in [20, 42, 46, 76, 96] {
            let rows = view.rows(width);
            for row in &rows {
                assert!(row.iter().map(|s| s.text.width()).sum::<usize>() <= width);
            }
            let text = rows
                .iter()
                .map(|row| row.iter().map(|s| s.text.as_str()).collect::<String>())
                .collect::<Vec<_>>()
                .join(" ");
            let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
            for agent in &AGENTS {
                assert!(text.contains(agent.label));
            }
            assert!(text.contains("CORE Architect"));
            assert!(text.contains("OFF Verifier"));
            assert!(text.contains("ON Scout"));
            assert!(text.contains("4 enabled · 3 disabled"));
        }
    }

    #[test]
    fn verifier_detail_explains_disabled_checks_and_uses_live_manifest_tools() {
        let settings = serde_json::from_value(serde_json::json!({
            "agents": [{"name": "verifier", "role": "checks", "enabled": false, "tools": ["run_check"]}]
        })).unwrap();
        let text = View::new(settings, Some("verifier".into())).plain_text();
        for expected in [
            "tool-only (no LLM)",
            "verifier: off",
            "/agents verifier on",
            "edits remain unverified",
            "Tools: run_check",
        ] {
            assert!(text.contains(expected));
        }
        assert!(!text.contains("required"));
    }
}
