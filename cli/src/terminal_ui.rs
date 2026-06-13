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
use std::collections::VecDeque;
use std::fs;
use std::io::{stdout, Stdout, Write};
use std::path::Path;
use std::time::Duration;
use tokio::time::sleep;

use crate::{call_apply_action, call_process_prompt, empty_as_unknown, load_doctor, wrap_lines, CoreResponse};

mod input;
mod diff_view;
mod model_picker;
mod state;

use diff_view::{diff_review_summary, draw_approval_modal, show_diff_modal};
use input::InputEditor;
use model_picker::handle_model_command;
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
                    KeyCode::Char('@') if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                        if crate::active_project_dir().is_none() {
                            app.panel = PanelView::Project;
                            app.refresh(None);
                            app.push(Role::Error, "@", "Choose a project with /project before tagging files.");
                        } else if let Some(path) = pick_project_file(self, app)? {
                            editor.insert_str(&format_file_tag(&path));
                        }
                    }
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
            let arg = parts.collect::<Vec<_>>().join(" ");
            if matches!(arg.trim(), "choose" | "set" | "select") {
                handle_model_command(app, terminal)?;
            } else {
                switch_panel(app, PanelView::Models, command, "Models panel pinned.");
            }
            Ok(true)
        }
        "/model" | "/provider" => {
            handle_model_command(app, terminal)?;
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
        "/project" | "/open" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_project_command(app, terminal, command, arg.trim())?;
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
            let arg = parts.collect::<Vec<_>>().join(" ");
            if let Some(response) = &app.last_response {
                let diff = response.diff.clone();
                if diff.trim().is_empty() {
                    app.push(Role::Command, "/diff", "No diff in the last response.");
                } else if arg.trim() == "raw" {
                    let raw = truncate_detail(&diff, 80);
                    app.push(Role::Command, "/diff raw", &raw);
                } else {
                    app.push(Role::Command, "/diff", &diff_review_summary(&diff));
                    show_diff_modal(terminal, app, "Diff Review", &diff)?;
                }
            } else {
                app.push(Role::Command, "/diff", "No response in this session yet.");
            }
            Ok(true)
        }
        "/trace" => {
            if let Some(response) = &app.last_response {
                if response.events.is_empty() {
                    app.push(Role::Command, "/trace", "No agent trace in the last response.");
                } else {
                    app.push(Role::Command, "/trace", &response.events.join("\n"));
                }
            } else {
                app.push(Role::Command, "/trace", "No response in this session yet.");
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
    let workspace = match crate::active_project_dir() {
        Some(path) => path.to_string_lossy().to_string(),
        None => {
            app.panel = PanelView::Project;
            app.refresh(None);
            app.push(
                Role::Error,
                "Project",
                "No project selected. Use /project to choose a folder before running a task.",
            );
            return Ok(());
        }
    };
    let prompt = query.to_string();
    let session_id = crate::project_session_id(&workspace);
    app.turn += 1;
    app.last_query = query.to_string();
    app.push(Role::User, "You", query);
    let progress_index = app.messages.len();
    app.push(Role::System, "Working", "Architect routing\nExplorer mapping workspace\nCoder preparing output");

    let mut task = tokio::task::spawn_blocking(move || call_process_prompt(prompt, workspace, session_id));
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
        if approval_prompt(terminal, app, &response)? {
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

fn handle_project_command(app: &mut TerminalApp, terminal: &mut TerminalSurface, command: &str, arg: &str) -> Result<()> {
    if matches!(arg, "clear" | "unset") {
        crate::clear_active_project()?;
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(Role::Command, command, "Project cleared. Choose a folder before running tasks.");
        return Ok(());
    }

    let selected = if arg.is_empty() || matches!(arg, "choose" | "set") {
        match project_prompt(terminal, app)? {
            Some(path) => path,
            None => {
                app.panel = PanelView::Project;
                app.refresh(None);
                app.push(Role::Command, command, "Project selection cancelled.");
                return Ok(());
            }
        }
    } else if let Some(rest) = arg.strip_prefix("set ") {
        rest.trim().to_string()
    } else if let Some(rest) = arg.strip_prefix("open ") {
        rest.trim().to_string()
    } else {
        arg.to_string()
    };

    match crate::set_active_project(&selected) {
        Ok(path) => {
            app.panel = PanelView::Project;
            app.refresh(None);
            app.push(
                Role::Command,
                command,
                &format!(
                    "Opened project: {}\nFuture starts reopen this folder. Type @ in the prompt to tag files.",
                    path.to_string_lossy()
                ),
            );
        }
        Err(err) => {
            app.panel = PanelView::Project;
            app.refresh(None);
            app.push(Role::Error, command, &format!("Could not open project: {err}"));
        }
    }
    Ok(())
}

fn project_prompt(terminal: &mut TerminalSurface, app: &TerminalApp) -> Result<Option<String>> {
    let initial = crate::active_project_dir()
        .unwrap_or_else(crate::default_launch_workspace)
        .to_string_lossy()
        .to_string();
    let history = VecDeque::new();
    let mut editor = InputEditor::with_initial(&history, &initial);
    loop {
        terminal.render(app, None)?;
        draw_input_modal(
            "Choose Project Folder",
            &[
                "Enter a folder for this session and future starts.".to_string(),
                "Use . for the launch directory, ~ for home, or an absolute path.".to_string(),
                "Enter confirms. Esc cancels.".to_string(),
            ],
            &editor,
        )?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => return Ok(Some(editor.line())),
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => editor.insert(ch),
            KeyCode::Backspace => editor.backspace(),
            KeyCode::Delete => editor.delete(),
            KeyCode::Left => editor.move_left(),
            KeyCode::Right => editor.move_right(),
            KeyCode::Home => editor.move_home(),
            KeyCode::End => editor.move_end(),
            _ => {}
        }
    }
}

fn prompt_line_modal(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    title: &str,
    rows: &[String],
    initial: &str,
) -> Result<Option<String>> {
    let history = VecDeque::new();
    let mut editor = InputEditor::with_initial(&history, initial);
    loop {
        terminal.render(app, None)?;
        draw_input_modal(title, rows, &editor)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => return Ok(Some(editor.line())),
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => editor.insert(ch),
            KeyCode::Backspace => editor.backspace(),
            KeyCode::Delete => editor.delete(),
            KeyCode::Left => editor.move_left(),
            KeyCode::Right => editor.move_right(),
            KeyCode::Home => editor.move_home(),
            KeyCode::End => editor.move_end(),
            _ => {}
        }
    }
}

fn pick_choice_modal(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    title: &str,
    rows: &[String],
    choices: &[String],
    initial: usize,
) -> Result<Option<usize>> {
    if choices.is_empty() {
        return Ok(None);
    }

    let mut filter = String::new();
    let mut selected = initial.min(choices.len().saturating_sub(1));
    loop {
        let matches = filtered_choices(choices, &filter);
        selected = selected.min(matches.len().saturating_sub(1));
        terminal.render(app, None)?;
        draw_choice_picker_modal(title, rows, &filter, &matches, selected)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => {
                if let Some((original_index, _)) = matches.get(selected) {
                    return Ok(Some(*original_index));
                }
            }
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Backspace => {
                filter.pop();
                selected = 0;
            }
            KeyCode::Up => selected = selected.saturating_sub(1),
            KeyCode::Down => {
                if selected + 1 < matches.len() {
                    selected += 1;
                }
            }
            KeyCode::PageUp => selected = selected.saturating_sub(8),
            KeyCode::PageDown => selected = (selected + 8).min(matches.len().saturating_sub(1)),
            KeyCode::Home => selected = 0,
            KeyCode::End => selected = matches.len().saturating_sub(1),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                filter.push(ch);
                selected = 0;
            }
            _ => {}
        }
    }
}

fn filtered_choices(choices: &[String], filter: &str) -> Vec<(usize, String)> {
    if filter.trim().is_empty() {
        return choices
            .iter()
            .enumerate()
            .take(200)
            .map(|(index, choice)| (index, choice.clone()))
            .collect();
    }
    let needle = filter.to_lowercase();
    choices
        .iter()
        .enumerate()
        .filter(|(_, choice)| choice.to_lowercase().contains(&needle))
        .take(200)
        .map(|(index, choice)| (index, choice.clone()))
        .collect()
}

fn pick_project_file(terminal: &mut TerminalSurface, app: &TerminalApp) -> Result<Option<String>> {
    let Some(root) = crate::active_project_dir() else {
        return Ok(None);
    };
    let files = collect_project_files(&root, 800)?;
    if files.is_empty() {
        draw_modal(
            "No Files Found",
            &[
                format!("Project: {}", root.to_string_lossy()),
                "No taggable text files were found.".to_string(),
                "Press any key to continue.".to_string(),
            ],
        )?;
        let _ = read();
        return Ok(None);
    }

    let mut filter = String::new();
    let mut selected = 0usize;
    loop {
        let matches = filtered_files(&files, &filter);
        selected = selected.min(matches.len().saturating_sub(1));
        terminal.render(app, None)?;
        draw_file_picker_modal(&root, &filter, &matches, selected)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => {
                if let Some(path) = matches.get(selected) {
                    return Ok(Some((*path).clone()));
                }
            }
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Backspace => {
                filter.pop();
                selected = 0;
            }
            KeyCode::Up => selected = selected.saturating_sub(1),
            KeyCode::Down => {
                if selected + 1 < matches.len() {
                    selected += 1;
                }
            }
            KeyCode::PageUp => selected = selected.saturating_sub(8),
            KeyCode::PageDown => selected = (selected + 8).min(matches.len().saturating_sub(1)),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                filter.push(ch);
                selected = 0;
            }
            _ => {}
        }
    }
}

fn filtered_files(files: &[String], filter: &str) -> Vec<String> {
    if filter.trim().is_empty() {
        return files.iter().take(200).cloned().collect();
    }
    let needle = filter.to_lowercase();
    files
        .iter()
        .filter(|path| path.to_lowercase().contains(&needle))
        .take(200)
        .cloned()
        .collect()
}

fn collect_project_files(root: &Path, limit: usize) -> Result<Vec<String>> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let mut entries = fs::read_dir(&dir)?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .collect::<Vec<_>>();
        entries.sort_by(|a, b| {
            let a_dir = a.is_dir();
            let b_dir = b.is_dir();
            b_dir.cmp(&a_dir).then_with(|| a.file_name().cmp(&b.file_name()))
        });

        for path in entries {
            let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
                continue;
            };
            if name.starts_with('.') || ignored_picker_name(name) {
                continue;
            }
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            if !path.is_file() || looks_binary_for_picker(&path) {
                continue;
            }
            if let Ok(relative) = path.strip_prefix(root) {
                out.push(relative.to_string_lossy().replace('\\', "/"));
                if out.len() >= limit {
                    out.sort();
                    return Ok(out);
                }
            }
        }
    }
    out.sort();
    Ok(out)
}

fn ignored_picker_name(name: &str) -> bool {
    matches!(
        name,
        ".git" | ".hg" | ".svn" | ".venv" | "__pycache__" | "node_modules" | "target" | "dist" | "build"
    )
}

fn looks_binary_for_picker(path: &Path) -> bool {
    let Some(ext) = path.extension().and_then(|ext| ext.to_str()).map(str::to_lowercase) else {
        return false;
    };
    matches!(
        ext.as_str(),
        "png"
            | "jpg"
            | "jpeg"
            | "gif"
            | "webp"
            | "pdf"
            | "zip"
            | "tar"
            | "gz"
            | "bin"
            | "so"
            | "dylib"
            | "class"
            | "pyc"
    )
}

fn format_file_tag(path: &str) -> String {
    if path.chars().any(char::is_whitespace) {
        format!("@\"{}\" ", path.replace('"', "\\\""))
    } else {
        format!("@{} ", path)
    }
}

fn approval_prompt(terminal: &mut TerminalSurface, app: &TerminalApp, response: &CoreResponse) -> Result<bool> {
    loop {
        terminal.render(app, None)?;
        draw_approval_modal(response)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return Ok(true),
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return Ok(false),
            KeyCode::Char('v') | KeyCode::Char('V') | KeyCode::Char('d') | KeyCode::Char('D')
                if !response.diff.trim().is_empty() =>
            {
                show_diff_modal(terminal, app, "Approval Diff", &response.diff)?;
            }
            _ => {}
        }
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

fn draw_modal_backdrop(_out: &mut Stdout, _width: u16, _height: u16) -> Result<()> {
    Ok(())
}

fn draw_modal_shadow(out: &mut Stdout, x: u16, y: u16, width: u16, height: u16) -> Result<()> {
    let (screen_width, screen_height) = size();
    let shadow_x = x.saturating_add(2);
    let shadow_y = y.saturating_add(1);
    let shadow_width = width.min(screen_width.saturating_sub(shadow_x));
    if shadow_width == 0 {
        return Ok(());
    }
    for row in shadow_y..shadow_y.saturating_add(height).min(screen_height) {
        write_at(out, shadow_x, row, shadow_width, "", muted(), modal_shadow_bg(), false)?;
    }
    Ok(())
}

fn modal_title(title: &str, width: u16) -> String {
    let title = format!(" {title}");
    let back = " ← Esc back ";
    let title_width = title.chars().count();
    let back_width = back.chars().count();
    if title_width + back_width >= width as usize {
        return clip_plain(&format!("{title} {back}"), width as usize);
    }
    format!("{}{}{}", title, " ".repeat(width as usize - title_width - back_width), back)
}

fn draw_modal(title: &str, rows: &[String]) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(2).saturating_div(3).clamp(42, width.saturating_sub(4));
    let modal_height = (rows.len() as u16 + 4).clamp(7, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
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
        modal_border(),
        modal_bg(),
        true,
    )?;
    out.flush()?;
    Ok(())
}

fn draw_input_modal(title: &str, rows: &[String], editor: &InputEditor) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(3).saturating_div(4).clamp(48, width.saturating_sub(4));
    let modal_height = (rows.len() as u16 + 6).clamp(8, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let inner_width = modal_width.saturating_sub(4).max(8) as usize;
    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
    for idx in 0..modal_height.saturating_sub(5) {
        let row = rows.get(idx as usize).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    let input_y = y + modal_height - 3;
    let prompt = " > ";
    let available = inner_width.saturating_sub(prompt.len()).max(8);
    let (visible, cursor) = editor.visible(available);
    write_at(&mut out, x + 1, input_y, modal_width.saturating_sub(2), "", text(), input_bg(), false)?;
    queue!(
        out,
        MoveTo(x + 2, input_y),
        SetForegroundColor(cyan()),
        SetBackgroundColor(input_bg()),
        Print(prompt),
        SetForegroundColor(text()),
        Print(clip_plain(&visible, available)),
        MoveTo(x + 2 + prompt.len() as u16 + cursor as u16, input_y),
        Show
    )?;
    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        &"=".repeat(modal_width as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    out.flush()?;
    Ok(())
}

fn draw_choice_picker_modal(
    title: &str,
    rows: &[String],
    filter: &str,
    choices: &[(usize, String)],
    selected: usize,
) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(4).saturating_div(5).clamp(52, width.saturating_sub(4));
    let modal_height = height.saturating_mul(2).saturating_div(3).clamp(11, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let info_rows = rows
        .len()
        .min(modal_height.saturating_sub(8) as usize);
    let list_start = y + 4 + info_rows as u16;
    let list_rows = modal_height
        .saturating_sub(info_rows as u16)
        .saturating_sub(5) as usize;
    let inner = modal_width.saturating_sub(4).max(10) as usize;
    let mut out = stdout();

    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
    for idx in 0..info_rows {
        let row = rows.get(idx).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx as u16 + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    let filter_y = y + 2 + info_rows as u16;
    write_at(
        &mut out,
        x,
        filter_y,
        modal_width,
        &format!(" Filter : {}", if filter.is_empty() { "(type to filter)" } else { filter }),
        cyan(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        filter_y + 1,
        modal_width,
        " Enter selects. ← Esc back. Up/Down/Page moves.",
        yellow(),
        modal_bg(),
        false,
    )?;

    let start = if selected >= list_rows {
        selected + 1 - list_rows
    } else {
        0
    };
    for row_index in 0..list_rows {
        let choice_index = start + row_index;
        let row_y = list_start + row_index as u16;
        let Some((_, choice)) = choices.get(choice_index) else {
            write_at(&mut out, x + 1, row_y, modal_width.saturating_sub(2), "", muted(), modal_list_bg(), false)?;
            continue;
        };
        let active = choice_index == selected;
        let marker = if active { ">" } else { " " };
        write_at(
            &mut out,
            x + 1,
            row_y,
            modal_width.saturating_sub(2),
            &format!("{marker} {}", clip_plain(choice, inner.saturating_sub(2))),
            if active { black() } else { text() },
            if active { modal_selection_bg() } else { modal_list_bg() },
            active,
        )?;
    }

    let footer = if choices.is_empty() {
        " No matches".to_string()
    } else {
        format!(" {} match(es)", choices.len())
    };
    write_at(&mut out, x, y + modal_height - 1, modal_width, &footer, modal_border(), modal_bg(), true)?;
    out.flush()?;
    Ok(())
}

fn draw_file_picker_modal(root: &Path, filter: &str, files: &[String], selected: usize) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(4).saturating_div(5).clamp(50, width.saturating_sub(4));
    let modal_height = height.saturating_mul(2).saturating_div(3).clamp(10, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let list_rows = modal_height.saturating_sub(7) as usize;
    let inner = modal_width.saturating_sub(4).max(10) as usize;
    let mut out = stdout();

    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title("Tag File With @", modal_width), black(), modal_border(), true)?;
    write_at(
        &mut out,
        x,
        y + 2,
        modal_width,
        &format!(" Project: {}", root.to_string_lossy()),
        muted(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 3,
        modal_width,
        &format!(" Filter : {}", if filter.is_empty() { "(type to filter)" } else { filter }),
        cyan(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 4,
        modal_width,
        " Enter inserts @file. ← Esc back. Up/Down moves.",
        yellow(),
        modal_bg(),
        false,
    )?;

    let start = if selected >= list_rows {
        selected + 1 - list_rows
    } else {
        0
    };
    for row_index in 0..list_rows {
        let file_index = start + row_index;
        let row_y = y + 5 + row_index as u16;
        let Some(path) = files.get(file_index) else {
            write_at(&mut out, x + 1, row_y, modal_width.saturating_sub(2), "", muted(), modal_list_bg(), false)?;
            continue;
        };
        let active = file_index == selected;
        let marker = if active { ">" } else { " " };
        write_at(
            &mut out,
            x + 1,
            row_y,
            modal_width.saturating_sub(2),
            &format!("{marker} {}", clip_plain(path, inner.saturating_sub(2))),
            if active { black() } else { text() },
            if active { modal_selection_bg() } else { modal_list_bg() },
            active,
        )?;
    }
    let footer = if files.is_empty() {
        " No matching files".to_string()
    } else {
        format!(" {} match(es)", files.len())
    };
    write_at(&mut out, x, y + modal_height - 1, modal_width, &footer, modal_border(), modal_bg(), true)?;
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

fn modal_shadow_bg() -> Color {
    Color::Rgb { r: 0, g: 0, b: 0 }
}

fn modal_bg() -> Color {
    Color::Rgb { r: 18, g: 23, b: 35 }
}

fn modal_list_bg() -> Color {
    Color::Rgb { r: 10, g: 14, b: 23 }
}

fn modal_selection_bg() -> Color {
    Color::Rgb { r: 111, g: 229, b: 235 }
}

fn modal_border() -> Color {
    Color::Rgb { r: 246, g: 207, b: 93 }
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
