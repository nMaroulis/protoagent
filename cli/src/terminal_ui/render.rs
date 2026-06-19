use anyhow::Result;
use crossterm::{
    cursor::MoveTo,
    queue,
    style::{Attribute, Color, Print, ResetColor, SetAttribute, SetBackgroundColor, SetForegroundColor},
};
use std::io::Stdout;

use crate::wrap_lines;

use super::input::InputEditor;
use super::state::{PanelView, TerminalApp, TerminalMessage};
use super::theme::{
    bg, black, clip_plain, cyan, green, input_bg, magenta, muted, panel_bg, red, role_color, surface_bg,
    text, write_at, write_line, yellow,
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
    draw_model_activity_row(out, width, 1, app)?;
    let rows = panel_rows(app);
    draw_panel_rows(out, width, &rows, available_rows.saturating_sub(1), 2)?;

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
        draw_scroll_marker(out, top, width, scroll_offset)?;
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
    draw_bottom_status(out, top + 2, width, app)?;
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
    let mut rows = Vec::new();
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
            rows.push(row("inventory", &app.status.model_summary, magenta(), false));
            rows.push(row("providers", &app.status.provider_summary, text(), false));
            rows.push(row("setup", "/model picks provider/model; /key openai stores a key", green(), true));
            rows.push(row("config", &app.status.config_path, muted(), false));
        }
        PanelView::Agents => {
            rows.push(row("architect", "intake, routing, final answer, approval gate", magenta(), true));
            rows.push(row("loom", "Context Loom feeds source-cited evidence before routing", green(), true));
            rows.push(row("explorer", "context packs, read-only files, directories, regex search, git status", cyan(), false));
            rows.push(row("coder", "approval-safe diffs and file payloads", yellow(), false));
            rows.push(row("approval", "human confirms side effects before writes land", green(), false));
            rows.push(row("surface", "terminal mirrors the browser cockpit without scrollback pollution", muted(), false));
        }
        PanelView::Context => {
            rows.push(row("loom", "deterministic workspace index plus source-cited Context Packs", magenta(), true));
            rows.push(row("status", "/context shows index status for the active project", cyan(), false));
            rows.push(row("pack", "/context <query> builds an evidence pack without running a model", yellow(), false));
            rows.push(row("refresh", "/index refresh rebuilds the local SQLite index", green(), false));
            rows.push(row("model", "packs are injected into ProtoLink Architect prompts before routing", muted(), false));
        }
        PanelView::Sessions => {
            for (idx, line) in crate::sessions::session_panel_rows().into_iter().take(6).enumerate() {
                rows.push(row(if idx == 0 { "current" } else { "session" }, line, if idx == 0 { cyan() } else { muted() }, idx == 0));
            }
        }
        PanelView::Timeline => {
            if let Some(response) = &app.last_response {
                rows.push(row("summary", crate::timeline::summary(&response.events), cyan(), true));
                for line in crate::timeline::panel_rows(&response.events, 5) {
                    rows.push(row("step", line, yellow(), false));
                }
            } else {
                rows.push(row("timeline", "No timeline yet. Run a task first.", muted(), false));
                rows.push(row("command", "/timeline opens the latest structured agent path", cyan(), false));
            }
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
            rows.push(row("keys", "/key sets API keys here; proto-cli key openai works from shell", green(), false));
            rows.push(row("report", "full report: proto-cli config", green(), false));
        }
        PanelView::Help => {
            rows.push(row("chat", "type any task or /run <task>", cyan(), true));
            rows.push(row("project", "/project chooses the folder; @ tags files into the prompt", yellow(), true));
            rows.push(row("model", "/model changes active provider/model; /key stores API keys", green(), true));
            rows.push(row("panels", "/dashboard /project /models /agents /context /sessions /timeline", magenta(), false));
            rows.push(row("loom", "/context <query> inspects the source-cited context pack", green(), false));
            rows.push(row("output", "/trace raw logs; /timeline structured path; /diff proposed changes", cyan(), false));
            rows.push(row("scroll", "mouse wheel, PageUp/PageDown, Ctrl-End", yellow(), false));
            rows.push(row("session", "/quit or Esc", muted(), false));
            rows.push(row("launch", "fullscreen TUI: proto-cli start | direct task: proto-cli run \"task\"", green(), false));
        }
    }
    rows
}

fn draw_model_activity_row(out: &mut Stdout, width: u16, y: u16, app: &TerminalApp) -> Result<()> {
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
    draw_text_segment(out, &mut x, y, width, "  ", muted(), panel_bg(), false)?;
    draw_activity_inline(out, &mut x, y, width, &app.activity, panel_bg())?;
    Ok(())
}

fn draw_panel_rows(out: &mut Stdout, width: u16, rows: &[PanelRow], max_rows: usize, start_y: u16) -> Result<()> {
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
            write_at(out, body_x, y, width.saturating_sub(body_x), line, text(), panel_bg(), row.bold)?;
            y += 1;
            used += 1;
        }
    }
    Ok(())
}

fn draw_provider_segments(out: &mut Stdout, start_x: u16, y: u16, width: u16, value: &str) -> Result<()> {
    write_at(out, start_x, y, width.saturating_sub(start_x), "", text(), panel_bg(), false)?;
    let mut x = start_x;
    for segment in value.split("  ").filter(|segment| !segment.trim().is_empty()) {
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
    draw_badge(out, &mut x, y, width, "CHAT SCROLLED", black(), yellow(), true)?;
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
        draw_badge(out, x, y, width, active, black(), activity_color(active), true)?;
        draw_text_segment(out, x, y, width, " ", muted(), background, false)?;
    }
    if let Some(spinner) = parsed.spinner.as_deref() {
        draw_text_segment(out, x, y, width, spinner, cyan(), background, true)?;
        draw_text_segment(out, x, y, width, " ", muted(), background, false)?;
    }
    draw_text_segment(out, x, y, width, &parsed.action, text(), background, false)?;
    Ok(())
}

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
    draw_text_segment(out, x, y, width, &format!(" {} ", label), fg, background, bold)
}

fn draw_text_segment(
    out: &mut Stdout,
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
    let remaining = width.saturating_sub(*x) as usize;
    let value = clip_plain(text_value, remaining);
    let used = value.chars().count() as u16;
    if used == 0 {
        return Ok(());
    }
    queue!(
        out,
        MoveTo(*x, y),
        SetForegroundColor(fg),
        SetBackgroundColor(background),
        SetAttribute(if bold { Attribute::Bold } else { Attribute::Reset }),
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
    } else if agent.contains("Registry") {
        green()
    } else if agent.contains("CLI") {
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
