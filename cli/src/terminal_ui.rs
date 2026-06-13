use anyhow::{anyhow, Result};
use std::time::Duration;
use tokio::time::sleep;

use crate::{
    call_process_prompt_with_progress, empty_as_unknown, load_doctor,
    progress::{format_live_progress, progress_activity, ProgressFile},
    CoreResponse,
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

use approval::{apply_actions, approval_prompt};
use diff_view::{diff_review_summary, show_diff_modal};
use model_picker::handle_model_command;
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
    app.push(Role::System, "Working", &format_live_progress(&[]));

    let mut progress_file = ProgressFile::new(app.turn);
    let progress_path = progress_file.path_string();
    let mut progress_events = Vec::new();
    let mut task = tokio::task::spawn_blocking(move || {
        call_process_prompt_with_progress(prompt, workspace, session_id, progress_path)
    });
    let mut tick = 0usize;

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
                progress_events.extend(progress_file.read_new());
                break raw.map_err(|err| anyhow!("Python core error: {err:?}"));
            }
            _ = sleep(Duration::from_millis(120)) => {
                progress_events.extend(progress_file.read_new());
                app.activity = progress_activity(&progress_events, tick);
                if let Some(message) = app.messages.get_mut(progress_index) {
                    message.body = format_live_progress(&progress_events);
                }
                terminal.render(app, None)?;
                tick += 1;
            }
        }
    };
    progress_events.extend(progress_file.read_new());
    progress_file.cleanup();
    let json = json_result?;

    let response: CoreResponse = serde_json::from_str(&json)?;
    app.activity = format!("completed in {} ms", response.elapsed_ms);
    if let Some(message) = app.messages.get_mut(progress_index) {
        message.label = "Completed".to_string();
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
