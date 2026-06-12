use anyhow::{anyhow, Result};
use crossterm::{
    cursor::{Hide, MoveTo, Show},
    event::{read, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyModifiers, MouseEventKind},
    execute, queue,
    style::{Attribute, Color, Print, ResetColor, SetAttribute, SetBackgroundColor, SetForegroundColor},
    terminal::{
        self, disable_raw_mode, enable_raw_mode, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen, SetTitle,
    },
};
use serde_json::Value;
use std::io::{stdout, Stdout, Write};
use std::time::Duration;
use tokio::time::sleep;

use crate::{call_apply_action, call_process_prompt, empty_as_unknown, load_doctor, wrap_lines, CoreResponse};

mod input;
mod state;

use input::InputEditor;
use state::{PanelView, Role, TerminalApp, TerminalMessage};

const HEADER_ROWS: u16 = 9;
const INPUT_ROWS: u16 = 4;
const WHEEL_LINES: usize = 5;

pub(crate) async fn interactive() -> Result<()> {
    let mut terminal = TerminalSurface::enter()?;
    let mut app = TerminalApp::new();

    loop {
        terminal.render(&app, None)?;
        let Some(input) = terminal.read_input(&mut app)? else {
            break;
        };
        let input = input.trim();
        if input.is_empty() {
            continue;
        }
        app.remember(input);
        if input.starts_with('/') {
            if !handle_command(&mut app, &mut terminal, input).await? {
                break;
            }
        } else {
            run_task(&mut app, &mut terminal, input).await?;
        }
    }

    terminal.leave()?;
    println!("Session restored to your shell.");
    Ok(())
}

struct TerminalSurface {
    active: bool,
}

impl TerminalSurface {
    fn enter() -> Result<Self> {
        enable_raw_mode()?;
        let enter_result = execute!(
            stdout(),
            Clear(ClearType::Purge),
            EnterAlternateScreen,
            EnableMouseCapture,
            Hide,
            SetTitle("ProtoAgent Terminal"),
            Clear(ClearType::All),
            Clear(ClearType::Purge)
        );
        if let Err(err) = enter_result {
            let _ = disable_raw_mode();
            return Err(err.into());
        }
        Ok(Self { active: true })
    }

    fn leave(&mut self) -> Result<()> {
        if self.active {
            let leave_result = execute!(
                stdout(),
                ResetColor,
                Show,
                DisableMouseCapture,
                Clear(ClearType::All),
                LeaveAlternateScreen
            );
            let raw_result = disable_raw_mode();
            self.active = false;
            leave_result?;
            raw_result?;
        }
        Ok(())
    }

    fn render(&mut self, app: &TerminalApp, editor: Option<&InputEditor>) -> Result<()> {
        let (width, height) = size();
        let mut out = stdout();
        queue!(out, Hide)?;
        draw_header(&mut out, width, app)?;
        draw_transcript(&mut out, width, height, app)?;
        let cursor = draw_input(&mut out, width, height, app, editor)?;
        queue!(out, MoveTo(cursor.0, cursor.1), Show, ResetColor)?;
        out.flush()?;
        Ok(())
    }

    fn read_input(&mut self, app: &mut TerminalApp) -> Result<Option<String>> {
        let mut editor = InputEditor::new(&app.input_history);
        loop {
            self.render(app, Some(&editor))?;
            match read()? {
                Event::Key(key) => match key.code {
                    KeyCode::Enter => return Ok(Some(editor.line())),
                    KeyCode::Esc => return Ok(None),
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
                    KeyCode::Char('d') if key.modifiers.contains(KeyModifiers::CONTROL) && editor.is_empty() => {
                        return Ok(None);
                    }
                    KeyCode::PageUp => app.scroll_up(chat_page_size()),
                    KeyCode::PageDown => app.scroll_down(chat_page_size()),
                    KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => app.scroll_up(100_000),
                    KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => app.jump_to_bottom(),
                    KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => editor.insert(ch),
                    KeyCode::Backspace => editor.backspace(),
                    KeyCode::Delete => editor.delete(),
                    KeyCode::Left => editor.move_left(),
                    KeyCode::Right => editor.move_right(),
                    KeyCode::Home => editor.move_home(),
                    KeyCode::End => editor.move_end(),
                    KeyCode::Up => editor.history_prev(),
                    KeyCode::Down => editor.history_next(),
                    KeyCode::Tab => editor.insert_str("  "),
                    _ => {}
                },
                Event::Mouse(mouse) => match mouse.kind {
                    MouseEventKind::ScrollUp => app.scroll_up(WHEEL_LINES),
                    MouseEventKind::ScrollDown => app.scroll_down(WHEEL_LINES),
                    _ => {}
                },
                Event::Resize(_, _) => {}
                _ => {}
            }
        }
    }
}

fn chat_page_size() -> usize {
    let (_, height) = size();
    height
        .saturating_sub(HEADER_ROWS + INPUT_ROWS)
        .saturating_sub(1)
        .max(1) as usize
}

impl Drop for TerminalSurface {
    fn drop(&mut self) {
        let _ = self.leave();
    }
}

async fn handle_command(app: &mut TerminalApp, terminal: &mut TerminalSurface, input: &str) -> Result<bool> {
    let mut parts = input.split_whitespace();
    let command = parts.next().unwrap_or("");
    match command {
        "/quit" | "/exit" => Ok(false),
        "/clear" => {
            app.messages.clear();
            app.push(Role::Command, "/clear", "Transcript cleared.");
            Ok(true)
        }
        "/dashboard" | "/dash" | "/status" => {
            switch_panel(app, PanelView::Dashboard, command, "Dashboard panel pinned.");
            Ok(true)
        }
        "/models" => {
            switch_panel(app, PanelView::Models, command, "Models panel pinned.");
            Ok(true)
        }
        "/agents" => {
            switch_panel(app, PanelView::Agents, command, "Agents panel pinned.");
            Ok(true)
        }
        "/config" => {
            switch_panel(app, PanelView::Config, command, "Config panel pinned.");
            Ok(true)
        }
        "/help" | "/menu" => {
            switch_panel(app, PanelView::Help, command, "Help panel pinned.");
            Ok(true)
        }
        "/check" => {
            app.activity = "checking runtime".to_string();
            terminal.render(app, None)?;
            match load_doctor() {
                Ok(report) => {
                    app.panel = PanelView::Check;
                    app.refresh(Some(&report));
                    app.push(Role::Command, "/check", "Runtime check refreshed.");
                }
                Err(err) => app.push(Role::Error, "/check", &format!("Check failed: {err}")),
            }
            app.activity = "idle".to_string();
            Ok(true)
        }
        "/last" => {
            if let Some(response) = app.last_response.clone() {
                app.push_response(&response);
            } else {
                app.push(Role::Command, "/last", "No response in this session yet.");
            }
            Ok(true)
        }
        "/diff" => {
            if let Some(response) = &app.last_response {
                if response.diff.trim().is_empty() {
                    app.push(Role::Command, "/diff", "No diff in the last response.");
                } else {
                    let diff = truncate_detail(&response.diff, 40);
                    app.push(Role::Command, "/diff", &diff);
                }
            } else {
                app.push(Role::Command, "/diff", "No response in this session yet.");
            }
            Ok(true)
        }
        "/run" => {
            let query = parts.collect::<Vec<_>>().join(" ");
            if query.trim().is_empty() {
                app.push(Role::Error, "/run", "Usage: /run your task");
            } else {
                run_task(app, terminal, query.trim()).await?;
            }
            Ok(true)
        }
        _ => {
            app.push(Role::Error, command, "Unknown command. Use /help.");
            Ok(true)
        }
    }
}

fn switch_panel(app: &mut TerminalApp, panel: PanelView, command: &str, body: &str) {
    app.panel = panel;
    app.refresh(None);
    app.push(Role::Command, command, body);
}

async fn run_task(app: &mut TerminalApp, terminal: &mut TerminalSurface, query: &str) -> Result<()> {
    app.turn += 1;
    app.last_query = query.to_string();
    app.push(Role::User, "You", query);
    let progress_index = app.messages.len();
    app.push(Role::System, "Working", "Architect routing\nExplorer mapping workspace\nCoder preparing output");

    let prompt = query.to_string();
    let workspace = crate::workspace_dir_string();
    let mut task = tokio::task::spawn_blocking(move || call_process_prompt(prompt, workspace));
    let spinner = ["|", "/", "-", "\\"];
    let mut tick = 0usize;

    let json = loop {
        tokio::select! {
            result = &mut task => {
                let raw = result?;
                break raw.map_err(|err| anyhow!("Python core error: {err:?}"))?;
            }
            _ = sleep(Duration::from_millis(90)) => {
                let phase = match tick % 3 {
                    0 => "Architect routing",
                    1 => "Explorer mapping",
                    _ => "Coder composing",
                };
                app.activity = format!("{} {}", spinner[tick % spinner.len()], phase);
                if let Some(message) = app.messages.get_mut(progress_index) {
                    message.body = format!("{} {}\nstatus stays pinned above; response lands below", spinner[tick % spinner.len()], phase);
                }
                terminal.render(app, None)?;
                tick += 1;
            }
        }
    };

    let response: CoreResponse = serde_json::from_str(&json)?;
    app.activity = format!("completed in {} ms", response.elapsed_ms);
    if let Some(message) = app.messages.get_mut(progress_index) {
        message.label = "Completed".to_string();
        message.body = format!(
            "{} / {} | {} ms",
            empty_as_unknown(&response.provider),
            if response.model.is_empty() { "not selected" } else { response.model.as_str() },
            response.elapsed_ms
        );
    }
    app.push_response(&response);
    app.last_response = Some(response.clone());
    app.refresh(None);

    if response.requires_approval || !response.actions.is_empty() {
        terminal.render(app, None)?;
        if approval_prompt(terminal, &response)? {
            match apply_actions(&response.actions, &response.workspace) {
                Ok(applied) => app.push(Role::System, "Approval", &applied),
                Err(err) => app.push(Role::Error, "Approval failed", &err.to_string()),
            }
        } else {
            app.push(Role::System, "Approval", "Denied. No files changed.");
        }
    }

    Ok(())
}

fn approval_prompt(terminal: &mut TerminalSurface, response: &CoreResponse) -> Result<bool> {
    loop {
        draw_modal(
            "Approval required",
            &[
                "The agent proposed side effects.".to_string(),
                format!("Workspace: {}", empty_as_unknown(&response.workspace)),
                format!("Actions: {}", response.actions.len()),
                "Press y to apply, n or Esc to deny.".to_string(),
            ],
        )?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return Ok(true),
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return Ok(false),
            _ => terminal.render_placeholder()?,
        }
    }
}

impl TerminalSurface {
    fn render_placeholder(&mut self) -> Result<()> {
        Ok(())
    }
}

fn apply_actions(actions: &[Value], workspace: &str) -> Result<String> {
    let mut applied = Vec::new();
    for action in actions {
        let result = call_apply_action(serde_json::to_string(action)?, workspace.to_string())
            .map_err(|err| anyhow!("Python apply error: {err:?}"))?;
        let parsed: Value = serde_json::from_str(&result)?;
        let path = parsed.get("path").and_then(Value::as_str).unwrap_or("(unknown path)");
        applied.push(format!("applied: {path}"));
    }
    Ok(applied.join("\n"))
}

fn draw_header(out: &mut Stdout, width: u16, app: &TerminalApp) -> Result<()> {
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
            rows.push(row("work", &app.status.workspace, magenta(), true));
            rows.push(row("models", &app.status.model_summary, cyan(), false));
            rows.push(row("agents", "Architect routes | Explorer reads | Coder drafts | human approves", yellow(), false));
            rows.push(row(
                "last",
                if app.last_query.is_empty() { "none".to_string() } else { app.last_query.clone() },
                muted(),
                false,
            ));
            rows.push(row("mode", "fixed panels, fluid transcript, bottom input", green(), false));
        }
        PanelView::Models => {
            rows.push(row("active", format!("{} / {}", app.status.provider, app.status.model), cyan(), true));
            rows.push(row("inventory", &app.status.model_summary, magenta(), false));
            rows.push(row("providers", &app.status.provider_summary, yellow(), false));
            rows.push(row("config", &app.status.config_path, muted(), false));
            rows.push(row("tip", "use the web app for richer provider inspection", green(), false));
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
            rows.push(row("panels", "/dashboard /models /agents /check /config /help", magenta(), false));
            rows.push(row("scroll", "mouse wheel, PageUp/PageDown, Ctrl-End", yellow(), false));
            rows.push(row("session", "/quit or Esc", muted(), false));
            rows.push(row("launch", "web app: proto-cli start | terminal app: proto-cli tui", green(), false));
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

fn draw_transcript(out: &mut Stdout, width: u16, height: u16, app: &TerminalApp) -> Result<()> {
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
        append_wrapped_render_line(
            lines,
            "  | ",
            &format!("details: {} (use /diff for proposed diff)", labels),
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

fn draw_input(
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
            app.activity,
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

fn draw_modal(title: &str, rows: &[String]) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(2).saturating_div(3).clamp(42, width.saturating_sub(4));
    let modal_height = (rows.len() as u16 + 4).clamp(7, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let mut out = stdout();
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), yellow(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &format!(" {title}"), yellow(), modal_bg(), true)?;
    for idx in 0..modal_height.saturating_sub(4) {
        let row = rows.get(idx as usize).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        &"=".repeat(modal_width as usize),
        yellow(),
        modal_bg(),
        true,
    )?;
    out.flush()?;
    Ok(())
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

fn truncate_detail(text: &str, max_lines: usize) -> String {
    let mut lines = text.lines().take(max_lines).collect::<Vec<_>>().join("\n");
    if text.lines().count() > max_lines {
        lines.push_str("\n...truncated");
    }
    lines
}

fn size() -> (u16, u16) {
    terminal::size().unwrap_or((100, 30))
}

fn write_line(
    out: &mut Stdout,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    write_at(out, 0, y, width, text_value, fg, background, bold)
}

fn write_at(
    out: &mut Stdout,
    x: u16,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    let mut value = clip_plain(text_value, width as usize);
    let len = value.chars().count();
    if len < width as usize {
        value.push_str(&" ".repeat(width as usize - len));
    }
    queue!(
        out,
        MoveTo(x, y),
        SetForegroundColor(fg),
        SetBackgroundColor(background),
        SetAttribute(if bold { Attribute::Bold } else { Attribute::Reset }),
        Print(value),
        SetAttribute(Attribute::Reset)
    )?;
    Ok(())
}

fn clip_plain(text: &str, width: usize) -> String {
    text.chars().take(width).collect()
}

fn role_color(role: Role) -> Color {
    match role {
        Role::User => magenta(),
        Role::Assistant => cyan(),
        Role::Command => muted(),
        Role::System => muted(),
        Role::Error => red(),
    }
}

fn bg() -> Color {
    Color::Rgb { r: 7, g: 9, b: 16 }
}

fn panel_bg() -> Color {
    Color::Rgb { r: 14, g: 16, b: 24 }
}

fn surface_bg() -> Color {
    Color::Rgb { r: 32, g: 38, b: 55 }
}

fn input_bg() -> Color {
    Color::Rgb { r: 18, g: 21, b: 30 }
}

fn modal_bg() -> Color {
    Color::Rgb { r: 28, g: 24, b: 42 }
}

fn text() -> Color {
    Color::Rgb { r: 238, g: 243, b: 248 }
}

fn muted() -> Color {
    Color::Rgb { r: 154, g: 167, b: 184 }
}

fn cyan() -> Color {
    Color::Rgb { r: 88, g: 220, b: 233 }
}

fn magenta() -> Color {
    Color::Rgb { r: 224, g: 86, b: 216 }
}

fn yellow() -> Color {
    Color::Rgb { r: 243, g: 198, b: 91 }
}

fn green() -> Color {
    Color::Rgb { r: 116, g: 223, b: 159 }
}

fn red() -> Color {
    Color::Rgb { r: 255, g: 122, b: 138 }
}

fn black() -> Color {
    Color::Black
}
