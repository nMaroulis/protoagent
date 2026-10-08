use anyhow::{anyhow, Result};
use crossterm::event::{poll, read, Event, KeyCode, KeyModifiers, MouseEventKind};
use std::time::Duration;
use tokio::time::sleep;

use crate::{
    agent_profile_text, call_process_prompt_with_progress, compact_context_history,
    component_version_text, context_history_text, context_memory_text, context_pack_text,
    context_status_text, context_window_text, empty_as_unknown, format_optional_agent_settings,
    format_scout_settings, help_availability_text, load_doctor, mcp_settings_text_input,
    optional_agent_settings, parse_agents_command,
    progress::{progress_activity, ProgressBatch, ProgressFile},
    refresh_context_text, reset_context_history, scout_settings, set_context_memory_text,
    AgentsCommand, CoreResponse,
};

mod approval;
mod commands;
mod diff_view;
pub(crate) mod input;
mod markdown;
mod modal;
mod model_picker;
mod project;
mod question;
mod render;
mod state;
mod surface;
mod theme;

use crate::question::QuestionReply;
use approval::approval_prompt;
use diff_view::{diff_review_summary, show_diff_modal};
use modal::pick_choice_modal;
use model_picker::{handle_key_command, handle_model_command, load_inventory_with_feedback};
use project::handle_project_command;
use question::question_prompt;
use render::truncate_detail;
use state::{PanelView, Role, TerminalApp};
use surface::TerminalSurface;

const HEADER_ROWS: u16 = 9;
const INPUT_ROWS: u16 = 6;
const WHEEL_LINES: usize = 5;
const INPUT_POLL_MS: u64 = 32;
const ANIMATION_MS: u64 = 120;

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

async fn handle_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    input: &str,
) -> Result<bool> {
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
            switch_panel(
                app,
                PanelView::Dashboard,
                command,
                "Dashboard panel pinned.",
            );
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
        "/mcp" => {
            let arg = input.strip_prefix(command).unwrap_or("").trim();
            match mcp_settings_text_input(arg) {
                Ok(text) => {
                    app.panel = PanelView::Agents;
                    app.refresh(None);
                    app.refresh_agent_settings()?;
                    app.push(Role::Command, command, &text);
                }
                Err(err) => app.push(Role::Error, command, &err.to_string()),
            }
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
        "/debug" => {
            app.configure_debug(input.strip_prefix(command).unwrap_or("").trim());
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
                Err(err) => app.push(
                    Role::Error,
                    command,
                    &format!("Version check failed: {err}"),
                ),
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
            let result = run_background_read(app, terminal, load_doctor).await?;
            match result {
                None => app.push(Role::Command, "/check", "Runtime check canceled."),
                Some(Ok(report)) => {
                    app.panel = PanelView::Check;
                    app.refresh(Some(&report));
                    app.push(Role::Command, "/check", "Runtime check refreshed.");
                }
                Some(Err(err)) => app.push(Role::Error, "/check", &format!("Check failed: {err}")),
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
        "/checkpoints" => {
            match crate::checkpoint_inventory_text() {
                Ok(text) => app.push(Role::Command, "/checkpoints", &text),
                Err(err) => app.push(Role::Error, "/checkpoints", &err.to_string()),
            }
            Ok(true)
        }
        "/undo" => {
            run_task(app, terminal, input).await?;
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
                app.push(
                    Role::Command,
                    "/diff",
                    "No proposed diff is available from the last run.",
                );
            } else {
                app.push(Role::Command, "/diff", "No response in this session yet.");
            }
            Ok(true)
        }
        "/trace" => {
            let trace = app
                .last_response
                .as_ref()
                .map(|response| {
                    crate::timeline::format_run_trace(&response.run_events, &response.events)
                })
                .unwrap_or_default();
            let output = app.live_output.render();
            let detail = format!("{trace}\n\n{output}");
            app.push(
                Role::Command,
                "/trace",
                if detail.trim().is_empty() {
                    "No trace or console diagnostics yet."
                } else {
                    detail.trim()
                },
            );
            Ok(true)
        }
        "/run" => {
            let query = input.strip_prefix(command).unwrap_or("").trim();
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

    if crate::selected_model_label().is_none() {
        app.push(Role::Command, command, &help_availability_text());
        return Ok(());
    }
    run_agent(app, terminal, arg, true).await
}

fn switch_panel(app: &mut TerminalApp, panel: PanelView, command: &str, body: &str) {
    app.panel = panel;
    app.refresh(None);
    app.push(Role::Command, command, body);
}

fn handle_agents_command(app: &mut TerminalApp, command: &str, arg: &str) -> Result<()> {
    let args = arg.split_whitespace().collect::<Vec<_>>();
    let parsed = match parse_agents_command(&args) {
        Ok(parsed) => parsed,
        Err(message) => {
            app.push(
                Role::Error,
                command,
                &message.replace("Usage: agents", "Usage: /agents"),
            );
            return Ok(());
        }
    };

    match parsed {
        AgentsCommand::Status => {
            app.panel = PanelView::Agents;
            app.refresh(None);
            match app.refresh_agent_settings() {
                Ok(()) => {
                    let body = agents_panel_pinned_text(app);
                    app.push(Role::Command, command, &body);
                }
                Err(err) => app.push(
                    Role::Error,
                    command,
                    &format!("Could not load agent settings: {err}"),
                ),
            }
        }
        AgentsCommand::Profile(value) => match agent_profile_text(value) {
            Ok(text) => {
                app.panel = PanelView::Agents;
                app.refresh(None);
                app.push(Role::Command, command, &text);
                if let Err(err) = app.refresh_agent_settings() {
                    app.push(
                        Role::Error,
                        command,
                        &format!("Prompt profile changed, but agent status refresh failed: {err}"),
                    );
                }
            }
            Err(err) => app.push(Role::Error, command, &err.to_string()),
        },
        AgentsCommand::Scout(enabled) => match scout_settings(enabled) {
            Ok(settings) => {
                let text = format_scout_settings(&settings).join("\n");
                app.panel = PanelView::Agents;
                app.refresh(None);
                app.apply_agent_settings(settings);
                app.push(Role::Command, command, &text);
            }
            Err(err) => app.push(Role::Error, command, &err.to_string()),
        },
        AgentsCommand::Optional(name, enabled) => match optional_agent_settings(&name, enabled) {
            Ok(settings) => {
                let text = format_optional_agent_settings(&settings, &name).join("\n");
                app.panel = PanelView::Agents;
                app.refresh(None);
                app.apply_agent_settings(settings);
                app.push(Role::Command, command, &text);
            }
            Err(err) => app.push(Role::Error, command, &err.to_string()),
        },
    }
    Ok(())
}

fn agents_panel_pinned_text(app: &TerminalApp) -> String {
    let scout = if app.agent_settings.is_scout_enabled() {
        "on"
    } else {
        "off"
    };
    format!(
        "Agents panel pinned. Architect, Explorer, Coder and Verifier are required. Optional Tester is {}, Scout is {scout}, MCP is {}. Toggle with /agents tester|scout|mcp on|off; /mcp configures servers. Changes apply to the next run. Current prompt profile: {}. Change it with /agents profile auto|small|medium|large|api.",
        if app.agent_settings.is_enabled("tester") { "on" } else { "off" },
        if app.agent_settings.is_enabled("mcp") { "on" } else { "off" },
        empty_as_unknown(&app.status.prompt_profile),
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

async fn run_task(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    query: &str,
) -> Result<()> {
    run_agent(app, terminal, query, false).await
}

async fn run_agent(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    query: &str,
    guide: bool,
) -> Result<()> {
    let workspace = match (guide, crate::active_project_dir()) {
        (true, _) => String::new(),
        (false, Some(path)) => path.to_string_lossy().to_string(),
        (false, None) => {
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
    app.live_output = Default::default();
    app.last_query = query.to_string();
    if !guide {
        app.last_diff_preview.clear();
    }
    app.push(
        Role::User,
        "You",
        &if guide {
            format!("/help {query}")
        } else {
            query.into()
        },
    );
    app.begin_response(if guide { "guide" } else { "architect" });
    terminal.render(app, None)?;

    let mut progress_file = ProgressFile::new(app.turn);
    let progress_path = progress_file.path_string();
    let mut progress_events = Vec::new();
    let mut task = tokio::task::spawn_blocking(move || {
        if guide {
            crate::call_answer_help_question(prompt, Some(progress_path))
        } else {
            call_process_prompt_with_progress(prompt, workspace, session_id, progress_path)
        }
    });
    let animation_started = std::time::Instant::now();
    let mut cancellation_requested = false;

    let json_result: Result<String> = loop {
        tokio::select! {
            result = &mut task => {
                let raw = match result {
                    Ok(raw) => raw,
                    Err(err) => {
                        break Err(err.into());
                    }
                };
                let _ = poll_runtime_input(app, terminal, true)?;
                ingest_progress(app, &mut progress_events, progress_file.read_new_batch());
                break raw.map_err(|err| anyhow!("Python core error: {err}"));
            }
            _ = sleep(Duration::from_millis(INPUT_POLL_MS)) => {
                ingest_progress(app, &mut progress_events, progress_file.read_ui_batch());
                if !cancellation_requested {
                    if let Some(question) = progress_file.take_question() {
                        app.activity = "waiting for your answer".into();
                        terminal.render(app, None)?;
                        let reply = question_prompt(terminal, app, &progress_file, &question)?;
                        // Restore the transcript frame after painting the overlay.
                        terminal.render(app, None)?;
                        match reply {
                            QuestionReply::Answered(answer) => {
                                progress_file.answer_question(&question, Some(&answer))?;
                                // Answers are already in native history/events. Keep
                                // the UI transcript usable without resubmitting a task.
                                app.record_clarification(&question.question, &answer);
                            }
                            QuestionReply::Declined => progress_file.answer_question(&question, None)?,
                            QuestionReply::Expired => {},
                            QuestionReply::Canceled => {
                                progress_file.request_cancel("Canceled while answering in the ProtoAgent TUI")?;
                                cancellation_requested = true;
                            }
                        }
                    }
                }
                if !cancellation_requested {
                    if let Some(approval) = progress_file.take_approval_request() {
                        if !approval.diff.trim().is_empty() {
                            app.last_diff_preview = approval.diff.clone();
                        }
                        terminal.render(app, None)?;
                        let approved = approval_prompt(terminal, app, &approval)?;
                        // Modal painting bypasses the transcript cache. Restore the
                        // complete frame before returning to incremental updates.
                        terminal.render(app, None)?;
                        progress_file.decide(&approval, approved)?;
                        let decision = if approved { "approved" } else { "denied" };
                        progress_events.push(format!(
                            "Approval {decision}: {}.",
                            if approval.description.is_empty() { approval.action_name } else { approval.description }
                        ));
                    }
                }
                if poll_runtime_input(app, terminal, cancellation_requested)? {
                    progress_file.request_cancel("Canceled from the ProtoAgent TUI")?;
                    cancellation_requested = true;
                    progress_events.push("Cancellation requested from the TUI.".to_string());
                }
                let tick = (animation_started.elapsed().as_millis() / u128::from(ANIMATION_MS)) as usize;
                app.activity = if cancellation_requested { "canceling · waiting for cleanup".into() } else { progress_activity(&progress_events, tick) };
                app.update_streaming_response(tick);
                terminal.render_streaming(app)?;
            }
        }
    };
    ingest_progress(app, &mut progress_events, progress_file.read_new_batch());
    progress_file.cleanup();
    if cancellation_requested {
        let _ = poll_runtime_input(app, terminal, true)?;
        terminal.suppress_exit_escape();
    }
    let response: CoreResponse =
        match json_result.and_then(|json| serde_json::from_str(&json).map_err(Into::into)) {
            Ok(response) => response,
            Err(error) => {
                app.fail_streaming_response(&error.to_string());
                app.activity = "failed".into();
                app.last_response = None;
                app.push(
                Role::Error,
                "Runtime",
                "Check /config or /check, then retry. The terminal is still ready for commands.",
            );
                return Ok(());
            }
        };
    app.context_usage.observe_run_events(&response.run_events);
    if !guide {
        if let Err(err) = crate::sessions::record_turn(query, &response) {
            app.push(Role::Error, "Session history", &err.to_string());
        }
    }
    let terminal_status = match response.status.as_str() {
        "blocked" => "blocked",
        "canceled" => "canceled",
        "incomplete" => "incomplete",
        "failed" => "failed",
        "uncertain" => "uncertain",
        "input_required" => "input required",
        "fallback" | "ready" => "diagnostics",
        _ => "completed",
    };
    app.activity = format!("{} in {} ms", terminal_status, response.elapsed_ms);
    app.push_response(&response);
    app.last_response = Some(response.clone());
    app.refresh(None);

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
    // These are status-bar summaries; complete events remain in the native trace.
    if events.len() > 512 {
        events.drain(..events.len() - 512);
    }
    for update in batch.output {
        app.live_output.observe(update);
    }
    for sample in batch.context_samples {
        app.context_usage.observe(sample);
    }
}

fn poll_runtime_input(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    already_requested: bool,
) -> Result<bool> {
    let mut request_cancellation = false;
    while poll(Duration::from_millis(0))? {
        let event = read()?;
        match runtime_input_action(&event) {
            RuntimeInput::Cancel => request_cancellation |= !already_requested,
            RuntimeInput::ScrollUp(lines) => app.scroll_up(lines),
            RuntimeInput::ScrollDown(lines) => app.scroll_down(lines),
            RuntimeInput::Bottom => app.jump_to_bottom(),
            RuntimeInput::Redraw => terminal.render(app, None)?,
            RuntimeInput::Ignore => {}
        }
    }
    Ok(request_cancellation)
}

#[derive(Debug, PartialEq)]
enum RuntimeInput {
    Cancel,
    ScrollUp(usize),
    ScrollDown(usize),
    Bottom,
    Redraw,
    Ignore,
}

fn runtime_input_action(event: &Event) -> RuntimeInput {
    let page = theme::size()
        .1
        .saturating_sub(HEADER_ROWS + INPUT_ROWS)
        .max(1) as usize;
    match event {
        Event::Key(key) => match key.code {
            KeyCode::Esc => RuntimeInput::Cancel,
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                RuntimeInput::Cancel
            }
            KeyCode::Char('l') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                RuntimeInput::Redraw
            }
            KeyCode::PageUp => RuntimeInput::ScrollUp(page),
            KeyCode::PageDown => RuntimeInput::ScrollDown(page),
            KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => {
                RuntimeInput::ScrollUp(100_000)
            }
            KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => RuntimeInput::Bottom,
            _ => RuntimeInput::Ignore,
        },
        Event::Mouse(mouse) => match mouse.kind {
            MouseEventKind::ScrollUp => RuntimeInput::ScrollUp(WHEEL_LINES),
            MouseEventKind::ScrollDown => RuntimeInput::ScrollDown(WHEEL_LINES),
            _ => RuntimeInput::Ignore,
        },
        Event::Resize(_, _) => RuntimeInput::Redraw,
        _ => RuntimeInput::Ignore,
    }
}

async fn run_background_read<T: Send + 'static>(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    work: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<Option<Result<T>>> {
    let mut task = tokio::task::spawn_blocking(work);
    loop {
        tokio::select! {
            result = &mut task => return Ok(Some(result.unwrap_or_else(|err| Err(err.into())))),
            _ = sleep(Duration::from_millis(INPUT_POLL_MS)) => {
                // Read-only probes may finish in the background after cancellation.
                if poll_runtime_input(app, terminal, false)? {
                    terminal.suppress_exit_escape();
                    return Ok(None);
                }
                terminal.render_streaming(app)?;
            }
        }
    }
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
                    app.push(
                        Role::Command,
                        command,
                        &format!("Renamed current session to {name}."),
                    );
                }
                Err(err) => app.push(
                    Role::Error,
                    command,
                    &format!("Could not rename session: {err}"),
                ),
            }
        }
        return Ok(());
    }

    app.panel = PanelView::Sessions;
    app.refresh(None);
    app.push(
        Role::Command,
        command,
        &crate::sessions::session_panel_rows().join("\n"),
    );
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
    let Some(workspace) =
        crate::active_project_dir().map(|path| path.to_string_lossy().to_string())
    else {
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(
            Role::Error,
            command,
            "Choose a project with /project before using Context Loom.",
        );
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
    let Some(workspace) =
        crate::active_project_dir().map(|path| path.to_string_lossy().to_string())
    else {
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(
            Role::Error,
            command,
            "Choose a project with /project before refreshing Context Loom.",
        );
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
        Err(err) => app.push(
            Role::Error,
            command,
            &format!("Index refresh failed: {err}"),
        ),
    }
    app.activity = "idle".to_string();
    Ok(())
}

fn choose_session(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
) -> Result<()> {
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
                Err(err) => app.push(
                    Role::Error,
                    command,
                    &format!("Could not resume session: {err}"),
                ),
            }
        }
        None => app.push(Role::Command, command, "Session selection cancelled."),
    }
    Ok(())
}

#[cfg(test)]
mod runtime_input_tests {
    use super::*;
    use crossterm::event::{KeyEvent, MouseEvent};

    #[test]
    fn busy_input_preserves_navigation_resize_and_cancellation() {
        let key = |code, modifiers| Event::Key(KeyEvent::new(code, modifiers));
        assert_eq!(
            runtime_input_action(&key(KeyCode::Esc, KeyModifiers::NONE)),
            RuntimeInput::Cancel
        );
        assert_eq!(
            runtime_input_action(&key(KeyCode::Char('c'), KeyModifiers::CONTROL)),
            RuntimeInput::Cancel
        );
        assert!(matches!(
            runtime_input_action(&key(KeyCode::PageUp, KeyModifiers::NONE)),
            RuntimeInput::ScrollUp(_)
        ));
        assert_eq!(
            runtime_input_action(&key(KeyCode::End, KeyModifiers::CONTROL)),
            RuntimeInput::Bottom
        );
        assert_eq!(
            runtime_input_action(&Event::Resize(100, 30)),
            RuntimeInput::Redraw
        );
        assert_eq!(
            runtime_input_action(&key(KeyCode::Char('l'), KeyModifiers::CONTROL)),
            RuntimeInput::Redraw
        );
        assert_eq!(
            runtime_input_action(&Event::Mouse(MouseEvent {
                kind: MouseEventKind::ScrollUp,
                column: 0,
                row: 0,
                modifiers: KeyModifiers::NONE
            })),
            RuntimeInput::ScrollUp(WHEEL_LINES)
        );
    }
}
