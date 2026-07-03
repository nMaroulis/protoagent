use anyhow::{anyhow, Result};
use crossterm::event::{poll, read, Event, KeyCode, KeyModifiers};
use std::time::Duration;
use tokio::time::sleep;

use crate::{
    agent_profile_text, call_process_prompt_with_progress, compact_context_history, component_version_text,
    context_history_text, context_memory_text, context_pack_text, context_status_text, context_window_text,
    empty_as_unknown, help_availability_text, help_question_text, load_doctor,
    progress::{format_live_progress, progress_activity, ProgressBatch, ProgressFile}, refresh_context_text,
    reset_context_history, set_context_memory_text, CoreResponse,
};

mod approval;
mod diff_view;
mod input;
mod modal;
mod model_picker;
mod project;
mod render;
mod state;
mod surface;
mod theme;

use approval::approval_prompt;
use diff_view::{diff_review_summary, show_diff_modal};
use modal::pick_choice_modal;
use model_picker::{handle_key_command, handle_model_command, load_inventory_with_feedback};
use project::handle_project_command;
use render::truncate_detail;
use state::{PanelView, Role, TerminalApp};
use surface::TerminalSurface;

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
                switch_model_panel(app, terminal, command, "Models panel pinned.")?;
            }
            Ok(true)
        }
        "/model" | "/provider" => {
            handle_model_command(app, terminal)?;
            Ok(true)
        }
        "/key" => {
            let provider = parts.next();
            handle_key_command(app, terminal, provider)?;
            Ok(true)
        }
        "/agents" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_agents_command(app, command, arg.trim())?;
            Ok(true)
        }
        "/context" | "/loom" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_context_command(app, terminal, command, arg.trim())?;
            Ok(true)
        }
        "/index" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_index_command(app, terminal, command, arg.trim())?;
            Ok(true)
        }
        "/sessions" | "/session" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_session_command(app, terminal, command, arg.trim())?;
            Ok(true)
        }
        "/timeline" | "/flow" => {
            app.panel = PanelView::Timeline;
            app.refresh(None);
            if let Some(response) = &app.last_response {
                app.push(
                    Role::Command,
                    command,
                    &crate::timeline::format_timeline_from_run_events(
                        &response.run_events,
                        &response.events,
                        24,
                    ),
                );
            } else {
                app.push(Role::Command, command, "No timeline yet. Run a task first.");
            }
            Ok(true)
        }
        "/config" => {
            switch_panel(app, PanelView::Config, command, "Config panel pinned.");
            Ok(true)
        }
        "/version" | "/versions" => {
            match component_version_text() {
                Ok(text) => {
                    app.panel = PanelView::Versions;
                    app.version_rows = text.lines().map(str::to_string).collect();
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &format!("Version check failed: {err}")),
            }
            Ok(true)
        }
        "/project" | "/open" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_project_command(app, terminal, command, arg.trim())?;
            Ok(true)
        }
        "/help" | "/menu" => {
            let arg = parts.collect::<Vec<_>>().join(" ");
            handle_help_command(app, terminal, command, arg.trim()).await?;
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
            let diff = latest_diff_preview(app);
            if !diff.trim().is_empty() {
                if arg.trim() == "raw" {
                    let raw = truncate_detail(&diff, 80);
                    app.push(Role::Command, "/diff raw", &raw);
                } else {
                    app.push(Role::Command, "/diff", &diff_review_summary(&diff));
                    show_diff_modal(terminal, app, "Diff Review", &diff)?;
                }
            } else if app.last_response.is_some() {
                app.push(Role::Command, "/diff", "No proposed diff is available from the last run.");
            } else {
                app.push(Role::Command, "/diff", "No response in this session yet.");
            }
            Ok(true)
        }
        "/trace" => {
            if let Some(response) = &app.last_response {
                if response.run_events.is_empty() && response.events.is_empty() {
                    app.push(Role::Command, "/trace", "No agent trace in the last response.");
                } else {
                    app.push(
                        Role::Command,
                        "/trace",
                        &crate::timeline::format_run_trace(&response.run_events, &response.events),
                    );
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

async fn handle_help_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    arg: &str,
) -> Result<()> {
    app.panel = PanelView::Help;
    app.refresh(None);
    if arg.is_empty() {
        app.push(Role::Command, command, &help_availability_text());
        return Ok(());
    }

    let question = arg.to_string();
    let answer = ask_guide_with_feedback(app, terminal, question).await?;
    app.push(Role::Command, command, &answer);
    Ok(())
}

async fn ask_guide_with_feedback(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    question: String,
) -> Result<String> {
    let mut tick = 0usize;
    app.activity = guide_activity(tick);
    let progress_index = app.messages.len();
    app.push(Role::System, "Guide", &guide_loading_text(tick));
    terminal.render(app, None)?;

    let mut task = tokio::task::spawn_blocking(move || help_question_text(&question));
    loop {
        tokio::select! {
            result = &mut task => {
                app.activity = "idle".to_string();
                if let Some(message) = app.messages.get_mut(progress_index) {
                    message.label = "Guide".to_string();
                    message.body = "Guide answered. Response is below.".to_string();
                }
                terminal.render(app, None)?;
                return result?;
            }
            _ = sleep(Duration::from_millis(120)) => {
                tick = tick.wrapping_add(1);
                app.activity = guide_activity(tick);
                if let Some(message) = app.messages.get_mut(progress_index) {
                    message.body = guide_loading_text(tick);
                }
                terminal.render(app, None)?;
            }
        }
    }
}

fn guide_activity(tick: usize) -> String {
    let spinner = ["|", "/", "-", "\\"];
    format!("{} asking Guide", spinner[tick % spinner.len()])
}

fn guide_loading_text(tick: usize) -> String {
    let spinner = ["|", "/", "-", "\\"];
    format!(
        "{} Asking Guide about ProtoAgent help...\nUsing the active model with redacted current settings.",
        spinner[tick % spinner.len()]
    )
}

fn switch_panel(app: &mut TerminalApp, panel: PanelView, command: &str, body: &str) {
    app.panel = panel;
    app.refresh(None);
    app.push(Role::Command, command, body);
}

fn handle_agents_command(app: &mut TerminalApp, command: &str, arg: &str) -> Result<()> {
    let mut parts = arg.split_whitespace();
    let first = parts.next();
    if first.is_none() || matches!(first, Some("status")) {
        app.panel = PanelView::Agents;
        app.refresh(None);
        let body = agents_panel_pinned_text(app);
        app.push(Role::Command, command, &body);
        return Ok(());
    }
    let value = match first {
        Some("profile" | "prompt" | "mode" | "reasoning") => parts.next().map(str::to_string),
        Some(value) if is_prompt_profile_value(value) => Some(value.to_string()),
        Some(_) => {
            app.push(
                Role::Error,
                command,
                "Usage: /agents profile [auto|small|medium|large|api]",
            );
            return Ok(());
        }
        None => None,
    };
    if parts.next().is_some() {
        app.push(
            Role::Error,
            command,
            "Usage: /agents profile [auto|small|medium|large|api]",
        );
        return Ok(());
    }
    match agent_profile_text(value) {
        Ok(text) => {
            app.panel = PanelView::Agents;
            app.refresh(None);
            app.push(Role::Command, command, &text);
        }
        Err(err) => app.push(Role::Error, command, &err.to_string()),
    }
    Ok(())
}

fn agents_panel_pinned_text(app: &TerminalApp) -> String {
    format!(
        "Agents panel pinned. Runtime kernel uses a RunContract, stateful Architect, stateless Explorer/Coder workers, and ProtoLink policy gates. Current prompt profile: {}. Change it with /agents profile auto|small|medium|large|api.",
        empty_as_unknown(&app.status.prompt_profile)
    )
}

fn is_prompt_profile_value(value: &str) -> bool {
    matches!(
        value,
        "auto"
            | "automatic"
            | "default"
            | "small"
            | "tiny"
            | "medium"
            | "mid"
            | "balanced"
            | "large"
            | "big"
            | "api"
            | "api-level"
            | "api-grade"
            | "frontier"
            | "cloud"
    )
}

fn switch_model_panel(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    body: &str,
) -> Result<()> {
    app.panel = PanelView::Models;
    app.models_loading = true;
    app.activity = "loading model inventory".to_string();
    app.push(Role::Command, command, body);
    let _ = load_inventory_with_feedback(
        app,
        terminal,
        command,
        "Loading Models",
        "Scanning configured model sources before opening the panel.",
    )?;
    app.activity = "idle".to_string();
    Ok(())
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
    let session_id = crate::context_session_id(&workspace);
    app.turn += 1;
    app.context_usage.reset();
    app.last_query = query.to_string();
    app.last_diff_preview.clear();
    app.push(Role::User, "You", query);
    let progress_index = app.messages.len();
    app.push(Role::System, "Working", &format_live_progress(&[]));

    let mut progress_file = ProgressFile::new(app.turn);
    let progress_path = progress_file.path_string();
    let mut progress_events = Vec::new();
    let mut task = tokio::task::spawn_blocking(move || {
        call_process_prompt_with_progress(prompt, workspace, session_id, progress_path)
    });
    let mut tick = 0usize;
    let mut cancellation_requested = false;

    let json_result: Result<String> = loop {
        tokio::select! {
            result = &mut task => {
                let raw = match result {
                    Ok(raw) => raw,
                    Err(err) => {
                        progress_file.cleanup();
                        return Err(err.into());
                    }
                };
                let _ = poll_task_cancellation(true)?;
                ingest_progress(app, &mut progress_events, progress_file.read_new_batch());
                break raw.map_err(|err| anyhow!("Python core error: {err:?}"));
            }
            _ = sleep(Duration::from_millis(120)) => {
                ingest_progress(app, &mut progress_events, progress_file.read_new_batch());
                if let Some(approval) = progress_file.take_approval_request() {
                    if !approval.diff.trim().is_empty() {
                        app.last_diff_preview = approval.diff.clone();
                    }
                    terminal.render(app, None)?;
                    let approved = approval_prompt(terminal, app, &approval)?;
                    progress_file.decide(&approval, approved)?;
                    let decision = if approved { "approved" } else { "denied" };
                    progress_events.push(format!(
                        "Approval {decision}: {}.",
                        if approval.description.is_empty() { approval.action_name } else { approval.description }
                    ));
                    app.push(
                        Role::System,
                        "Policy decision",
                        &format!("{} was {decision} before execution.", approval.target),
                    );
                }
                if poll_task_cancellation(cancellation_requested)? {
                    progress_file.request_cancel("Canceled from the ProtoAgent TUI")?;
                    cancellation_requested = true;
                    progress_events.push("Cancellation requested from the TUI.".to_string());
                }
                app.activity = progress_activity(&progress_events, tick);
                if let Some(message) = app.messages.get_mut(progress_index) {
                    let task_hint = if cancellation_requested {
                        "Cancellation requested. Repeated Esc is ignored while the task winds down."
                    } else {
                        "Esc or Ctrl-C cancels this task."
                    };
                    message.body = format!(
                        "{}\n\n{task_hint}",
                        format_live_progress(&progress_events),
                    );
                }
                terminal.render(app, None)?;
                tick += 1;
            }
        }
    };
    ingest_progress(app, &mut progress_events, progress_file.read_new_batch());
    progress_file.cleanup();
    if cancellation_requested {
        let _ = poll_task_cancellation(true)?;
        terminal.suppress_exit_escape();
    }
    let json = json_result?;

    let response: CoreResponse = serde_json::from_str(&json)?;
    app.context_usage.observe_run_events(&response.run_events);
    if let Err(err) = crate::sessions::record_turn(query, &response) {
        app.push(Role::Error, "Session history", &err.to_string());
    }
    let terminal_status = match response.status.as_str() {
        "blocked" => "blocked",
        "canceled" => "canceled",
        "incomplete" => "incomplete",
        _ => "completed",
    };
    app.activity = format!("{} in {} ms", terminal_status, response.elapsed_ms);
    if let Some(message) = app.messages.get_mut(progress_index) {
        message.label = match response.status.as_str() {
            "blocked" => "Blocked",
            "canceled" => "Canceled",
            "incomplete" => "Incomplete",
            _ => "Completed",
        }
        .to_string();
        message.body = format!(
            "{} / {} | {} ms\n{} live event(s); /trace shows the full agent run",
            empty_as_unknown(&response.provider),
            if response.model.is_empty() { "not selected" } else { response.model.as_str() },
            response.elapsed_ms,
            progress_events.len().max(response.events.len())
        );
    }
    app.push_response(&response);
    app.last_response = Some(response.clone());
    if app.panel == PanelView::Models {
        app.refresh_models();
    } else {
        app.refresh(None);
    }

    Ok(())
}

fn latest_diff_preview(app: &TerminalApp) -> String {
    app.last_response
        .as_ref()
        .map(|response| response.diff.trim())
        .filter(|diff| !diff.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| app.last_diff_preview.clone())
}

fn ingest_progress(app: &mut TerminalApp, events: &mut Vec<String>, batch: ProgressBatch) {
    events.extend(batch.events);
    for sample in batch.context_samples {
        app.context_usage.observe(sample);
    }
}

fn poll_task_cancellation(already_requested: bool) -> Result<bool> {
    let mut request_cancellation = false;
    while poll(Duration::from_millis(0))? {
        let Event::Key(key) = read()? else {
            continue;
        };
        let is_cancel = matches!(key.code, KeyCode::Esc)
            || matches!(key.code, KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL));
        if is_cancel && !already_requested && !request_cancellation {
            request_cancellation = true;
        }
    }
    Ok(request_cancellation)
}

fn handle_session_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    arg: &str,
) -> Result<()> {
    if matches!(arg, "choose" | "open" | "resume") {
        choose_session(app, terminal, command)?;
        return Ok(());
    }
    if let Some(name) = arg.strip_prefix("rename ") {
        let name = name.trim();
        if name.is_empty() {
            app.push(Role::Error, command, "Usage: /session rename NAME");
        } else {
            match crate::sessions::rename_current(name) {
                Ok(()) => {
                    app.panel = PanelView::Sessions;
                    app.refresh(None);
                    app.push(Role::Command, command, &format!("Renamed current session to {name}."));
                }
                Err(err) => app.push(Role::Error, command, &format!("Could not rename session: {err}")),
            }
        }
        return Ok(());
    }

    app.panel = PanelView::Sessions;
    app.refresh(None);
    app.push(Role::Command, command, &crate::sessions::session_panel_rows().join("\n"));
    Ok(())
}

fn handle_context_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    arg: &str,
) -> Result<()> {
    let mut parts = arg.split_whitespace();
    match parts.next() {
        Some("window") => {
            let value = parts.next().map(str::to_string);
            if parts.next().is_some() {
                app.push(Role::Error, command, "Usage: /context window [16k|auto]");
                return Ok(());
            }
            match context_window_text(value.clone()) {
                Ok(text) => {
                    if value.is_some() {
                        app.context_usage.reset();
                    }
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("compact") => {
            let values = parts.collect::<Vec<_>>();
            if values.len() > 2 {
                app.push(
                    Role::Error,
                    command,
                    "Usage: /context compact [recent|tokens|summary] [limit]",
                );
                return Ok(());
            }
            match compact_context_history(&values) {
                Ok(text) => {
                    app.context_usage.reset();
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("history") => {
            match context_history_text() {
                Ok(text) => {
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("reset") => {
            match reset_context_history() {
                Ok(text) => {
                    app.context_usage.reset();
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("on") => {
            match set_context_memory_text(true) {
                Ok(text) => {
                    app.context_usage.reset();
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("off") => {
            match set_context_memory_text(false) {
                Ok(text) => {
                    app.context_usage.reset();
                    app.panel = PanelView::Context;
                    app.refresh(None);
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
            return Ok(());
        }
        Some("memory") => {
            app.panel = PanelView::Context;
            app.refresh(None);
            app.push(Role::Command, command, &context_memory_text());
            return Ok(());
        }
        _ => {}
    }
    let Some(workspace) = crate::active_project_dir().map(|path| path.to_string_lossy().to_string()) else {
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(Role::Error, command, "Choose a project with /project before using Context Loom.");
        return Ok(());
    };
    app.panel = PanelView::Context;
    app.activity = if arg.is_empty() {
        "checking Context Loom".to_string()
    } else {
        "weaving Context Loom pack".to_string()
    };
    terminal.render(app, None)?;
    let result = if arg.is_empty() {
        context_status_text(workspace)
    } else {
        context_pack_text(arg.to_string(), workspace)
    };
    match result {
        Ok(text) => {
            app.refresh(None);
            app.push(Role::Command, command, &text);
        }
        Err(err) => app.push(Role::Error, command, &format!("Context Loom failed: {err}")),
    }
    app.activity = "idle".to_string();
    Ok(())
}

fn handle_index_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    arg: &str,
) -> Result<()> {
    let Some(workspace) = crate::active_project_dir().map(|path| path.to_string_lossy().to_string()) else {
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(Role::Error, command, "Choose a project with /project before refreshing Context Loom.");
        return Ok(());
    };
    app.panel = PanelView::Context;
    app.activity = "refreshing Context Loom index".to_string();
    terminal.render(app, None)?;
    let result = if matches!(arg, "" | "refresh" | "rebuild") {
        refresh_context_text(workspace)
    } else {
        Err(anyhow!("Usage: /index refresh"))
    };
    match result {
        Ok(text) => {
            app.refresh(None);
            app.push(Role::Command, command, &text);
        }
        Err(err) => app.push(Role::Error, command, &format!("Index refresh failed: {err}")),
    }
    app.activity = "idle".to_string();
    Ok(())
}

fn choose_session(app: &mut TerminalApp, terminal: &mut TerminalSurface, command: &str) -> Result<()> {
    let sessions = crate::sessions::recent_sessions();
    if sessions.is_empty() {
        app.panel = PanelView::Sessions;
        app.refresh(None);
        app.push(Role::Command, command, "No saved sessions yet.");
        return Ok(());
    }
    let choices = sessions
        .iter()
        .map(|session| {
            format!(
                "{} | {} turn(s) | {}",
                session.name, session.turns, session.workspace
            )
        })
        .collect::<Vec<_>>();
    match pick_choice_modal(
        terminal,
        app,
        "Resume Session",
        &["Choose a saved project session to reopen its workspace.".to_string()],
        &choices,
        0,
    )? {
        Some(index) => {
            let selected = &sessions[index];
            match crate::set_active_project(&selected.workspace) {
                Ok(_) => {
                    app.panel = PanelView::Sessions;
                    app.refresh(None);
                    app.push(
                        Role::Command,
                        command,
                        &format!(
                            "Resumed session: {}\nWorkspace: {}\nProtoLink memory key: {}",
                            selected.name, selected.workspace, selected.id
                        ),
                    );
                }
                Err(err) => app.push(Role::Error, command, &format!("Could not resume session: {err}")),
            }
        }
        None => app.push(Role::Command, command, "Session selection cancelled."),
    }
    Ok(())
}
