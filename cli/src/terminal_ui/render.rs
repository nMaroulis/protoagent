use anyhow::Result;
use crossterm::{
    cursor::MoveTo,
    queue,
    style::{Color, Print, SetBackgroundColor, SetForegroundColor},
};
use std::io::Stdout;

use crate::wrap_lines;

use super::input::InputEditor;
use super::state::{PanelView, TerminalApp, TerminalMessage};
use super::theme::{
    bg, black, clip_plain, cyan, green, input_bg, magenta, muted, panel_bg, role_color, surface_bg, text,
    write_at, write_line, yellow,
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

    let rows = panel_rows(app);
    let available_rows = controls_row.saturating_sub(1) as usize;
    draw_panel_rows(out, width, &rows, available_rows)?;

    draw_command_bar(out, controls_row, width, app.panel)?;
    write_line(out, separator_row, width, &"-".repeat(width as usize), magenta(), panel_bg(), false)?;
    Ok(())
}

pub(super) fn draw_transcript(out: &mut Stdout, width: u16, height: u16, app: &TerminalApp) -> Result<()> {
    let top = HEADER_ROWS;
    let bottom = height.saturating_sub(INPUT_ROWS).max(top + 1);
    for y in top..bottom {
        write_line(out, y, width, "", text(), bg(), false)?;
    }

    let content_width = width.saturating_sub(4).max(20) as usize;
    let mut lines = Vec::new();
    for message in &app.messages {
        if !lines.is_empty() {
            lines.push(RenderLine::blank());
        }
        append_message_lines(&mut lines, message, content_width);
    }
    let visible = bottom.saturating_sub(top) as usize;
    let latest_start = lines.len().saturating_sub(visible);
    let scroll_offset = app.scroll_offset.min(latest_start);
    let start = latest_start.saturating_sub(scroll_offset);
    for (idx, line) in lines.iter().skip(start).take(visible).enumerate() {
        write_line(out, top + idx as u16, width, &line.text, line.color, bg(), line.bold)?;
    }
    if scroll_offset > 0 && visible > 0 {
        let marker = format!(
            "  chat scrolled up {} line(s)  |  wheel down / PageDown / Ctrl-End returns to live",
            scroll_offset
        );
        write_line(out, top, width, &marker, yellow(), bg(), true)?;
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
    write_line(out, top, width, &"-".repeat(width as usize), cyan(), input_bg(), false)?;
    write_line(out, top + 1, width, "", text(), input_bg(), false)?;
    write_line(
        out,
        top + 2,
        width,
        &format!(
            " status {}    chat {}    last {}",
            format!("{} / {}", app.status.project_short, app.activity),
            if app.scroll_offset == 0 { "live".to_string() } else { "scrolled".to_string() },
            if app.last_query.is_empty() { "none".to_string() } else { app.last_query.clone() }
        ),
        muted(),
        input_bg(),
        false,
    )?;
    write_line(out, top + 3, width, "", muted(), input_bg(), false)?;

    let prompt = " > ";
    let available = width.saturating_sub(prompt.len() as u16 + 4).max(10) as usize;
    let (visible, cursor) = editor
        .map(|editor| editor.visible(available))
        .unwrap_or_else(|| (String::new(), 0));
    queue!(
        out,
        MoveTo(2, top + 1),
        SetForegroundColor(cyan()),
        SetBackgroundColor(input_bg()),
        Print(prompt),
        SetForegroundColor(text()),
        Print(clip_plain(&visible, available))
    )?;
    Ok((2 + prompt.len() as u16 + cursor as u16, top + 1))
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

fn panel_rows(app: &TerminalApp) -> Vec<PanelRow> {
    let mut rows = vec![row(
        "model",
        format!("{} / {}    {}", app.status.provider, app.status.model, app.activity),
        cyan(),
        true,
    )];
    match app.panel {
        PanelView::Dashboard => {
            rows.push(row("project", &app.status.workspace, magenta(), app.status.project_ready));
            rows.push(row("models", &app.status.model_summary, cyan(), false));
            rows.push(row("agents", "Architect routes | Explorer reads | Coder drafts | human approves", yellow(), false));
            rows.push(row(
                "last",
                if app.last_query.is_empty() { "none".to_string() } else { app.last_query.clone() },
                muted(),
                false,
            ));
            rows.push(row("mode", "fullscreen takeover, fixed panels, fluid transcript, bottom input", green(), false));
        }
        PanelView::Project => {
            rows.push(row("active", &app.status.workspace, magenta(), app.status.project_ready));
            rows.push(row("state", if app.status.project_ready { "ready for tasks" } else { "select a project before tasks" }, cyan(), true));
            rows.push(row("set", "/project or /project PATH", yellow(), false));
            rows.push(row("clear", "/project clear", muted(), false));
            rows.push(row("tags", "type @ in the prompt to choose a file from the active project", green(), false));
            rows.push(row("store", &app.status.project_config_path, muted(), false));
        }
        PanelView::Models => {
            rows.push(row("active", format!("{} / {}", app.status.provider, app.status.model), cyan(), true));
            rows.push(row("choose", "/model opens the in-app provider/model picker", green(), true));
            rows.push(row("inventory", &app.status.model_summary, magenta(), false));
            rows.push(row("providers", &app.status.provider_summary, yellow(), false));
            rows.push(row("config", &app.status.config_path, muted(), false));
            rows.push(row("tip", "use /check for runtime wiring and /config for provider setup", green(), false));
        }
        PanelView::Agents => {
            rows.push(row("architect", "intake, routing, final answer, approval gate", magenta(), true));
            rows.push(row("explorer", "read-only files, directories, regex search, git status", cyan(), false));
            rows.push(row("coder", "approval-safe diffs and file payloads", yellow(), false));
            rows.push(row("approval", "human confirms side effects before writes land", green(), false));
            rows.push(row("surface", "terminal mirrors the browser cockpit without scrollback pollution", muted(), false));
        }
        PanelView::Check => {
            rows.push(row("runtime", &app.status.runtime, magenta(), true));
            rows.push(row("active", format!("{} / {}", app.status.provider, app.status.model), cyan(), false));
            rows.push(row("workspace", &app.status.workspace, yellow(), false));
            rows.push(row("config", &app.status.config_path, muted(), false));
            rows.push(row("refresh", "run /check to refresh", green(), false));
        }
        PanelView::Config => {
            rows.push(row("provider", &app.status.provider, cyan(), true));
            rows.push(row("model", &app.status.model, magenta(), false));
            rows.push(row("config", &app.status.config_path, yellow(), false));
            rows.push(row("keys", "model/key setup stays in regular terminal prompts for now", muted(), false));
            rows.push(row("report", "full report: proto-cli config", green(), false));
        }
        PanelView::Help => {
            rows.push(row("chat", "type any task or /run <task>", cyan(), true));
            rows.push(row("project", "/project chooses the folder; @ tags files into the prompt", yellow(), true));
            rows.push(row("model", "/model changes active provider/model without leaving the TUI", green(), true));
            rows.push(row("panels", "/dashboard /project /models /agents /check /config /help", magenta(), false));
            rows.push(row("output", "/trace shows last agent path; /diff shows proposed changes", cyan(), false));
            rows.push(row("scroll", "mouse wheel, PageUp/PageDown, Ctrl-End", yellow(), false));
            rows.push(row("session", "/quit or Esc", muted(), false));
            rows.push(row("launch", "fullscreen TUI: proto-cli start | direct task: proto-cli run \"task\"", green(), false));
        }
    }
    rows
}

fn draw_panel_rows(out: &mut Stdout, width: u16, rows: &[PanelRow], max_rows: usize) -> Result<()> {
    let label_width = 12usize;
    let body_x = label_width as u16 + 1;
    let body_width = width.saturating_sub(body_x + 1).max(10) as usize;
    let mut y = 1u16;
    let mut used = 0usize;
    for row in rows {
        if used >= max_rows {
            break;
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
            write_at(out, body_x, y, width.saturating_sub(body_x), line, text(), panel_bg(), row.bold)?;
            y += 1;
            used += 1;
        }
    }
    Ok(())
}

fn draw_command_bar(out: &mut Stdout, y: u16, width: u16, active: PanelView) -> Result<()> {
    write_line(out, y, width, "", muted(), panel_bg(), false)?;
    let commands = [
        (PanelView::Dashboard, "/dashboard"),
        (PanelView::Project, "/project"),
        (PanelView::Models, "/models"),
        (PanelView::Agents, "/agents"),
        (PanelView::Check, "/check"),
        (PanelView::Config, "/config"),
        (PanelView::Help, "/help"),
    ];
    let mut x = 1u16;
    for (panel, label) in commands {
        if x >= width.saturating_sub(1) {
            return Ok(());
        }
        let active_chip = panel == active;
        let chip = format!(" {} ", if active_chip { label.to_uppercase() } else { label.to_string() });
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
    let hint = "Wheel/PageUp scrolls chat  Esc exits";
    let hint_width = hint.chars().count() as u16;
    if x + 1 + hint_width <= width {
        write_at(out, x + 1, y, hint_width, hint, muted(), panel_bg(), false)?;
    }
    Ok(())
}

fn append_message_lines(lines: &mut Vec<RenderLine>, message: &TerminalMessage, width: usize) {
    let color = role_color(message.role);
    lines.push(RenderLine {
        text: format!("  {:<10}", message.label.to_uppercase()),
        color,
        bold: true,
    });
    append_wrapped_render_line(lines, "  | ", &message.body, text(), false, width);
    if !message.meta.is_empty() {
        append_wrapped_render_line(lines, "  | ", &format!("[{}]", message.meta.join("] [")), muted(), false, width);
    }
    if !message.details.is_empty() {
        let labels = message
            .details
            .iter()
            .map(|(label, _)| label.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        let hint = if message.details.iter().any(|(label, _)| label == "Proposed diff") {
            " (use /diff for proposed diff)"
        } else if message.details.iter().any(|(label, _)| label.contains("trace")) {
            " (use /trace for last agent trace)"
        } else {
            ""
        };
        append_wrapped_render_line(
            lines,
            "  | ",
            &format!("details: {labels}{hint}"),
            yellow(),
            false,
            width,
        );
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
        });
    }
}

struct RenderLine {
    text: String,
    color: Color,
    bold: bool,
}

impl RenderLine {
    fn blank() -> Self {
        Self {
            text: String::new(),
            color: muted(),
            bold: false,
        }
    }
}
