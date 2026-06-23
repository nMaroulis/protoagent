use anyhow::{anyhow, Result};
use crossterm::event::{poll, read, Event, KeyCode, KeyModifiers};
use std::time::Duration;
use tokio::time::sleep;

use crate::{
    call_process_prompt_with_progress, compact_context_history, context_pack_text, context_status_text,
    context_window_text, empty_as_unknown, load_doctor, load_inventory_with_validation,
    progress::{format_live_progress, progress_activity, ProgressBatch, ProgressFile}, refresh_context_text,
    reset_context_history, CoreResponse,
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
use model_picker::{handle_key_command, handle_model_command};
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
            switch_panel(app, PanelView::Agents, command, "Agents panel pinned.");
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
                app.push(Role::Command, command, &crate::timeline::format_timeline(&response.events, 24));
            } else {
                app.push(Role::Command, command, "No timeline yet. Run a task first.");
            }
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
    terminal.render(app, None)?;
    match load_inventory_with_validation(true) {
        Ok(inventory) => app.apply_model_inventory(&inventory),
        Err(err) => {
            app.models_loading = false;
            app.push(Role::Error, command, &format!("Could not load model inventory: {err}"));
        }
    }
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
    let session_id = crate::project_session_id(&workspace);
    app.turn += 1;
    app.context_usage.reset();
    app.last_query = query.to_string();
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
    let canceled = response.status == "canceled";
    app.activity = format!("{} in {} ms", if canceled { "canceled" } else { "completed" }, response.elapsed_ms);
    if let Some(message) = app.messages.get_mut(progress_index) {
        message.label = if canceled { "Canceled" } else { "Completed" }.to_string();
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
