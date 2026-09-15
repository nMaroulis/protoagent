use anyhow::Result;
use crossterm::{
    cursor::MoveTo,
    queue,
    style::{
        Attribute, Color, Print, ResetColor, SetAttribute, SetBackgroundColor, SetForegroundColor,
    },
};
use std::io::{Stdout, Write};
use unicode_width::UnicodeWidthStr;

use crate::inline_style::{inline_code_segments, InlineKind};
use crate::wrap_lines;

use super::input::InputEditor;
use super::markdown::{self, Code, Span, Style};
use super::state::{PanelView, TerminalApp, TerminalMessage};
use super::theme::{
    bg, black, clip_plain, cyan, green, input_bg, magenta, muted, panel_bg, red, role_color,
    surface_bg, text, write_at, write_line, yellow,
};
use super::{HEADER_ROWS, INPUT_ROWS};

pub(super) fn draw_header(out: &mut Stdout, width: u16, app: &TerminalApp) -> Result<()> {
    let controls_row = HEADER_ROWS.saturating_sub(2);
    let separator_row = HEADER_ROWS.saturating_sub(1);
    write_line(
        out,
        0,
        width,
        &format!(" PROTOAGENT TERMINAL  {}", app.panel.label()),
        black(),
        magenta(),
        true,
    )?;

    for y in 1..separator_row {
        write_line(out, y, width, "", muted(), panel_bg(), false)?;
    }

    let available_rows = controls_row.saturating_sub(1) as usize;
    draw_model_row(out, width, 1, app)?;
    let rows = panel_rows(app);
    draw_panel_rows(out, width, &rows, available_rows.saturating_sub(1), 2)?;

    draw_command_bar(out, controls_row, width, app.panel)?;
    write_line(
        out,
        separator_row,
        width,
        &"-".repeat(width as usize),
        magenta(),
        panel_bg(),
        false,
    )?;
    Ok(())
}

pub(super) fn draw_transcript(
    out: &mut Stdout,
    width: u16,
    height: u16,
    app: &TerminalApp,
) -> Result<()> {
    let top = HEADER_ROWS;
    let bottom = height.saturating_sub(INPUT_ROWS).max(top + 1);
    for y in top..bottom {
        write_line(out, y, width, "", text(), bg(), false)?;
    }

    let content_width = width.saturating_sub(4).max(20) as usize;
    let mut lines = Vec::new();
    for (index, message) in app.messages.iter().enumerate() {
        if !lines.is_empty() {
            lines.push(RenderLine::blank());
        }
        let cursor = (app.active_response == Some(index)).then_some(true);
        append_message_lines(&mut lines, message, content_width, cursor, app.debug_mode);
    }
    let visible = bottom.saturating_sub(top) as usize;
    let latest_start = lines.len().saturating_sub(visible);
    let scroll_offset = app.scroll_offset.min(latest_start);
    let start = latest_start.saturating_sub(scroll_offset);
    for (idx, line) in lines.iter().skip(start).take(visible).enumerate() {
        draw_render_line(out, top + idx as u16, width, line)?;
    }
    if scroll_offset > 0 && visible > 0 {
        draw_scroll_marker(out, top, width, scroll_offset)?;
    }
    Ok(())
}

fn draw_render_line(out: &mut impl Write, y: u16, width: u16, line: &RenderLine) -> Result<()> {
    if line.footnote {
        let value = clip_plain(&line.text, width as usize);
        let padding = width as usize - unicode_width::UnicodeWidthStr::width(value.as_str());
        // Keep metadata uniformly subdued, including any backticks in its values.
        queue!(
            out,
            MoveTo(0, y),
            SetAttribute(Attribute::Reset),
            ResetColor,
            SetForegroundColor(line.color),
            SetAttribute(Attribute::Dim),
            Print(value),
            Print(" ".repeat(padding)),
            SetAttribute(Attribute::Reset)
        )?;
        return Ok(());
    }
    write_line(out, y, width, "", line.color, bg(), false)?;
    let mut x = 0u16;
    let body = if line.cursor.is_some() {
        line.text.strip_suffix('_').unwrap_or(&line.text)
    } else {
        &line.text
    };
    if let Some(spans) = &line.spans {
        for span in spans {
            let (fg, background) = match span.style.code {
                Code::None => (line.color, Color::Reset),
                Code::Inline => (black(), yellow()),
                Code::Block => (green(), Color::Reset),
            };
            draw_styled_segment(
                out, &mut x, y, width, &span.text, fg, background, span.style,
            )?;
        }
    } else {
        for segment in inline_code_segments(body) {
            match segment.kind {
                InlineKind::Text => {
                    draw_text_segment(
                        out,
                        &mut x,
                        y,
                        width,
                        &segment.text,
                        line.color,
                        bg(),
                        line.bold,
                    )?;
                }
                InlineKind::Code => {
                    draw_text_segment(
                        out,
                        &mut x,
                        y,
                        width,
                        &segment.text,
                        black(),
                        yellow(),
                        true,
                    )?;
                }
            }
        }
    }
    if line.cursor == Some(true) {
        draw_styled_segment(
            out,
            &mut x,
            y,
            width,
            "_",
            green(),
            Color::Reset,
            Style::default(),
        )?;
    }
    Ok(())
}

pub(super) fn draw_input(
    out: &mut Stdout,
    width: u16,
    height: u16,
    app: &TerminalApp,
    editor: Option<&InputEditor>,
) -> Result<(u16, u16)> {
    let top = height.saturating_sub(INPUT_ROWS);
    write_line(out, top, width, "", muted(), bg(), false)?;
    for row in 1..=3 {
        write_line(out, top + row, width, "", text(), input_bg(), false)?;
    }
    draw_context_usage(out, top + 4, width, app)?;
    draw_bottom_status(out, top + 5, width, app)?;

    let prompt = " > ";
    let available = width.saturating_sub(prompt.len() as u16 + 4).max(1) as usize;
    let (lines, cursor, cursor_row) = editor
        .map(|editor| editor.layout(available, 3))
        .unwrap_or_else(|| (vec![String::new()], 0, 0));
    for (row, visible) in lines.iter().enumerate() {
        queue!(
            out,
            MoveTo(2, top + 1 + row as u16),
            SetForegroundColor(cyan()),
            SetBackgroundColor(input_bg()),
            Print(if row == 0 { prompt } else { " · " }),
            SetForegroundColor(text()),
            Print(clip_plain(visible, available))
        )?;
    }
    if let Some(placeholder) = input_placeholder(editor, app.active_response.is_some()) {
        queue!(
            out,
            MoveTo(2 + prompt.len() as u16 + 1, top + 1),
            SetForegroundColor(muted()),
            SetAttribute(Attribute::Dim),
            Print(clip_plain(placeholder, available.saturating_sub(1))),
            SetAttribute(Attribute::Reset),
        )?;
    } else if lines.len() < 3 {
        write_at(
            out,
            5,
            top + 3,
            width.saturating_sub(6),
            &composer_hint(editor),
            muted(),
            input_bg(),
            false,
        )?;
    }
    Ok((
        2 + prompt.len() as u16 + cursor as u16,
        top + 1 + cursor_row as u16,
    ))
}

fn input_placeholder(editor: Option<&InputEditor>, running: bool) -> Option<&'static str> {
    if editor.is_some_and(|editor| !editor.is_empty()) {
        return None;
    }
    Some(if running {
        "Esc / Ctrl-C cancel"
    } else {
        "Ask anything · Enter sends · Ctrl-J newline · Tab commands · Ctrl-R history"
    })
}

fn composer_hint(editor: Option<&InputEditor>) -> String {
    if let Some(editor) = editor {
        let line = editor.line();
        if line.starts_with('/') && !line.contains('\n') {
            let matches = super::commands::matching_commands(&line);
            return format!(
                " Tab: {}",
                matches
                    .iter()
                    .take(3)
                    .map(|item| item.0)
                    .collect::<Vec<_>>()
                    .join("  ")
            );
        }
    }
    String::new()
}

fn draw_context_usage(out: &mut Stdout, y: u16, width: u16, app: &TerminalApp) -> Result<()> {
    write_line(out, y, width, "", muted(), input_bg(), false)?;
    let mut x = 1u16;
    draw_badge(out, &mut x, y, width, "CONTEXT", black(), cyan(), true)?;
    draw_text_segment(out, &mut x, y, width, "  ", muted(), input_bg(), false)?;

    let Some(latest) = app.context_usage.latest() else {
        return draw_text_segment(
            out,
            &mut x,
            y,
            width,
            "waiting for model metrics",
            muted(),
            input_bg(),
            false,
        );
    };

    let pressure = latest.used_percent.or_else(|| {
        latest
            .window_tokens
            .filter(|window| *window > 0)
            .map(|window| latest.used_tokens as f64 / window as f64 * 100.0)
    });
    let pressure_color = context_pressure_color(pressure);
    if let Some(percent) = pressure {
        let cells = if width >= 88 {
            18
        } else if width >= 60 {
            12
        } else {
            7
        };
        let filled = meter_fill(percent, cells);
        draw_text_segment(out, &mut x, y, width, "[", muted(), input_bg(), false)?;
        draw_text_segment(
            out,
            &mut x,
            y,
            width,
            &"#".repeat(filled),
            pressure_color,
            input_bg(),
            true,
        )?;
        draw_text_segment(
            out,
            &mut x,
            y,
            width,
            &".".repeat(cells.saturating_sub(filled)),
            muted(),
            input_bg(),
            false,
        )?;
        draw_text_segment(out, &mut x, y, width, "] ", muted(), input_bg(), false)?;
    }

    let estimated = if latest.estimated { "~" } else { "" };
    let usage = match latest.window_tokens {
        Some(window) => format!(
            "{}{}/{}  {:.0}%",
            estimated,
            compact_tokens(latest.used_tokens),
            compact_tokens(window),
            pressure.unwrap_or_default()
        ),
        None => format!("{}{} tokens", estimated, compact_tokens(latest.used_tokens)),
    };
    draw_text_segment(
        out,
        &mut x,
        y,
        width,
        &usage,
        pressure_color,
        input_bg(),
        true,
    )?;

    if width >= 76 {
        if let Some(peak) = app.context_usage.peak() {
            let peak_text = match peak.used_percent {
                Some(percent) => format!(
                    "  PEAK {}{:.0}%",
                    if peak.estimated { "~" } else { "" },
                    percent
                ),
                None => format!(
                    "  PEAK {}{}",
                    if peak.estimated { "~" } else { "" },
                    compact_tokens(peak.used_tokens)
                ),
            };
            draw_text_segment(
                out,
                &mut x,
                y,
                width,
                &peak_text,
                muted(),
                input_bg(),
                false,
            )?;
        }
    }
    if width >= 108 && !latest.model.is_empty() {
        draw_text_segment(
            out,
            &mut x,
            y,
            width,
            &format!("  {}", latest.model),
            muted(),
            input_bg(),
            false,
        )?;
    }
    Ok(())
}

fn context_pressure_color(percent: Option<f64>) -> Color {
    match percent {
        Some(value) if value >= 88.0 => red(),
        Some(value) if value >= 70.0 => yellow(),
        Some(_) => green(),
        None => cyan(),
    }
}

fn meter_fill(percent: f64, cells: usize) -> usize {
    ((percent.clamp(0.0, 100.0) / 100.0 * cells as f64).round() as usize).min(cells)
}

fn compact_tokens(tokens: u64) -> String {
    if tokens >= 1_000_000 {
        format!("{:.1}m", tokens as f64 / 1_000_000.0)
    } else if tokens >= 1_000 {
        format!("{:.1}k", tokens as f64 / 1_000.0)
    } else {
        tokens.to_string()
    }
}

pub(super) fn truncate_detail(text: &str, max_lines: usize) -> String {
    let mut lines = text.lines().take(max_lines).collect::<Vec<_>>().join("\n");
    if text.lines().count() > max_lines {
        lines.push_str("\n...truncated");
    }
    lines
}

struct PanelRow {
    label: &'static str,
    value: String,
    color: Color,
    bold: bool,
}

fn row(label: &'static str, value: impl Into<String>, color: Color, bold: bool) -> PanelRow {
    PanelRow {
        label,
        value: value.into(),
        color,
        bold,
    }
}

fn agent_state<'a>(agent: Option<&'a crate::AgentManifest>, fallback: &'a str) -> &'a str {
    agent
        .map(|agent| agent.state.trim())
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
}

fn agent_memory<'a>(agent: Option<&'a crate::AgentManifest>, fallback: &'a str) -> &'a str {
    agent
        .map(|agent| agent.memory.trim())
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
}

fn agent_tools(agent: Option<&crate::AgentManifest>, fallback: &str) -> String {
    agent
        .filter(|agent| !agent.tools.is_empty())
        .map(|agent| agent.tools.join(" + "))
        .unwrap_or_else(|| fallback.to_string())
}

fn panel_rows(app: &TerminalApp) -> Vec<PanelRow> {
    let mut rows = Vec::new();
    match app.panel {
        PanelView::Dashboard => {
            rows.push(row(
                "project",
                &app.status.workspace,
                magenta(),
                app.status.project_ready,
            ));
            rows.push(row("models", &app.status.model_summary, cyan(), false));
            rows.push(row("prompt", &app.status.prompt_profile, green(), false));
            rows.push(row(
                "agents",
                "RunContract -> Architect -> stateless workers -> policy gate",
                yellow(),
                false,
            ));
            rows.push(row(
                "last",
                if app.last_query.is_empty() {
                    "none".to_string()
                } else {
                    app.last_query.clone()
                },
                muted(),
                false,
            ));
            rows.push(row(
                "mode",
                "fullscreen takeover, fixed panels, fluid transcript, bottom input",
                green(),
                false,
            ));
        }
        PanelView::Project => {
            rows.push(row(
                "active",
                &app.status.workspace,
                magenta(),
                app.status.project_ready,
            ));
            rows.push(row(
                "state",
                if app.status.project_ready {
                    "ready for tasks"
                } else {
                    "select a project before tasks"
                },
                cyan(),
                true,
            ));
            rows.push(row("set", "/project or /project PATH", yellow(), false));
            rows.push(row("clear", "/project clear", muted(), false));
            rows.push(row(
                "tags",
                "type @ in the prompt to choose a file from the active project",
                green(),
                false,
            ));
            rows.push(row(
                "store",
                &app.status.project_config_path,
                muted(),
                false,
            ));
        }
        PanelView::Models => {
            rows.push(row(
                "active",
                format!("{} / {}", app.status.provider, app.status.model),
                cyan(),
                true,
            ));
            if app.models_loading {
                rows.push(row(
                    "inventory",
                    "scanning configured model sources...",
                    magenta(),
                    true,
                ));
                rows.push(row(
                    "local",
                    "Ollama [LOADING]  LM Studio [LOADING]  llama.cpp [LOADING]",
                    cyan(),
                    false,
                ));
                rows.push(row(
                    "cloud",
                    "provider keys and model access [CHECKING]",
                    yellow(),
                    false,
                ));
                rows.push(row("config", &app.status.config_path, muted(), false));
            } else {
                rows.push(row(
                    "inventory",
                    &app.status.model_summary,
                    magenta(),
                    false,
                ));
                rows.push(row(
                    "providers",
                    &app.status.provider_summary,
                    text(),
                    false,
                ));
                rows.push(row(
                    "setup",
                    "/model picks provider/model; /key openai stores a key",
                    green(),
                    true,
                ));
                rows.push(row("config", &app.status.config_path, muted(), false));
            }
        }
        PanelView::Agents => {
            let architect = app.agent_settings.agent("architect");
            let explorer = app.agent_settings.agent("explorer");
            let coder = app.agent_settings.agent("coder");
            let scout = app.agent_settings.agent("scout");
            rows.push(row(
                "kernel",
                "ProtoLink: context, budgets, events, policy, reports",
                magenta(),
                true,
            ));
            rows.push(row(
                "contract",
                "writes require Coder, diff/approval, or an explicit blocker",
                green(),
                true,
            ));
            rows.push(row(
                "architect",
                format!(
                    "{} controller; durable memory {}",
                    agent_state(architect, "stateful"),
                    agent_memory(architect, "protoagent-architect"),
                ),
                magenta(),
                true,
            ));
            rows.push(row(
                "workers",
                format!(
                    "Explorer read/{} | Coder write/{} | Verifier approved checks",
                    agent_state(explorer, "stateless"),
                    agent_state(coder, "stateless"),
                ),
                cyan(),
                false,
            ));
            rows.push(row(
                "scout",
                format!(
                    "{} | {} | /agents scout {}",
                    if app.agent_settings.is_scout_enabled() {
                        "ON"
                    } else {
                        "OFF"
                    },
                    agent_tools(scout, "web_search + fetch_url"),
                    if app.agent_settings.is_scout_enabled() {
                        "off"
                    } else {
                        "on"
                    },
                ),
                if app.agent_settings.is_scout_enabled() {
                    green()
                } else {
                    yellow()
                },
                true,
            ));
        }
        PanelView::Context => {
            rows.push(row(
                "loom",
                "deterministic workspace index plus source-cited Context Packs",
                magenta(),
                true,
            ));
            rows.push(row("window", "/context window 16k | auto", cyan(), true));
            rows.push(row(
                "memory",
                "/context on | off | history | compact | reset",
                yellow(),
                true,
            ));
            rows.push(row(
                "pack",
                "/context <query> previews workspace evidence",
                green(),
                false,
            ));
            rows.push(row(
                "refresh",
                "/index refresh reports updated / unchanged / removed files",
                muted(),
                false,
            ));
        }
        PanelView::Sessions => {
            for (idx, line) in crate::sessions::session_panel_rows()
                .into_iter()
                .take(6)
                .enumerate()
            {
                rows.push(row(
                    if idx == 0 { "current" } else { "session" },
                    line,
                    if idx == 0 { cyan() } else { muted() },
                    idx == 0,
                ));
            }
        }
        PanelView::Timeline => {
            if let Some(response) = &app.last_response {
                rows.push(row(
                    "summary",
                    crate::timeline::summary_from_run_events(
                        &response.run_events,
                        &response.events,
                    ),
                    cyan(),
                    true,
                ));
                for line in crate::timeline::panel_rows_from_run_events(
                    &response.run_events,
                    &response.events,
                    5,
                ) {
                    rows.push(row("step", line, yellow(), false));
                }
            } else {
                rows.push(row(
                    "timeline",
                    "No timeline yet. Run a task first.",
                    muted(),
                    false,
                ));
                rows.push(row(
                    "command",
                    "/timeline opens the latest structured agent path",
                    cyan(),
                    false,
                ));
            }
        }
        PanelView::Check => {
            rows.push(row("runtime", &app.status.runtime, magenta(), true));
            rows.push(row(
                "active",
                format!("{} / {}", app.status.provider, app.status.model),
                cyan(),
                false,
            ));
            rows.push(row("workspace", &app.status.workspace, yellow(), false));
            rows.push(row("config", &app.status.config_path, muted(), false));
            rows.push(row("refresh", "run /check to refresh", green(), false));
        }
        PanelView::Config => {
            rows.push(row("provider", &app.status.provider, cyan(), true));
            rows.push(row("model", &app.status.model, magenta(), false));
            rows.push(row("prompt", &app.status.prompt_profile, green(), false));
            rows.push(row("config", &app.status.config_path, yellow(), false));
            rows.push(row(
                "keys",
                "/key sets API keys here; proto-cli key openai works from shell",
                green(),
                false,
            ));
            rows.push(row(
                "report",
                "full report: proto-cli config",
                green(),
                false,
            ));
        }
        PanelView::Versions => {
            if app.version_rows.is_empty() {
                rows.push(row(
                    "refresh",
                    "/version loads component versions",
                    cyan(),
                    true,
                ));
            } else {
                for line in app.version_rows.iter().take(6) {
                    rows.push(row("component", line, cyan(), line.contains("proto-cli")));
                }
            }
            rows.push(row(
                "policy",
                "CLI/core follow SemVer; ACP stays prerelease until implemented",
                green(),
                false,
            ));
            rows.push(row(
                "sources",
                "cli/Cargo.toml, core/pyproject.toml, acp/VERSION",
                muted(),
                false,
            ));
        }
        PanelView::Help => {
            rows.push(row(
                "compose",
                "Enter sends | Ctrl-J newline | Tab commands | Ctrl-R history",
                cyan(),
                true,
            ));
            rows.push(row(
                "recover",
                "/checkpoints lists snapshots | /undo [id] reviews recovery",
                green(),
                true,
            ));
            rows.push(row(
                "verify",
                "Verifier runs checks with approval | V shows command preview",
                yellow(),
                true,
            ));
            rows.push(row(
                "guide",
                "/help <question> asks isolated Guide with current settings",
                magenta(),
                true,
            ));
            rows.push(row(
                "project",
                "/project chooses the folder; @ tags files into the prompt",
                yellow(),
                true,
            ));
            rows.push(row(
                "model",
                "/model changes active provider/model; /key stores API keys",
                green(),
                true,
            ));
            rows.push(row(
                "agents",
                "/agents profile | /agents scout on|off",
                cyan(),
                false,
            ));
            rows.push(row(
                "panels",
                "/dashboard /project /models /agents /context /sessions /timeline /version",
                magenta(),
                false,
            ));
            rows.push(row(
                "context",
                "/context on | off | history | window 16k | compact | reset",
                green(),
                false,
            ));
            rows.push(row(
                "output",
                "/trace raw logs; /timeline structured path; /diff proposed changes",
                cyan(),
                false,
            ));
            rows.push(row(
                "scroll",
                "mouse wheel, PageUp/PageDown, Ctrl-End",
                yellow(),
                false,
            ));
            rows.push(row(
                "cancel",
                "Esc or Ctrl-C while a task runs",
                red(),
                false,
            ));
            rows.push(row(
                "session",
                "/quit exits now; Esc asks first",
                muted(),
                false,
            ));
            rows.push(row(
                "launch",
                "fullscreen TUI: proto-cli start | direct task: proto-cli run \"task\"",
                green(),
                false,
            ));
        }
    }
    rows
}

fn draw_model_row(out: &mut Stdout, width: u16, y: u16, app: &TerminalApp) -> Result<()> {
    let label_width = 12usize;
    let body_x = label_width as u16 + 1;
    write_at(
        out,
        1,
        y,
        label_width as u16,
        " MODEL ",
        black(),
        cyan(),
        true,
    )?;
    let mut x = body_x;
    let model = format!("{} / {}", app.status.provider, app.status.model);
    draw_text_segment(out, &mut x, y, width, &model, text(), panel_bg(), true)?;
    Ok(())
}

fn draw_panel_rows(
    out: &mut Stdout,
    width: u16,
    rows: &[PanelRow],
    max_rows: usize,
    start_y: u16,
) -> Result<()> {
    let label_width = 12usize;
    let body_x = label_width as u16 + 1;
    let body_width = width.saturating_sub(body_x + 1).max(10) as usize;
    let mut y = start_y;
    let mut used = 0usize;
    for row in rows {
        if used >= max_rows {
            break;
        }
        if row.label == "providers" {
            write_at(
                out,
                1,
                y,
                label_width as u16,
                &format!(" {} ", row.label.to_uppercase()),
                row.color,
                panel_bg(),
                true,
            )?;
            draw_provider_segments(out, body_x, y, width, &row.value)?;
            y += 1;
            used += 1;
            continue;
        }
        let wrapped = wrap_lines(&row.value, body_width);
        for (line_index, line) in wrapped.iter().enumerate() {
            if used >= max_rows {
                break;
            }
            if line_index == 0 {
                write_at(
                    out,
                    1,
                    y,
                    label_width as u16,
                    &format!(" {} ", row.label.to_uppercase()),
                    if row.bold { black() } else { row.color },
                    if row.bold { row.color } else { panel_bg() },
                    true,
                )?;
            }
            write_at(
                out,
                body_x,
                y,
                width.saturating_sub(body_x),
                line,
                text(),
                panel_bg(),
                row.bold,
            )?;
            y += 1;
            used += 1;
        }
    }
    Ok(())
}

fn draw_provider_segments(
    out: &mut Stdout,
    start_x: u16,
    y: u16,
    width: u16,
    value: &str,
) -> Result<()> {
    write_at(
        out,
        start_x,
        y,
        width.saturating_sub(start_x),
        "",
        text(),
        panel_bg(),
        false,
    )?;
    let mut x = start_x;
    for segment in value
        .split("  ")
        .filter(|segment| !segment.trim().is_empty())
    {
        let color = provider_segment_color(segment);
        draw_text_segment(out, &mut x, y, width, segment, color, panel_bg(), true)?;
        draw_text_segment(out, &mut x, y, width, "  ", muted(), panel_bg(), false)?;
        if x >= width.saturating_sub(4) {
            break;
        }
    }
    Ok(())
}

fn provider_segment_color(segment: &str) -> Color {
    if segment.starts_with("K✓") || segment.starts_with("L✓") {
        green()
    } else if segment.starts_with("K✗") || segment.starts_with("L✗") {
        red()
    } else if segment.starts_with("K?") || segment.starts_with("K!") || segment.starts_with("L*") {
        yellow()
    } else {
        muted()
    }
}

fn draw_scroll_marker(out: &mut Stdout, y: u16, width: u16, scroll_offset: usize) -> Result<()> {
    write_line(out, y, width, "", text(), bg(), false)?;
    let mut x = 2u16;
    draw_badge(
        out,
        &mut x,
        y,
        width,
        "CHAT SCROLLED",
        black(),
        yellow(),
        true,
    )?;
    draw_text_segment(
        out,
        &mut x,
        y,
        width,
        &format!(" {} line(s) up", scroll_offset),
        yellow(),
        bg(),
        true,
    )?;
    draw_text_segment(
        out,
        &mut x,
        y,
        width,
        "  Wheel down / PageDown / Ctrl-End returns to live",
        muted(),
        bg(),
        false,
    )?;
    Ok(())
}

fn draw_bottom_status(out: &mut Stdout, y: u16, width: u16, app: &TerminalApp) -> Result<()> {
    write_line(out, y, width, "", muted(), input_bg(), false)?;
    let mut x = 1u16;
    draw_badge(out, &mut x, y, width, "PROJECT", black(), magenta(), true)?;
    draw_text_segment(
        out,
        &mut x,
        y,
        width,
        &format!(" {}  ", app.status.project_short),
        text(),
        input_bg(),
        true,
    )?;

    let live = app.scroll_offset == 0;
    draw_badge(
        out,
        &mut x,
        y,
        width,
        if live { "CHAT LIVE" } else { "CHAT SCROLLED" },
        black(),
        if live { green() } else { yellow() },
        true,
    )?;
    if !live {
        draw_text_segment(
            out,
            &mut x,
            y,
            width,
            &format!(" +{}  ", app.scroll_offset),
            yellow(),
            input_bg(),
            true,
        )?;
    } else {
        draw_text_segment(out, &mut x, y, width, "  ", muted(), input_bg(), false)?;
    }
    draw_activity_inline(out, &mut x, y, width, &app.activity, input_bg())?;
    Ok(())
}

fn draw_activity_inline(
    out: &mut Stdout,
    x: &mut u16,
    y: u16,
    width: u16,
    activity: &str,
    background: Color,
) -> Result<()> {
    let parsed = parse_activity(activity);
    if let Some(route) = parsed.route.as_deref() {
        draw_badge(out, x, y, width, route, text(), surface_bg(), true)?;
        draw_text_segment(out, x, y, width, " ", muted(), background, false)?;
    }
    if let Some(active) = parsed.active.as_deref() {
        draw_badge(
            out,
            x,
            y,
            width,
            active,
            black(),
            activity_color(active),
            true,
        )?;
        draw_text_segment(out, x, y, width, " ", muted(), background, false)?;
    }
    if let Some(spinner) = parsed.spinner.as_deref() {
        draw_text_segment(out, x, y, width, spinner, cyan(), background, true)?;
        draw_text_segment(out, x, y, width, " ", muted(), background, false)?;
    }
    draw_text_segment(out, x, y, width, &parsed.action, text(), background, false)?;
    Ok(())
}

#[allow(clippy::too_many_arguments)] // Terminal drawing primitives keep coordinates and style explicit.
fn draw_badge(
    out: &mut Stdout,
    x: &mut u16,
    y: u16,
    width: u16,
    label: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    draw_text_segment(
        out,
        x,
        y,
        width,
        &format!(" {} ", label),
        fg,
        background,
        bold,
    )
}

#[allow(clippy::too_many_arguments)] // Terminal drawing primitives keep coordinates and style explicit.
fn draw_text_segment(
    out: &mut impl Write,
    x: &mut u16,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    if *x >= width {
        return Ok(());
    }
    let value = clip_plain(text_value, width.saturating_sub(*x) as usize);
    let used = value.width() as u16;
    if used == 0 {
        return Ok(());
    }
    // Preserve the existing UI palette and terminal-default plain segments.
    // Markdown styling is scoped to draw_styled_segment, not this shared path.
    queue!(
        out,
        MoveTo(*x, y),
        SetForegroundColor(fg),
        SetBackgroundColor(background),
        SetAttribute(if bold {
            Attribute::Bold
        } else {
            Attribute::Reset
        }),
        Print(value),
        SetAttribute(Attribute::Reset),
        ResetColor
    )?;
    *x = (*x).saturating_add(used);
    Ok(())
}

#[allow(clippy::too_many_arguments)] // Terminal drawing primitives keep coordinates and style explicit.
fn draw_styled_segment(
    out: &mut impl Write,
    x: &mut u16,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    style: Style,
) -> Result<()> {
    if *x >= width {
        return Ok(());
    }
    let remaining = width.saturating_sub(*x) as usize;
    let value = clip_plain(text_value, remaining);
    let used = value.width() as u16;
    if used == 0 {
        return Ok(());
    }
    queue!(
        out,
        MoveTo(*x, y),
        // Response styles are independent of the shared UI helpers. Normal
        // text and the cursor use Color::Reset to retain the terminal background.
        SetAttribute(Attribute::Reset),
        SetAttribute(if style.bold || style.code == Code::Inline {
            Attribute::Bold
        } else {
            Attribute::NormalIntensity
        }),
        SetAttribute(if style.italic {
            Attribute::Italic
        } else {
            Attribute::NoItalic
        }),
        SetForegroundColor(fg),
        SetBackgroundColor(background),
        Print(value),
        SetAttribute(Attribute::Reset),
        ResetColor
    )?;
    *x = (*x).saturating_add(used);
    Ok(())
}

struct ActivityView {
    spinner: Option<String>,
    route: Option<String>,
    active: Option<String>,
    action: String,
}

fn parse_activity(activity: &str) -> ActivityView {
    let mut rest = activity.trim();
    let spinner = match rest.chars().next() {
        Some(ch) if matches!(ch, '|' | '/' | '-' | '\\') => {
            rest = rest[ch.len_utf8()..].trim_start();
            Some(ch.to_string())
        }
        _ => None,
    };
    let route = take_bracket(&mut rest);
    let active = take_bracket(&mut rest);
    let action = if rest.trim().is_empty() {
        "idle".to_string()
    } else {
        rest.trim().to_string()
    };

    ActivityView {
        spinner,
        route,
        active,
        action,
    }
}

fn take_bracket(rest: &mut &str) -> Option<String> {
    let value = rest.trim_start();
    if !value.starts_with('[') {
        *rest = value;
        return None;
    }
    let end = value.find(']')?;
    let badge = value[1..end].trim().to_string();
    *rest = value[end + 1..].trim_start();
    if badge.is_empty() {
        None
    } else {
        Some(badge)
    }
}

fn activity_color(agent: &str) -> Color {
    if agent.contains("Architect") {
        magenta()
    } else if agent.contains("Explorer") {
        cyan()
    } else if agent.contains("Coder") {
        yellow()
    } else if agent.contains("Registry") || agent.contains("CLI") {
        green()
    } else {
        muted()
    }
}

fn draw_command_bar(out: &mut Stdout, y: u16, width: u16, active: PanelView) -> Result<()> {
    write_line(out, y, width, "", muted(), panel_bg(), false)?;
    let commands = [
        (PanelView::Dashboard, "/dashboard"),
        (PanelView::Project, "/project"),
        (PanelView::Models, "/models"),
        (PanelView::Agents, "/agents"),
        (PanelView::Context, "/context"),
        (PanelView::Sessions, "/sessions"),
        (PanelView::Timeline, "/timeline"),
        (PanelView::Check, "/check"),
        (PanelView::Config, "/config"),
        (PanelView::Versions, "/version"),
        (PanelView::Help, "/help"),
    ];
    let mut x = 1u16;
    for (panel, label) in commands {
        if x >= width.saturating_sub(1) {
            return Ok(());
        }
        let active_chip = panel == active;
        let chip = format!(
            " {} ",
            if active_chip {
                label.to_uppercase()
            } else {
                label.to_string()
            }
        );
        let remaining = width.saturating_sub(x);
        let chip_width = (chip.chars().count() as u16).min(remaining);
        write_at(
            out,
            x,
            y,
            chip_width,
            &chip,
            if active_chip { black() } else { muted() },
            if active_chip { magenta() } else { surface_bg() },
            true,
        )?;
        x = x.saturating_add(chip_width + 1);
    }
    let hint = "Wheel/PageUp scrolls chat  Esc asks to exit";
    let hint_width = hint.chars().count() as u16;
    if x + 1 + hint_width <= width {
        write_at(out, x + 1, y, hint_width, hint, muted(), panel_bg(), false)?;
    }
    Ok(())
}

fn append_message_lines(
    lines: &mut Vec<RenderLine>,
    message: &TerminalMessage,
    width: usize,
    cursor: Option<bool>,
    debug: bool,
) {
    let running = cursor.is_some();
    let agent = matches!(message.role, super::state::Role::Assistant);
    let color = role_color(message.role);
    let heading = if agent {
        let status = message
            .meta
            .iter()
            .find_map(|item| item.strip_prefix("status "))
            .unwrap_or("answered");
        format!("  AGENT / {}  > {status}", message.label.to_lowercase())
    } else {
        format!("  {}", message.label.to_uppercase())
    };
    lines.push(RenderLine {
        text: clip_plain(&heading, width),
        color,
        bold: true,
        footnote: false,
        cursor: None,
        spans: None,
    });
    let cursor = if agent {
        Some(cursor.unwrap_or(false))
    } else {
        cursor
    };
    if agent {
        append_answer_lines(lines, &message.body, width, running);
    } else {
        append_wrapped_render_line(lines, "  | ", &message.body, text(), false, width);
    }
    if cursor.is_some() {
        // Reserve the same cell during and after execution. Keep it out of the
        // Markdown source so it cannot accidentally open/close emphasis.
        if lines.last().is_some_and(|line| line.text.width() >= width) {
            lines.push(answer_line(vec![]));
        }
        if let Some(last) = lines.last_mut() {
            last.text.push('_');
        }
    }
    if let Some(last) = lines.last_mut() {
        last.cursor = cursor;
    }
    if agent && (!debug || running) {
        // Native reports stay attached to the message and available through
        // /trace. Revealing them here at completion would push the answer up.
        return;
    }
    if !message.meta.is_empty() {
        append_footnote_lines(lines, &format!("[{}]", message.meta.join("] [")), width);
    }
    if !message.details.is_empty() {
        let labels = message
            .details
            .iter()
            .map(|(label, _)| label.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        let hint = if message
            .details
            .iter()
            .any(|(label, _)| label == "Proposed diff")
        {
            " (use /diff for proposed diff)"
        } else if message
            .details
            .iter()
            .any(|(label, _)| label.contains("trace"))
        {
            " (use /trace for last agent trace)"
        } else {
            ""
        };
        append_footnote_lines(lines, &format!("details: {labels}{hint}"), width);
    }
    if agent && debug {
        append_footnote_lines(lines, "debug on · /trace shows full run events and worker output · /debug off hides these details", width);
    }
}

fn append_answer_lines(lines: &mut Vec<RenderLine>, body: &str, width: usize, streaming: bool) {
    lines.extend(
        markdown::layout(body, width.saturating_sub(4).max(1), streaming)
            .into_iter()
            .map(answer_line),
    );
}

fn answer_line(mut spans: Vec<Span>) -> RenderLine {
    spans.insert(
        0,
        Span {
            text: "  | ".into(),
            style: Style::default(),
        },
    );
    RenderLine {
        text: spans.iter().map(|span| span.text.as_str()).collect(),
        color: text(),
        bold: false,
        footnote: false,
        cursor: None,
        spans: Some(spans),
    }
}

fn append_footnote_lines(lines: &mut Vec<RenderLine>, value: &str, width: usize) {
    let prefix = "  | ";
    for line in wrap_lines(value, width.saturating_sub(prefix.len()).max(1)) {
        lines.push(RenderLine {
            text: format!("{prefix}{line}"),
            color: Color::Grey,
            bold: false,
            footnote: true,
            cursor: None,
            spans: None,
        });
    }
}

fn append_wrapped_render_line(
    lines: &mut Vec<RenderLine>,
    prefix: &str,
    text_value: &str,
    color: Color,
    bold: bool,
    width: usize,
) {
    let prefix_width = prefix.chars().count();
    let wrap_width = width.saturating_sub(prefix_width).max(1);
    for line in wrap_lines(text_value, wrap_width) {
        lines.push(RenderLine {
            text: format!("{prefix}{line}"),
            color,
            bold,
            footnote: false,
            cursor: None,
            spans: None,
        });
    }
}

struct RenderLine {
    text: String,
    color: Color,
    bold: bool,
    footnote: bool,
    cursor: Option<bool>,
    spans: Option<Vec<Span>>,
}

impl RenderLine {
    fn blank() -> Self {
        Self {
            text: String::new(),
            color: muted(),
            bold: false,
            footnote: false,
            cursor: None,
            spans: None,
        }
    }
}

#[cfg(test)]
mod context_meter_tests {
    use super::{compact_tokens, meter_fill};

    #[test]
    fn input_instructions_are_only_an_empty_composer_placeholder() {
        use super::*;
        let mut editor = InputEditor::new(&Default::default());
        assert!(input_placeholder(Some(&editor), false)
            .unwrap()
            .contains("Enter sends"));
        assert_eq!(input_placeholder(None, true), Some("Esc / Ctrl-C cancel"));
        editor.insert_str("hello");
        assert_eq!(input_placeholder(Some(&editor), false), None);
        assert!(composer_hint(Some(&editor)).is_empty());
        editor.replace("/debug");
        assert!(composer_hint(Some(&editor)).contains("/debug"));
    }

    #[test]
    fn debug_reveals_metadata_and_trace_hint_only_after_streaming() {
        use super::*;
        use crate::terminal_ui::state::Role;
        let message = TerminalMessage {
            role: Role::Assistant,
            label: "guide".into(),
            body: "Use /config.".into(),
            meta: vec!["status completed".into(), "provider mock".into()],
            details: vec![("RunReport".into(), "report".into())],
        };
        let render = |cursor, debug| {
            let mut lines = vec![];
            append_message_lines(&mut lines, &message, 100, cursor, debug);
            lines
                .into_iter()
                .map(|line| line.text)
                .collect::<Vec<_>>()
                .join("\n")
        };
        assert!(!render(None, false).contains("provider mock"));
        assert!(!render(Some(true), true).contains("provider mock"));
        let debug = render(None, true);
        assert!(debug.contains("provider mock"));
        assert!(debug.contains("RunReport"));
        assert!(debug.contains("/trace"));
    }

    #[test]
    fn scales_context_pressure_into_fixed_cells() {
        assert_eq!(meter_fill(0.0, 12), 0);
        assert_eq!(meter_fill(62.5, 12), 8);
        assert_eq!(meter_fill(140.0, 12), 12);
    }

    #[test]
    fn formats_token_counts_compactly() {
        assert_eq!(compact_tokens(5116), "5.1k");
        assert_eq!(compact_tokens(8192), "8.2k");
        assert_eq!(compact_tokens(1_200_000), "1.2m");
    }

    #[test]
    fn completion_keeps_answer_rows_fixed_despite_large_report_metadata() {
        use super::*;
        use crate::terminal_ui::state::Role;
        for width in [20, 60, 100] {
            for body in [
                "an answer",
                "1234567890123456",
                "line one\nline two\n",
                "**bold** `code`\n```rust\n    let x = 1;\n```",
                "**界👩‍💻**",
            ] {
                let mut message = TerminalMessage {
                    role: Role::Assistant,
                    label: "architect".into(),
                    body: body.into(),
                    meta: vec!["status streaming".into()],
                    details: vec![],
                };
                let mut live = vec![];
                append_message_lines(&mut live, &message, width, Some(true), false);
                message.meta = vec!["status completed".into(), "model very long name".repeat(20)];
                message.details = vec![("RunReport".into(), "full native report".repeat(100))];
                let mut final_lines = vec![];
                append_message_lines(&mut final_lines, &message, width, None, false);
                assert_eq!(live.len(), final_lines.len());
                assert!(final_lines[0].text.starts_with("  AGENT / architect"));
                assert_eq!(
                    live[1..].iter().map(|line| &line.text).collect::<Vec<_>>(),
                    final_lines[1..]
                        .iter()
                        .map(|line| &line.text)
                        .collect::<Vec<_>>()
                );
            }
        }
    }

    #[test]
    fn response_cursor_reserves_its_cell_without_changing_saved_text() {
        use super::*;
        use crate::terminal_ui::state::Role;
        for body in ["", "1234567890123456", "hello\nworld", "`café` 🎉"] {
            let message = TerminalMessage {
                role: Role::Assistant,
                label: "Architect".into(),
                body: body.into(),
                meta: vec![],
                details: vec![],
            };
            let mut on = vec![];
            let mut off = vec![];
            append_message_lines(&mut on, &message, 20, Some(true), false);
            append_message_lines(&mut off, &message, 20, Some(false), false);
            assert_eq!(
                on.iter().map(|line| &line.text).collect::<Vec<_>>(),
                off.iter().map(|line| &line.text).collect::<Vec<_>>()
            );
            assert_eq!(on.last().unwrap().cursor, Some(true));
            assert_eq!(off.last().unwrap().cursor, Some(false));
            assert_eq!(message.body, body);
            let mut finished = vec![];
            append_message_lines(&mut finished, &message, 20, None, false);
            assert_eq!(finished.last().unwrap().cursor, Some(false));
            assert_eq!(finished.len(), on.len());
        }
    }

    #[test]
    fn response_cursor_uses_the_text_background_without_blink_attributes() {
        use super::*;
        let mut line = answer_line(markdown::layout("**bold** `code` plain", 80, true).remove(0));
        line.text.push('_');
        line.cursor = Some(true);
        let mut output = vec![];
        draw_render_line(&mut output, 0, 100, &line).unwrap();
        let output = String::from_utf8(output).unwrap();
        for text in ["bold", " plain", "_"] {
            assert_eq!(background_before(&output, text), Color::Reset);
        }
        assert_eq!(background_before(&output, "code"), yellow());
        assert!(output.contains(&SetAttribute(Attribute::Bold).to_string()));
        assert!(!output.contains("**"));
        assert!(!output.contains('`'));
        assert!(!output.contains(&SetAttribute(Attribute::SlowBlink).to_string()));
        assert!(!output.contains(&SetAttribute(Attribute::RapidBlink).to_string()));

        line.cursor = Some(false);
        output_final_has_no_cursor(&line);
    }

    // Interpret the emitted background at the point text is painted, including
    // SGR resets. Merely checking that a color sequence exists misses regressions.
    fn background_before(output: &str, text: &str) -> crossterm::style::Color {
        use crossterm::style::Color;
        let mut background = Color::Reset;
        for sequence in output[..output.find(text).unwrap()].split("\x1b[").skip(1) {
            let Some((parameters, _)) = sequence.split_once('m') else {
                continue;
            };
            let Ok(codes) = parameters
                .split(';')
                .map(str::parse::<u8>)
                .collect::<Result<Vec<_>, _>>()
            else {
                continue;
            };
            match codes.as_slice() {
                [0] | [49] => background = Color::Reset,
                [48, 2, r, g, b] => {
                    background = Color::Rgb {
                        r: *r,
                        g: *g,
                        b: *b,
                    }
                }
                [48, 5, value] => background = Color::AnsiValue(*value),
                _ => {}
            }
        }
        background
    }

    #[test]
    fn shared_ui_helpers_keep_plain_terminal_backgrounds_and_existing_chips() {
        use super::*;
        for background in [bg(), panel_bg(), input_bg()] {
            let mut output = vec![];
            write_line(&mut output, 0, 40, "plain row", text(), background, false).unwrap();
            assert_eq!(
                background_before(&String::from_utf8(output).unwrap(), "plain row"),
                Color::Reset
            );
            let mut output = vec![];
            draw_text_segment(
                &mut output,
                &mut 0,
                0,
                40,
                "plain segment",
                text(),
                background,
                false,
            )
            .unwrap();
            assert_eq!(
                background_before(&String::from_utf8(output).unwrap(), "plain segment"),
                Color::Reset
            );
        }
        let mut output = vec![];
        write_line(
            &mut output,
            0,
            40,
            "selected chip",
            black(),
            magenta(),
            true,
        )
        .unwrap();
        assert_eq!(
            background_before(&String::from_utf8(output).unwrap(), "selected chip"),
            magenta()
        );

        let line = answer_line(markdown::layout("```\nprint('hi')\n```", 80, true).remove(1));
        let mut output = vec![];
        draw_render_line(&mut output, 0, 80, &line).unwrap();
        assert_eq!(
            background_before(&String::from_utf8(output).unwrap(), "print('hi')"),
            Color::Reset
        );
    }

    fn output_final_has_no_cursor(line: &super::RenderLine) {
        let mut output = vec![];
        super::draw_render_line(&mut output, 0, 100, line).unwrap();
        assert!(!output.contains(&b'_'));
    }

    #[test]
    fn styled_segments_position_the_next_span_by_terminal_cells() {
        use super::*;
        let mut output = vec![];
        let mut x = 4;
        draw_text_segment(
            &mut output,
            &mut x,
            0,
            80,
            "界👩‍💻e\u{301}",
            text(),
            bg(),
            true,
        )
        .unwrap();
        assert_eq!(x, 9);
        draw_text_segment(&mut output, &mut x, 0, 80, "_", green(), bg(), false).unwrap();
        assert!(String::from_utf8(output)
            .unwrap()
            .contains(&MoveTo(9, 0).to_string()));
    }
}
