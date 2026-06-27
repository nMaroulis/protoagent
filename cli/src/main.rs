use anyhow::{anyhow, Result};
use console::{style, Term};
use indicatif::{ProgressBar, ProgressStyle};
use inquire::{Confirm, Password, Select, Text};
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{hash_map::DefaultHasher, HashMap};
use std::{env, fs};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::Duration;

mod diff;
mod inline_style;
mod progress;
mod sessions;
mod terminal_ui;
mod timeline;

use inline_style::{inline_code_segments, InlineKind};
use progress::{latest_progress_message, ProgressFile, RuntimeApproval};

const APP_TITLE: &str = "PROTOAGENT";
const TAGLINE: &str = "MIAMI-80 LOCAL-FIRST AGENT CONSOLE";
const INPUT_HISTORY_CAPACITY: usize = 10_000;

#[derive(Debug, Clone, Deserialize)]
struct CoreResponse {
    status: String,
    #[serde(default)]
    headline: String,
    #[serde(default)]
    answer: String,
    #[serde(default)]
    thought_process: String,
    #[serde(default)]
    file_target: String,
    #[serde(default)]
    diff: String,
    #[serde(default)]
    events: Vec<String>,
    #[serde(default)]
    run_events: Vec<Value>,
    #[serde(default)]
    approval_requests: Vec<Value>,
    #[serde(default)]
    approval_decisions: Vec<Value>,
    #[serde(default)]
    run_context: Value,
    #[serde(default)]
    run_report: Value,
    #[serde(default)]
    provider: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    responder: String,
    #[serde(default)]
    workspace: String,
    #[serde(default)]
    warning: String,
    #[serde(default)]
    elapsed_ms: u64,
}

#[derive(Debug, Deserialize)]
struct ModelInventory {
    config_path: String,
    #[serde(default)]
    api_key_validation: bool,
    active_provider: String,
    #[serde(default)]
    active_model: String,
    providers: Vec<ModelProvider>,
}

#[derive(Debug, Deserialize, Clone)]
struct ModelProvider {
    id: String,
    name: String,
    kind: String,
    status: String,
    #[serde(default)]
    configured: bool,
    #[serde(default)]
    api_key_set: bool,
    #[serde(default)]
    key_status: String,
    #[serde(default)]
    key_source: String,
    #[serde(default)]
    env_key: String,
    #[serde(default)]
    base_url: String,
    #[serde(default)]
    hint: String,
    #[serde(default)]
    models: Vec<ModelInfo>,
}

#[derive(Debug, Deserialize, Clone)]
struct ModelInfo {
    id: String,
    #[serde(default)]
    source: String,
    #[serde(default)]
    size_label: String,
    #[serde(default)]
    modified_at: String,
}

#[derive(Debug, Deserialize)]
struct VisibleConfig {
    config_path: String,
    active_provider: String,
    providers: HashMap<String, VisibleProviderConfig>,
}

#[derive(Debug, Deserialize)]
struct VisibleProviderConfig {
    label: String,
    #[serde(default)]
    base_url: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    api_key: String,
    #[serde(default)]
    api_key_set: bool,
    #[serde(default)]
    from_env: bool,
    #[serde(default)]
    context_window: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct DoctorReport {
    python: String,
    platform: String,
    workspace: String,
    config_path: String,
    protolink: ProtolinkStatus,
    active_provider: String,
    #[serde(default)]
    active_model: String,
    #[serde(default)]
    active_provider_status: String,
    agents: Vec<AgentManifest>,
}

#[derive(Debug, Deserialize)]
struct ProtolinkStatus {
    installed: bool,
    #[serde(default)]
    version: String,
    #[serde(default)]
    agent_ready: bool,
    #[serde(default)]
    streaming_ready: bool,
    #[serde(default)]
    metrics_ready: bool,
    #[serde(default)]
    compaction_ready: bool,
    #[serde(default)]
    context_manifest_ready: bool,
    #[serde(default)]
    run_report_ready: bool,
    #[serde(default)]
    state_ready: bool,
    #[serde(default)]
    cancellation_ready: bool,
    #[serde(default)]
    error: String,
}

#[derive(Debug, Deserialize)]
struct AgentManifest {
    name: String,
    role: String,
    #[serde(default)]
    memory: String,
    tools: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
struct ProjectConfig {
    active_project: Option<String>,
    #[serde(default)]
    recent_projects: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    context_memory_enabled: Option<bool>,
}

#[tokio::main]
async fn main() -> Result<()> {
    env::set_var("PYTHONDONTWRITEBYTECODE", "1");
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        None | Some("start") | Some("cli") | Some("tui") | Some("terminal") | Some("ui") => {
            terminal_ui::interactive().await
        }
        Some("run") => {
            let query = args.iter().skip(1).cloned().collect::<Vec<_>>().join(" ");
            if query.trim().is_empty() {
                return Err(anyhow!("Usage: proto-cli run \"your task\""));
            }
            print_header()?;
            run_orchestration(&query).await.map(|_| ())
        }
        Some("models") => {
            print_header()?;
            show_models()
        }
        Some("model") => {
            print_header()?;
            choose_model(None)
        }
        Some("key") => {
            print_header()?;
            let provider = args.get(1).cloned();
            add_key(provider.as_deref())
        }
        Some("config") => {
            print_header()?;
            show_config()
        }
        Some("project") | Some("projects") | Some("open") => {
            print_header()?;
            handle_project_command(&args[1..])
        }
        Some("check") => {
            print_header()?;
            show_check()
        }
        Some("dashboard") | Some("dash") | Some("status") => {
            show_dashboard()
        }
        Some("agents") => {
            print_header()?;
            show_agents()
        }
        Some("context") | Some("loom") => {
            print_header()?;
            handle_context_command(&args[1..])
        }
        Some("index") => {
            print_header()?;
            handle_index_command(&args[1..])
        }
        Some("sessions") | Some("session") => {
            print_header()?;
            show_sessions()
        }
        Some("help") | Some("--help") | Some("-h") => {
            let question = args.iter().skip(1).cloned().collect::<Vec<_>>().join(" ");
            print_cli_help();
            if question.trim().is_empty() {
                println!("{}", help_availability_text());
            } else {
                let answer = help_question_with_spinner(question.trim()).await?;
                print_panel(
                    "GUIDE",
                    &answer.lines().map(str::to_string).collect::<Vec<_>>(),
                    PanelTone::Cyan,
                );
            }
            Ok(())
        }
        Some(other) => {
            print_cli_help();
            Err(anyhow!("Unknown command: {other}"))
        }
    }
}

fn print_header() -> Result<()> {
    render_brand_header();
    Ok(())
}

fn print_cli_help() {
    render_brand_header();
    println!("{}", style("Commands").bold().underlined());
    println!("  proto-cli start              Start the fullscreen terminal UI");
    println!("  proto-cli tui                Alias for the fullscreen terminal UI");
    println!("  proto-cli cli                Alias for the fullscreen terminal UI");
    println!("  proto-cli run \"task\"         Run one task");
    println!("  proto-cli dashboard          Show cockpit status");
    println!("  proto-cli models             Show detected local/API models");
    println!("  proto-cli model              Pick active provider/model");
    println!("  proto-cli key [provider]     Add API key");
    println!("  proto-cli config             Show current config");
    println!("  proto-cli project            Show or choose active project folder");
    println!("  proto-cli project set PATH   Set active project folder");
    println!("  proto-cli project clear      Clear active project folder");
    println!("  proto-cli check              Check Python/protolink/providers");
    println!("  proto-cli agents             Show Architect/Explorer/Coder topology");
    println!("  proto-cli context [query]    Show Context Loom status or a Context Pack");
    println!("  proto-cli context window 16k Set Ollama context window; use auto to reset");
    println!("  proto-cli context history    Inspect saved ProtoLink conversation memory");
    println!("  proto-cli context compact    Compact ProtoLink history (tokens by default)");
    println!("  proto-cli context reset      Clear ProtoLink history and the session index");
    println!("  proto-cli context on|off     Toggle persistent project conversation memory");
    println!("  proto-cli index refresh      Refresh the Context Loom workspace index");
    println!("  proto-cli sessions           Show saved project sessions");
    println!("  proto-cli help [question]    Ask Guide about ProtoAgent usage");
    println!();
}

pub(crate) fn help_availability_text() -> String {
    match selected_model_label() {
        Some(selection) => {
            format!("Guide is available on {selection}. Ask ProtoAgent usage questions with `/help <question>`.")
        }
        None => {
            "Static help is available now. Choose a model with `/model` or `proto-cli model`, then ask Guide with `/help <question>`.".to_string()
        }
    }
}

pub(crate) fn help_question_text(question: &str) -> Result<String> {
    if selected_model_label().is_none() {
        return Ok(
            "No model is selected yet. Use /model or `proto-cli model`, then ask Guide with /help <question>.".to_string(),
        );
    }
    let raw = call_answer_help_question(question.to_string())
        .map_err(|err| anyhow!("Python Guide help error: {err:?}"))?;
    let value: Value = serde_json::from_str(&raw)?;
    let answer = value
        .get("answer")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if answer.is_empty() {
        Ok("Guide returned an empty answer.".to_string())
    } else {
        Ok(answer.to_string())
    }
}

async fn help_question_with_spinner(question: &str) -> Result<String> {
    if selected_model_label().is_none() {
        return help_question_text(question);
    }
    let spinner_style = ProgressStyle::with_template("{prefix:.bold.dim} {spinner:.cyan} {msg}")?
        .tick_chars(">|/-\\");
    let pb = ProgressBar::new_spinner();
    pb.set_style(spinner_style);
    pb.set_prefix("[guide]");
    pb.set_message("asking Guide");
    pb.enable_steady_tick(Duration::from_millis(100));

    let question = question.to_string();
    let result = tokio::task::spawn_blocking(move || help_question_text(&question)).await;
    pb.finish_and_clear();
    result?
}

async fn run_orchestration(query: &str) -> Result<CoreResponse> {
    render_run_banner(query);

    let prompt = query.to_string();
    let workspace = require_project_dir_string()?;
    let session_id = context_session_id(&workspace);

    let spinner_style = ProgressStyle::with_template("{prefix:.bold.dim} {spinner:.cyan} {msg}")?
        .tick_chars(">|/-\\");
    let pb = ProgressBar::new_spinner();
    pb.set_style(spinner_style);
    pb.set_prefix("[protolink]");
    pb.set_message("starting ProtoLink runtime");
    pb.enable_steady_tick(Duration::from_millis(100));

    let mut progress_file = ProgressFile::new("run");
    let progress_path = progress_file.path_string();
    let mut progress_events = Vec::new();
    let mut task = tokio::task::spawn_blocking(move || {
        call_process_prompt_with_progress(prompt, workspace, session_id, progress_path)
    });

    let json_result: Result<String> = loop {
        tokio::select! {
            result = &mut task => {
                let raw = match result {
                    Ok(raw) => raw,
                    Err(err) => break Err(err.into()),
                };
                progress_events.extend(progress_file.read_new());
                break raw.map_err(|err| anyhow!("Python core error: {err:?}"));
            }
            _ = tokio::time::sleep(Duration::from_millis(140)) => {
                progress_events.extend(progress_file.read_new());
                if let Some(approval) = progress_file.take_approval_request() {
                    let approved = pb.suspend(|| -> Result<bool> {
                        render_runtime_approval(&approval);
                        if !approval.diff.trim().is_empty() {
                            render_diff(&approval.diff);
                        }
                        Ok(Confirm::new("Authorize this Protolink action?")
                            .with_default(false)
                            .prompt()?)
                    })?;
                    progress_file.decide(&approval, approved)?;
                    progress_events.push(format!(
                        "Approval {}: {}.",
                        if approved { "approved" } else { "denied" },
                        approval.description
                    ));
                }
                pb.set_message(latest_progress_message(&progress_events));
            }
        }
    };
    progress_events.extend(progress_file.read_new());
    progress_file.cleanup();
    let json = match json_result {
        Ok(json) => {
            pb.finish_with_message(format!("completed with {} trace event(s)", progress_events.len()));
            json
        }
        Err(err) => {
            pb.abandon_with_message("task failed");
            return Err(err);
        }
    };

    let response: CoreResponse = serde_json::from_str(&json)?;
    if let Err(err) = sessions::record_turn(query, &response) {
        eprintln!("{}", style(format!("session history warning: {err}")).yellow());
    }
    render_response(&response)?;

    Ok(response)
}

fn render_response(response: &CoreResponse) -> Result<()> {
    let model = if response.model.is_empty() {
        "not selected"
    } else {
        response.model.as_str()
    };
    let mut summary = vec![
        format!("Status      : {}", response.status),
        format!("Responder   : {}", response_actor(response)),
        format!("Provider    : {} / {}", empty_as_unknown(&response.provider), model),
        format!("Elapsed     : {} ms", response.elapsed_ms),
    ];
    if !response.headline.is_empty() {
        summary.push(format!("Headline    : {}", response.headline));
    }
    if !response.warning.is_empty() {
        summary.push(format!("Warning     : {}", response.warning));
    }
    if !response.run_events.is_empty() {
        summary.push(format!("Run events  : {} normalized", response.run_events.len()));
    }
    if !response.approval_requests.is_empty() {
        summary.push(format!(
            "Approvals   : {} request(s), {} decision(s)",
            response.approval_requests.len(),
            response.approval_decisions.len()
        ));
    }
    if let Some(run_id) = response.run_context.get("run_id").and_then(Value::as_str) {
        summary.push(format!("Run ID      : {run_id}"));
    }
    print_panel("RUN SUMMARY", &summary, PanelTone::Magenta);

    if !response.run_events.is_empty() || !response.events.is_empty() {
        render_agent_trace(&response.run_events, &response.events);
        render_agent_timeline(&response.run_events, &response.events);
    }

    if !response.answer.trim().is_empty() {
        let answer = wrap_lines(&response.answer, panel_inner_width());
        print_panel("ANSWER", &answer, PanelTone::Cyan);
    }

    if !response.file_target.is_empty() {
        print_panel("TARGET", &[response.file_target.clone()], PanelTone::Cyan);
    }

    if !response.thought_process.is_empty() {
        let notes = wrap_lines(&response.thought_process, panel_inner_width());
        print_panel("CORE NOTES", &notes, PanelTone::Dim);
    }

    if !response.diff.trim().is_empty() {
        render_diff(&response.diff);
    }

    Ok(())
}

fn render_run_banner(query: &str) {
    println!();
    println!("{}", style(repeat_char('=', terminal_width())).magenta());
    println!("{}", style("RUN CHANNEL OPEN").bold().magenta());
    println!("{}", style(truncate_plain(query, terminal_width().saturating_sub(4))).cyan());
    println!("{}", style(repeat_char('-', terminal_width())).dim());
}

fn render_agent_trace(run_events: &[Value], events: &[String]) {
    println!("{}", style("AGENT TRACE").bold().underlined().cyan());
    for line in timeline::format_run_trace(run_events, events).lines() {
        print_inline_code_line(&format!("  {}", line));
    }
    println!();
}

fn render_agent_timeline(run_events: &[Value], events: &[String]) {
    println!("{}", style("AGENT TIMELINE").bold().underlined().cyan());
    for line in timeline::format_timeline_from_run_events(run_events, events, 18).lines() {
        print_inline_code_line(&format!("  {}", line));
    }
    println!();
}

fn render_diff(diff_text: &str) {
    let review = diff::parse_diff(diff_text);
    println!("{}", style("PROPOSED DIFF").bold().underlined().cyan());
    print!("  {}", style(format!("{} file(s)", review.files.len())).bold());
    print!("  {}", style(format!("+{}", review.additions)).green().bold());
    print!(" ");
    print!("{}", style(format!("-{}", review.removals)).red().bold());
    println!("  {}", style(format!("{} hunk(s)", review.hunks)).yellow().bold());

    if review.files.is_empty() {
        println!("{}", style("  No structured diff lines found.").dim());
        println!();
        return;
    }

    let number_width = review.line_number_width();
    let line_width = terminal_width().saturating_sub(2);
    let column_header = format!(
        "{:>width$} {:>width$} | change",
        "old",
        "new",
        width = number_width
    );
    for file in &review.files {
        println!();
        println!(
            "{}",
            style(format!("FILE {}", diff::format_file_heading(file)))
                .cyan()
                .bold()
        );
        println!("{}", style(&column_header).dim());
        for line in &file.lines {
            let rendered =
                truncate_plain(&diff::format_guttered_line(line, number_width), line_width);
            match line.kind {
                diff::DiffLineKind::Add => println!("{}", style(rendered).green()),
                diff::DiffLineKind::Remove => println!("{}", style(rendered).red()),
                diff::DiffLineKind::Hunk => println!("{}", style(rendered).yellow().bold()),
                diff::DiffLineKind::Meta => println!("{}", style(rendered).dim()),
                diff::DiffLineKind::Context => println!("{rendered}"),
            }
        }
    }
    println!();
}

fn render_runtime_approval(approval: &RuntimeApproval) {
    let mut rows = vec![
        "Protolink paused this action before execution.".to_string(),
        format!("Run        : {}", empty_as_unknown(&approval.run_id)),
        format!("Action     : {}", empty_as_unknown(&approval.action_name)),
        format!("Capability : {}", empty_as_unknown(&approval.capabilities())),
        format!("Target     : {}", empty_as_unknown(&approval.target)),
    ];
    if !approval.description.is_empty() {
        rows.push(format!("Intent     : {}", approval.description));
    }
    print_panel("POLICY APPROVAL", &rows, PanelTone::Yellow);
}

fn show_dashboard() -> Result<()> {
    render_brand_header();
    let inventory = load_inventory();
    let config = load_visible_config();
    let project = project_label();

    let mut rows = vec![format!("Project   : {}", project)];
    match &config {
        Ok(config) => {
            let provider = &config.active_provider;
            let model = config
                .providers
                .get(provider)
                .map(|data| data.model.as_str())
                .unwrap_or("");
            rows.push(format!("Provider  : {}", provider));
            rows.push(format!("Model     : {}", if model.is_empty() { "not selected" } else { model }));
            rows.push(format!("Config    : {}", config.config_path));
        }
        Err(err) => rows.push(format!("Config    : unavailable ({err})")),
    }
    match &inventory {
        Ok(inventory) => {
            let total_models: usize = inventory.providers.iter().map(|provider| provider.models.len()).sum();
            let online = inventory
                .providers
                .iter()
                .filter(|provider| provider.status == "online" || provider.status == "configured")
                .count();
            rows.push(format!("Models    : {} visible across {} providers", total_models, inventory.providers.len()));
            rows.push(format!("Ready     : {} provider(s)", online));
        }
        Err(err) => rows.push(format!("Models    : unavailable ({err})")),
    }
    print_panel("COCKPIT", &rows, PanelTone::Magenta);

    print_agent_graph();

    if let Ok(inventory) = inventory {
        render_provider_strip(&inventory);
    }

    let commands = vec![
        "/menu opens the command palette".to_string(),
        "/models scans local and cloud model options".to_string(),
        "/project sets the active project folder".to_string(),
        "Use @ inside a task to tag project files".to_string(),
        "/context shows Context Loom evidence; /context history shows model memory".to_string(),
        "/check checks runtime wiring".to_string(),
        "Type any coding task to dispatch Architect -> Explorer -> Coder".to_string(),
    ];
    print_panel("HOTKEYS", &commands, PanelTone::Cyan);
    Ok(())
}

fn handle_project_command(args: &[String]) -> Result<()> {
    match args.first().map(String::as_str) {
        None | Some("show") | Some("status") => show_project(),
        Some("set") | Some("open") => {
            let path = args
                .get(1)
                .ok_or_else(|| anyhow!("Usage: proto-cli project set PATH"))?;
            let selected = set_active_project(path)?;
            print_panel(
                "PROJECT OPENED",
                &[
                    format!("Active : {}", selected.to_string_lossy()),
                    "Future starts will reopen this project automatically.".to_string(),
                ],
                PanelTone::Cyan,
            );
            Ok(())
        }
        Some("clear") | Some("unset") => {
            clear_active_project()?;
            print_panel(
                "PROJECT CLEARED",
                &["No project is selected. Run `proto-cli project set PATH` or use `/project` in the TUI.".to_string()],
                PanelTone::Yellow,
            );
            Ok(())
        }
        Some("choose") => choose_project_from_prompt(),
        Some(path) => {
            let selected = set_active_project(path)?;
            print_panel(
                "PROJECT OPENED",
                &[
                    format!("Active : {}", selected.to_string_lossy()),
                    "Future starts will reopen this project automatically.".to_string(),
                ],
                PanelTone::Cyan,
            );
            Ok(())
        }
    }
}

fn show_project() -> Result<()> {
    let config = load_project_config();
    let mut rows = vec![
        format!("Active : {}", project_label()),
        context_memory_text(),
        format!("Config : {}", project_config_path().to_string_lossy()),
    ];
    if !config.recent_projects.is_empty() {
        rows.push("Recent :".to_string());
        for path in config.recent_projects.iter().take(8) {
            rows.push(format!("  - {path}"));
        }
    }
    rows.push("Set    : proto-cli project set PATH".to_string());
    rows.push("TUI    : /project or /project PATH".to_string());
    rows.push("Tags   : type @ in the TUI to insert a project file reference".to_string());
    print_panel("PROJECT", &rows, PanelTone::Magenta);
    Ok(())
}

fn choose_project_from_prompt() -> Result<()> {
    let initial = active_project_dir()
        .unwrap_or_else(default_launch_workspace)
        .to_string_lossy()
        .to_string();
    let entered = Text::new("Project folder")
        .with_initial_value(&initial)
        .prompt()?;
    if entered.trim().is_empty() {
        print_panel("PROJECT", &["Selection cancelled.".to_string()], PanelTone::Yellow);
        return Ok(());
    }
    let selected = set_active_project(&entered)?;
    print_panel(
        "PROJECT OPENED",
        &[format!("Active : {}", selected.to_string_lossy())],
        PanelTone::Cyan,
    );
    Ok(())
}

fn show_models() -> Result<()> {
    let inventory = load_inventory_with_validation(true)?;
    print_panel(
        "MODEL RADAR",
        &[
            format!(
                "Active : {} / {}",
                inventory.active_provider,
                if inventory.active_model.is_empty() {
                    "not selected"
                } else {
                    inventory.active_model.as_str()
                }
            ),
            format!("Config : {}", inventory.config_path),
            format!(
                "Keys   : {}",
                if inventory.api_key_validation {
                    "API keys checked against provider endpoints"
                } else {
                    "API keys read from config/env"
                }
            ),
        ],
        PanelTone::Magenta,
    );

    render_provider_strip(&inventory);

    for provider in &inventory.providers {
        render_provider_card(provider);
    }
    Ok(())
}

fn render_provider_strip(inventory: &ModelInventory) {
    let mut providers = inventory
        .providers
        .iter()
        .map(|provider| (provider_priority(provider), provider))
        .collect::<Vec<_>>();
    providers.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.name.cmp(&right.1.name)));
    print_provider_strip(&providers.into_iter().map(|(_, provider)| provider).collect::<Vec<_>>());
}

fn print_provider_strip(providers: &[&ModelProvider]) {
    let width = terminal_width();
    let inner = width.saturating_sub(4).max(24);
    let title_text = " PROVIDERS ";
    let line_len = width.saturating_sub(title_text.len() + 3).max(2);
    let top = format!("+{}{}+", title_text, repeat_char('-', line_len));
    println!("{}", style(&top).dim().bold());
    if providers.is_empty() {
        println!("| {:<inner$} |", "", inner = inner);
    }
    for provider in providers {
        let marker = provider_marker(provider);
        let rest = format!("{:<22} {:>2} model(s)", provider.name, provider.models.len());
        let marker_width = marker.chars().count();
        let available = inner.saturating_sub(marker_width + 1);
        let rest = truncate_plain(&rest, available);
        let plain_len = marker_width + 1 + rest.chars().count();
        let padding = inner.saturating_sub(plain_len);
        print!("| ");
        print!("{}", provider_marker_style(marker, provider));
        print!(" {}{}", rest, " ".repeat(padding));
        println!(" |");
    }
    println!("{}", style(&format!("+{}+", repeat_char('-', width.saturating_sub(2)))).dim().bold());
    println!();
}

fn provider_marker(provider: &ModelProvider) -> &'static str {
    if provider.kind == "api" {
        match provider.key_status.as_str() {
            "valid" => "K✓",
            "invalid" => "K✗",
            "missing" => "K?",
            "unverified" => "K!",
            "set" => "K!",
            _ if provider.api_key_set => "K!",
            _ => "K?",
        }
    } else if provider.status == "online" {
        "L✓"
    } else if provider.status == "detected" {
        "L*"
    } else if provider.status == "not-found" {
        "L-"
    } else {
        "L✗"
    }
}

fn provider_marker_style<'a>(marker: &'a str, provider: &ModelProvider) -> console::StyledObject<&'a str> {
    if marker == "K✓" || marker == "L✓" {
        style(marker).green().bold()
    } else if marker == "K✗" || marker == "L✗" {
        style(marker).red().bold()
    } else if marker == "K?" || marker == "K!" || marker == "L*" {
        style(marker).yellow().bold()
    } else if provider.configured {
        style(marker).cyan().bold()
    } else {
        style(marker).dim().bold()
    }
}

fn provider_priority(provider: &ModelProvider) -> u8 {
    if provider.kind == "api" && provider.key_status == "valid" {
        0
    } else if provider.status == "online" {
        1
    } else if provider.status == "detected" || provider.configured {
        2
    } else if provider.kind == "api" && provider.api_key_set {
        3
    } else if provider.kind == "api" {
        4
    } else {
        5
    }
}

fn provider_badges(provider: &ModelProvider) -> String {
    let mut badges = Vec::new();
    badges.push(format!("[{}]", provider.status.to_uppercase()));
    if provider.kind == "api" || provider.api_key_set || !provider.key_status.is_empty() {
        badges.push(format!("[KEY:{}]", provider_key_badge(provider)));
    }
    if provider.configured {
        badges.push("[READY]".to_string());
    } else {
        badges.push("[SETUP]".to_string());
    }
    badges.join(" ")
}

fn provider_key_badge(provider: &ModelProvider) -> &'static str {
    match provider.key_status.as_str() {
        "valid" => "VALID",
        "invalid" => "INVALID",
        "missing" => "MISSING",
        "unverified" => "UNVERIFIED",
        "not-required" => "NOT-REQUIRED",
        "set" => "SET",
        "" if provider.api_key_set => "SET",
        "" => "N/A",
        _ => "UNKNOWN",
    }
}

fn provider_key_line(provider: &ModelProvider) -> String {
    let status = provider_key_badge(provider);
    let source = if provider.key_source.is_empty() {
        "none"
    } else {
        provider.key_source.as_str()
    };
    if provider.env_key.is_empty() {
        format!("{status} via {source}")
    } else {
        format!("{status} via {source} (env: {})", provider.env_key)
    }
}

fn provider_needs_key_prompt(provider: &ModelProvider) -> bool {
    provider.kind == "api"
        && (!provider.api_key_set || matches!(provider.key_status.as_str(), "missing" | "invalid"))
}

fn ensure_cli_provider_key(provider: &mut ModelProvider) -> Result<()> {
    if !provider_needs_key_prompt(provider) {
        return Ok(());
    }
    print_panel(
        "API KEY REQUIRED",
        &[
            format!("Provider: {} ({})", provider.name, provider.id),
            format!("Key     : {}", provider_key_line(provider)),
            provider.hint.clone(),
        ],
        PanelTone::Yellow,
    );
    let api_key = Password::new("API key").without_confirmation().prompt()?;
    call_add_api_key(provider.id.clone(), api_key)
        .map_err(|err| anyhow!("Python config error: {err:?}"))?;
    if let Some(updated) = load_inventory_with_validation(true)?
        .providers
        .into_iter()
        .find(|item| item.id == provider.id)
    {
        *provider = updated;
    }
    if provider.key_status == "invalid" {
        return Err(anyhow!("API key for {} was rejected by the provider", provider.name));
    }
    Ok(())
}

fn render_provider_card(provider: &ModelProvider) {
    let mut rows = vec![
        format!("Badges : {}", provider_badges(provider)),
        format!("Kind   : {}", provider.kind),
        format!("Status : {} ({})", provider.status, if provider.configured { "ready" } else { "setup" }),
    ];
    if provider.kind == "api" || provider.api_key_set || !provider.key_status.is_empty() {
        rows.push(format!("Key    : {}", provider_key_line(provider)));
    }
    if !provider.base_url.is_empty() {
        rows.push(format!("Base   : {}", provider.base_url));
    }
    if provider.models.is_empty() {
        rows.push(format!(
            "Models : {}",
            if provider.hint.is_empty() {
                "none reported"
            } else {
                provider.hint.as_str()
            }
        ));
    } else {
        rows.push(format!("Models : {}", provider.models.len()));
        for model in provider.models.iter().take(8) {
            let size = if model.size_label.is_empty() {
                String::new()
            } else {
                format!(" [{}]", model.size_label)
            };
            let source = if model.source.is_empty() {
                String::new()
            } else {
                format!(" via {}", model.source)
            };
            let modified = if model.modified_at.is_empty() {
                String::new()
            } else {
                format!(" {}", model.modified_at)
            };
            rows.push(format!("  - {}{}{}{}", model.id, size, source, modified));
        }
        if provider.models.len() > 8 {
            rows.push(format!("  ...and {} more", provider.models.len() - 8));
        }
    }
    if !provider.hint.is_empty() && provider.kind == "api" {
        rows.push(format!("Hint   : {}", provider.hint));
    }
    let tone = match provider.status.as_str() {
        "online" | "configured" | "detected" => PanelTone::Cyan,
        "needs-key" | "key-invalid" | "key-unverified" | "not-found" => PanelTone::Yellow,
        _ => PanelTone::Dim,
    };
    print_panel(&format!("{} ({})", provider.name, provider.id), &rows, tone);
}

fn choose_model(preselected_provider: Option<&str>) -> Result<()> {
    let inventory = load_inventory_with_validation(true)?;
    let mut provider = match preselected_provider {
        Some(provider_id) => inventory
            .providers
            .iter()
            .find(|provider| provider.id == provider_id)
            .cloned()
            .ok_or_else(|| anyhow!("Unknown provider: {provider_id}"))?,
        None => {
            let labels: Vec<String> = inventory
                .providers
                .iter()
                .map(|provider| {
                    format!(
                        "{} ({}) - {} model(s) {}",
                        provider.name,
                        provider.id,
                        provider.models.len(),
                        provider_badges(provider)
                    )
                })
                .collect();
            let selected = Select::new("Choose provider", labels).prompt()?;
            let index = inventory
                .providers
                .iter()
                .position(|provider| selected.contains(&format!("({})", provider.id)))
                .ok_or_else(|| anyhow!("Could not resolve provider selection"))?;
            inventory.providers[index].clone()
        }
    };
    ensure_cli_provider_key(&mut provider)?;

    let mut model_choices: Vec<String> = provider.models.iter().map(|model| model.id.clone()).collect();
    model_choices.push("Custom model id or path".to_string());
    let selected_model = Select::new("Choose model", model_choices).prompt()?;
    let model = if selected_model == "Custom model id or path" {
        Text::new("Model id or .gguf path").prompt()?
    } else {
        selected_model
    };

    let base_url = if provider.kind.contains("server") || provider.kind.contains("compatible") {
        let current = provider.base_url.clone();
        let entered = Text::new("Base URL")
            .with_initial_value(if current.is_empty() { "" } else { &current })
            .prompt()?;
        Some(entered)
    } else {
        None
    };

    call_set_model(provider.id.clone(), model.clone(), base_url)
        .map_err(|err| anyhow!("Python config error: {err:?}"))?;
    print_panel(
        "MODEL SELECTED",
        &[format!("{} / {}", provider.id, model)],
        PanelTone::Cyan,
    );
    Ok(())
}

fn add_key(preselected_provider: Option<&str>) -> Result<()> {
    let providers = vec![
        "openai".to_string(),
        "anthropic".to_string(),
        "gemini".to_string(),
        "deepseek".to_string(),
        "openai-compatible".to_string(),
    ];
    let provider = match preselected_provider {
        Some(value) if providers.iter().any(|provider| provider == value) => value.to_string(),
        Some(value) => return Err(anyhow!("{value} is not a supported API-key provider")),
        None => Select::new("API-key provider", providers).prompt()?,
    };

    let api_key = Password::new("API key").without_confirmation().prompt()?;
    call_add_api_key(provider.clone(), api_key)
        .map_err(|err| anyhow!("Python config error: {err:?}"))?;
    let rows = match load_inventory_with_validation(true)
        .ok()
        .and_then(|inventory| inventory.providers.into_iter().find(|item| item.id == provider))
    {
        Some(provider) => vec![
            format!("Provider: {}", provider.name),
            format!("Key     : {}", provider_key_line(&provider)),
            format!("Status  : {}", provider_badges(&provider)),
        ],
        None => vec![format!("Provider: {}", provider)],
    };
    print_panel("KEY STORED", &rows, PanelTone::Cyan);

    if Confirm::new("Choose a model for this provider now?")
        .with_default(true)
        .prompt()?
    {
        choose_model(Some(&provider))?;
    }
    Ok(())
}

fn show_config() -> Result<()> {
    let config = load_visible_config()?;
    print_panel(
        "CONFIGURATION",
        &[
            format!("Path   : {}", config.config_path),
            format!("Active : {}", config.active_provider),
        ],
        PanelTone::Magenta,
    );

    let mut providers: Vec<_> = config.providers.iter().collect();
    providers.sort_by(|a, b| a.0.cmp(b.0));
    for (id, provider) in providers {
        let mut rows = vec![
            format!("Label : {}", provider.label),
            format!(
                "Model : {}",
                if provider.model.is_empty() {
                    "not selected"
                } else {
                    provider.model.as_str()
                }
            ),
        ];
        if !provider.base_url.is_empty() {
            rows.push(format!("Base  : {}", provider.base_url));
        }
        if let Some(window) = provider.context_window {
            rows.push(format!("Context: {} tokens", format_token_count(window)));
        }
        if provider.api_key_set {
            let source = if provider.from_env { "env" } else { "config" };
            rows.push(format!("Key   : {} ({source})", provider.api_key));
        }
        print_panel(id, &rows, PanelTone::Dim);
    }
    Ok(())
}

fn selected_model_label() -> Option<String> {
    let config = load_visible_config().ok()?;
    let provider = config.active_provider;
    let model = config.providers.get(&provider)?.model.trim().to_string();
    if model.is_empty() {
        None
    } else {
        Some(format!("{provider} / {model}"))
    }
}

fn show_check() -> Result<()> {
    let report = load_doctor()?;
    print_panel(
        "CHECK",
        &[
            format!("Python    : {}", report.python),
            format!("Platform  : {}", report.platform),
            format!("Workspace : {}", report.workspace),
            format!("Config    : {}", report.config_path),
            format!(
                "Protolink : {}",
                if report.protolink.installed && report.protolink.agent_ready {
                    format!(
                        "installed {}, stream {}, metrics {}, compaction {}, context {}, state {}, reports {}, cancellation {}",
                        empty_as_unknown(&report.protolink.version),
                        readiness(report.protolink.streaming_ready),
                        readiness(report.protolink.metrics_ready),
                        readiness(report.protolink.compaction_ready),
                        readiness(report.protolink.context_manifest_ready),
                        readiness(report.protolink.state_ready),
                        readiness(report.protolink.run_report_ready),
                        readiness(report.protolink.cancellation_ready),
                    )
                } else if report.protolink.installed {
                    format!("installed, agent runtime blocked ({})", report.protolink.error)
                } else {
                    format!("missing ({})", report.protolink.error)
                }
            ),
            format!(
                "Active    : {} / {} [{}]",
                report.active_provider,
                if report.active_model.is_empty() {
                    "not selected"
                } else {
                    report.active_model.as_str()
                },
                report.active_provider_status
            ),
        ],
        PanelTone::Magenta,
    );

    let rows: Vec<String> = report
        .agents
        .iter()
        .map(format_agent_manifest)
        .collect();
    print_panel("AGENTS", &rows, PanelTone::Cyan);
    Ok(())
}

fn readiness(ready: bool) -> &'static str {
    if ready { "ready" } else { "unavailable" }
}

fn show_agents() -> Result<()> {
    print_agent_graph();
    if let Ok(report) = load_doctor() {
        let rows: Vec<String> = report
            .agents
            .iter()
            .map(format_agent_manifest)
            .collect();
        print_panel("TOOL ISOLATION", &rows, PanelTone::Cyan);
    }
    Ok(())
}

fn format_agent_manifest(agent: &AgentManifest) -> String {
    let memory = if agent.memory.is_empty() {
        format!("protoagent-{}", agent.name)
    } else {
        agent.memory.clone()
    };
    let tools = if agent.tools.is_empty() {
        "no direct tools".to_string()
    } else {
        agent.tools.join(", ")
    };
    format!(
        "{} ({}) | memory: {} | tools: {}",
        agent.name, agent.role, memory, tools
    )
}

fn handle_context_command(args: &[String]) -> Result<()> {
    match args.first().map(String::as_str) {
        Some("window") => {
            if args.len() > 2 {
                return Err(anyhow!("Usage: proto-cli context window [16k|auto]"));
            }
            let value = args.get(1).cloned();
            let text = context_window_text(value)?;
            print_panel(
                "RUNTIME CONTEXT",
                &text.lines().map(str::to_string).collect::<Vec<_>>(),
                PanelTone::Cyan,
            );
            Ok(())
        }
        Some("compact") => {
            let values = args.iter().skip(1).map(String::as_str).collect::<Vec<_>>();
            let text = compact_context_history(&values)?;
            print_panel("CONVERSATION MEMORY", &[text], PanelTone::Cyan);
            Ok(())
        }
        Some("history") => {
            let text = context_history_text()?;
            print_panel(
                "PROTOLINK MEMORY",
                &text.lines().map(str::to_string).collect::<Vec<_>>(),
                PanelTone::Cyan,
            );
            Ok(())
        }
        Some("reset") => {
            let text = reset_context_history()?;
            print_panel("CONVERSATION MEMORY", &[text], PanelTone::Cyan);
            Ok(())
        }
        Some("on") => {
            let text = set_context_memory_text(true)?;
            print_panel("CONVERSATION MEMORY", &[text], PanelTone::Cyan);
            Ok(())
        }
        Some("off") => {
            let text = set_context_memory_text(false)?;
            print_panel("CONVERSATION MEMORY", &[text], PanelTone::Yellow);
            Ok(())
        }
        Some("memory") => {
            let text = context_memory_text();
            print_panel("CONVERSATION MEMORY", &[text], PanelTone::Cyan);
            Ok(())
        }
        _ => {
            let query = args.join(" ");
            if query.trim().is_empty() {
                show_context_status()
            } else {
                show_context_pack(query.trim())
            }
        }
    }
}

pub(crate) fn context_window_text(value: Option<String>) -> Result<String> {
    let raw = match value.as_deref() {
        None | Some("status") => call_no_args("get_context_settings"),
        Some(value) => call_configure_context_window(Some(value.to_string())),
    }
    .map_err(|err| anyhow!("Python context configuration error: {err:?}"))?;
    let settings: Value = serde_json::from_str(&raw)?;
    let provider = settings.get("provider").and_then(Value::as_str).unwrap_or("unknown");
    let model = settings.get("model").and_then(Value::as_str).unwrap_or("");
    let selection = if model.trim().is_empty() {
        provider.to_string()
    } else {
        format!("{provider} / {model}")
    };
    let controllable = settings
        .get("controllable")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !controllable {
        return Ok(format!(
            "Provider: {selection}\nWindow: provider managed; ProtoAgent cannot change it"
        ));
    }
    let window = settings.get("window_tokens").and_then(Value::as_u64).unwrap_or(0);
    let source = settings.get("source").and_then(Value::as_str).unwrap_or("unknown");
    Ok(format!(
        "Provider: {selection}\nWindow: {} tokens ({source})\nCommands: /context window 16k | /context window auto",
        format_token_count(window)
    ))
}

pub(crate) fn compact_context_history(values: &[&str]) -> Result<String> {
    if !context_memory_enabled() {
        return Ok("Conversation memory is off. New tasks use task-local ProtoLink sessions and start fresh. Use /context on to persist project history again.".to_string());
    }
    let (strategy, limit, ui_turns) = parse_compaction_request(values)?;
    let workspace = require_project_dir_string()?;
    let session_id = project_session_id(&workspace);
    let raw = call_compact_protolink_history(session_id, strategy.to_string(), limit)
        .map_err(|err| anyhow!("Python ProtoLink compaction error: {err:?}"))?;
    let report: Value = serde_json::from_str(&raw)?;
    let summary = report
        .get("summary")
        .and_then(Value::as_str)
        .unwrap_or("ProtoLink history compaction completed.");

    let session = sessions::compact_current_history(ui_turns)?;
    if session.found {
        Ok(format!(
            "{summary}\nSession index: removed {} old turn(s), kept {} recent turn(s).",
            session.removed, session.kept
        ))
    } else {
        Ok(summary.to_string())
    }
}

pub(crate) fn reset_context_history() -> Result<String> {
    let workspace = require_project_dir_string()?;
    let session_id = project_session_id(&workspace);
    let raw = call_reset_protolink_history(session_id)
        .map_err(|err| anyhow!("Python ProtoLink history reset error: {err:?}"))?;
    let report: Value = serde_json::from_str(&raw)?;
    let summary = report
        .get("summary")
        .and_then(Value::as_str)
        .unwrap_or("ProtoLink history reset completed.");
    let session = sessions::compact_current_history(0)?;
    if session.found {
        Ok(format!(
            "{summary}\nSession index: removed {} stored turn(s).",
            session.removed
        ))
    } else {
        Ok(summary.to_string())
    }
}

pub(crate) fn context_history_text() -> Result<String> {
    if !context_memory_enabled() {
        return Ok("Conversation memory is off. New tasks use task-local ProtoLink sessions and start fresh. Use /context on to inspect and resume project memory.".to_string());
    }
    let workspace = require_project_dir_string()?;
    let session_id = project_session_id(&workspace);
    let raw = call_describe_protolink_history(session_id)
        .map_err(|err| anyhow!("Python ProtoLink history inspection error: {err:?}"))?;
    let report: Value = serde_json::from_str(&raw)?;
    let mut rows = vec![report
        .get("summary")
        .and_then(Value::as_str)
        .unwrap_or("ProtoLink history inspection completed.")
        .to_string()];

    if let Some(agents) = report.get("agents").and_then(Value::as_array) {
        for agent in agents {
            let name = title_case_agent(agent.get("agent").and_then(Value::as_str).unwrap_or("agent"));
            if !agent.get("found").and_then(Value::as_bool).unwrap_or(false) {
                rows.push(format!("{name}: no saved model-facing history."));
                continue;
            }
            let messages = agent.get("message_count").and_then(Value::as_u64).unwrap_or(0);
            let tokens = agent.get("estimated_tokens").and_then(Value::as_u64).unwrap_or(0);
            rows.push(format!(
                "{name}: {messages} message(s), about {} tokens.",
                format_token_count(tokens)
            ));
            if let Some(recent) = agent.get("recent").and_then(Value::as_array) {
                for message in recent {
                    let role = message.get("role").and_then(Value::as_str).unwrap_or("unknown");
                    let preview = message.get("preview").and_then(Value::as_str).unwrap_or("");
                    if !preview.trim().is_empty() {
                        rows.push(format!("  {role}: {}", truncate_plain(preview, 96)));
                    }
                }
            }
        }
    }
    Ok(rows.join("\n"))
}

fn parse_compaction_request(values: &[&str]) -> Result<(&'static str, Option<usize>, usize)> {
    if values.is_empty() {
        return Ok(("tokens", None, 2));
    }
    if values.len() == 1 {
        if let Ok(turns) = values[0].parse::<usize>() {
            if turns > 20 {
                return Err(anyhow!("Compaction can keep at most 20 recent turns"));
            }
            let messages = turns.saturating_mul(4).saturating_add(1).max(2);
            return Ok(("recent", Some(messages), turns));
        }
    }
    if values.len() > 2 {
        return Err(anyhow!(
            "Usage: /context compact [recent|tokens|summary] [limit]"
        ));
    }
    let strategy = match values[0].to_ascii_lowercase().as_str() {
        "recent" => "recent",
        "tokens" => "tokens",
        "summary" => "summary",
        _ => {
            return Err(anyhow!(
                "Usage: /context compact [recent|tokens|summary] [limit]"
            ))
        }
    };
    let limit = values
        .get(1)
        .map(|value| value.parse::<usize>())
        .transpose()
        .map_err(|_| anyhow!("Compaction limit must be a positive integer"))?;
    if limit == Some(0) || (strategy == "recent" && limit == Some(1)) {
        return Err(anyhow!("Compaction limit is too small for {strategy}"));
    }
    Ok((strategy, limit, 2))
}

#[cfg(test)]
mod context_command_tests {
    use super::{
        context_memory_enabled, context_session_id, parse_compaction_request,
        set_context_memory_enabled,
    };
    use std::{env, fs};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn defaults_to_protolink_token_compaction() {
        assert_eq!(
            parse_compaction_request(&[]).unwrap(),
            ("tokens", None, 2)
        );
    }

    #[test]
    fn supports_strategies_and_legacy_turn_shorthand() {
        assert_eq!(
            parse_compaction_request(&["summary", "8"]).unwrap(),
            ("summary", Some(8), 2)
        );
        assert_eq!(
            parse_compaction_request(&["3"]).unwrap(),
            ("recent", Some(13), 3)
        );
        assert!(parse_compaction_request(&["mystery"]).is_err());
    }

    #[test]
    fn context_memory_toggle_controls_project_session_id() {
        let previous = env::var("PROTOAGENT_CONFIG_DIR").ok();
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = env::temp_dir().join(format!("protoagent-context-toggle-{unique}"));
        env::set_var("PROTOAGENT_CONFIG_DIR", &root);

        assert!(context_memory_enabled());
        assert!(context_session_id("/tmp/example-project").is_some());
        set_context_memory_enabled(false).unwrap();
        assert!(!context_memory_enabled());
        assert!(context_session_id("/tmp/example-project").is_none());
        set_context_memory_enabled(true).unwrap();
        assert!(context_session_id("/tmp/example-project").is_some());

        if let Some(value) = previous {
            env::set_var("PROTOAGENT_CONFIG_DIR", value);
        } else {
            env::remove_var("PROTOAGENT_CONFIG_DIR");
        }
        let _ = fs::remove_dir_all(root);
    }
}

fn format_token_count(tokens: u64) -> String {
    if tokens >= 1_048_576 && tokens % 1_048_576 == 0 {
        format!("{}m", tokens / 1_048_576)
    } else if tokens >= 1_024 && tokens % 1_024 == 0 {
        format!("{}k", tokens / 1_024)
    } else {
        tokens.to_string()
    }
}

fn handle_index_command(args: &[String]) -> Result<()> {
    match args.first().map(String::as_str) {
        Some("refresh") | Some("rebuild") => {
            let workspace = require_project_dir_string()?;
            let text = refresh_context_text(workspace)?;
            print_panel(
                "CONTEXT LOOM INDEX",
                &text.lines().map(str::to_string).collect::<Vec<_>>(),
                PanelTone::Cyan,
            );
            Ok(())
        }
        None | Some("status") => show_context_status(),
        Some(other) => Err(anyhow!("Unknown index command: {other}. Use `proto-cli index refresh`.")),
    }
}

fn show_context_status() -> Result<()> {
    let workspace = require_project_dir_string()?;
    let text = context_status_text(workspace)?;
    print_panel(
        "CONTEXT LOOM",
        &text.lines().map(str::to_string).collect::<Vec<_>>(),
        PanelTone::Magenta,
    );
    Ok(())
}

fn show_context_pack(query: &str) -> Result<()> {
    let workspace = require_project_dir_string()?;
    let raw = call_context_pack(query.to_string(), workspace)
        .map_err(|err| anyhow!("Python Context Loom error: {err:?}"))?;
    let value: Value = serde_json::from_str(&raw)?;
    print_panel(
        "CONTEXT LOOM PACK",
        &context_pack_rows(&value),
        PanelTone::Magenta,
    );
    if let Some(items) = value.get("items").and_then(Value::as_array) {
        for item in items.iter().take(6) {
            let title = item
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or("context item");
            print_panel(title, &context_item_rows(item), PanelTone::Dim);
        }
    }
    Ok(())
}

fn show_sessions() -> Result<()> {
    print_panel("SESSIONS", &sessions::session_panel_rows(), PanelTone::Magenta);
    for session in sessions::recent_sessions().into_iter().take(5) {
        print_panel(&session.name, &wrap_lines(&sessions::session_detail(&session), panel_inner_width()), PanelTone::Dim);
    }
    Ok(())
}

fn print_agent_graph() {
    let rows = vec![
        "[USER]".to_string(),
        "   |".to_string(),
        "   v".to_string(),
        "[CONTEXT LOOM] deterministic workspace index and evidence pack".to_string(),
        "   |".to_string(),
        "   v".to_string(),
        "[ARCHITECT] intent, routing, approval gate".to_string(),
        "   |".to_string(),
        "   +--> [EXPLORER] context pack, read_file, list_directory, search_regex, git status".to_string(),
        "   |".to_string(),
        "   +--> [CODER] generate_unified_diff, create_new_file".to_string(),
        "   |".to_string(),
        "   v".to_string(),
        "[HUMAN APPROVAL] before writes land on disk".to_string(),
    ];
    print_panel("AGENT DECK", &rows, PanelTone::Magenta);
}

fn load_inventory() -> Result<ModelInventory> {
    load_inventory_with_validation(false)
}

fn load_inventory_with_validation(validate_api_keys: bool) -> Result<ModelInventory> {
    let json = call_list_models(validate_api_keys)
        .map_err(|err| anyhow!("Python model discovery error: {err:?}"))?;
    Ok(serde_json::from_str(&json)?)
}

fn load_visible_config() -> Result<VisibleConfig> {
    let json = call_no_args("get_config").map_err(|err| anyhow!("Python config error: {err:?}"))?;
    Ok(serde_json::from_str(&json)?)
}

fn load_doctor() -> Result<DoctorReport> {
    let json = call_doctor(workspace_dir_string()).map_err(|err| anyhow!("Python doctor error: {err:?}"))?;
    Ok(serde_json::from_str(&json)?)
}

pub(crate) fn context_status_text(workspace: String) -> Result<String> {
    let raw = call_context_status(workspace).map_err(|err| anyhow!("Python Context Loom error: {err:?}"))?;
    let value: Value = serde_json::from_str(&raw)?;
    let mut rows = vec![context_memory_text()];
    rows.extend(context_status_rows(&value));
    Ok(rows.join("\n"))
}

pub(crate) fn refresh_context_text(workspace: String) -> Result<String> {
    let raw = call_refresh_context(workspace).map_err(|err| anyhow!("Python Context Loom error: {err:?}"))?;
    let value: Value = serde_json::from_str(&raw)?;
    Ok(context_status_rows(&value).join("\n"))
}

pub(crate) fn context_pack_text(query: String, workspace: String) -> Result<String> {
    let raw = call_context_pack(query, workspace).map_err(|err| anyhow!("Python Context Loom error: {err:?}"))?;
    let value: Value = serde_json::from_str(&raw)?;
    let mut rows = context_pack_rows(&value);
    if let Some(items) = value.get("items").and_then(Value::as_array) {
        for item in items.iter().take(6) {
            rows.push(String::new());
            rows.extend(context_item_rows(item));
        }
    }
    Ok(rows.join("\n"))
}

fn context_status_rows(value: &Value) -> Vec<String> {
    vec![
        format!("Name       : {}", value_str(value, "name")),
        format!("Workspace  : {}", value_str(value, "workspace")),
        format!("Index      : {}", value_str(value, "index_path")),
        format!("Files      : {}", value_u64(value, "files_indexed")),
        format!("Indexed at : {}", empty_as_unknown(&value_str(value, "indexed_at"))),
        format!(
            "Duration   : {} ms",
            value_u64_any(value, &["duration_ms", "last_duration_ms"])
        ),
        format!("Updated    : {}", value_u64(value, "files_updated")),
        format!("Removed    : {}", value_u64(value, "files_removed")),
        format!("Skipped    : {}", value_u64(value, "files_skipped")),
    ]
}

fn context_pack_rows(value: &Value) -> Vec<String> {
    let index = value.get("index").unwrap_or(&Value::Null);
    let item_count = value
        .get("items")
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or(0);
    let terms = value
        .get("terms")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .take(16)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    vec![
        format!("Query      : {}", value_str(value, "query")),
        format!("Workspace  : {}", value_str(value, "workspace")),
        format!("Items      : {}", item_count),
        format!("Terms      : {}", if terms.is_empty() { "none" } else { terms.as_str() }),
        format!(
            "Index      : {} file(s), {} ms",
            value_u64(index, "files_indexed"),
            value_u64_any(index, &["duration_ms", "last_duration_ms"])
        ),
        format!(
            "Git        : {} changed path(s)",
            value
                .get("git")
                .and_then(|git| git.get("status"))
                .and_then(Value::as_array)
                .map(|items| items.len())
                .unwrap_or(0)
        ),
    ]
}

fn context_item_rows(item: &Value) -> Vec<String> {
    let mut rows = vec![
        format!("Path     : {}", value_str(item, "path")),
        format!("Language : {}", value_str(item, "language")),
        format!("Score    : {}", value_u64(item, "score")),
        format!("Reason   : {}", value_str(item, "reason")),
        format!("Evidence : {}", value_array_strings(item, "evidence", 4).join("; ")),
    ];
    let symbols = value_array_strings(item, "symbols", 10);
    if !symbols.is_empty() {
        rows.push(format!("Symbols  : {}", symbols.join(", ")));
    }
    let headings = value_array_strings(item, "headings", 6);
    if !headings.is_empty() {
        rows.push(format!("Headings : {}", headings.join(", ")));
    }
    let line_range = value_str(item, "line_range");
    if !line_range.is_empty() {
        rows.push(format!("Lines    : {}", line_range));
    }
    let snippet = value_str(item, "snippet");
    if !snippet.trim().is_empty() {
        rows.push("Snippet  :".to_string());
        rows.extend(wrap_lines(&snippet, panel_inner_width()).into_iter().take(12));
    }
    rows
}

fn value_str(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn value_u64(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or(0)
}

fn value_u64_any(value: &Value, keys: &[&str]) -> u64 {
    keys.iter().find_map(|key| value.get(*key).and_then(Value::as_u64)).unwrap_or(0)
}

fn value_array_strings(value: &Value, key: &str, limit: usize) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .take(limit)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

#[derive(Clone, Copy)]
enum PanelTone {
    Magenta,
    Cyan,
    Yellow,
    Dim,
}

fn print_panel(title: &str, rows: &[String], tone: PanelTone) {
    let width = terminal_width();
    let inner = width.saturating_sub(4).max(24);
    let title_text = format!(" {} ", title);
    let line_len = width.saturating_sub(title_text.len() + 3).max(2);
    let top = format!("+{}{}+", title_text, repeat_char('-', line_len));
    println!("{}", tone_style(&top, tone).bold());
    if rows.is_empty() {
        println!("| {:<inner$} |", "", inner = inner);
    }
    for row in rows {
        for wrapped in wrap_lines(row, inner) {
            print_panel_row(&truncate_plain(&wrapped, inner), inner);
        }
    }
    println!("{}", tone_style(&format!("+{}+", repeat_char('-', width.saturating_sub(2))), tone).bold());
    println!();
}

fn print_panel_row(row: &str, inner: usize) {
    print!("| ");
    print_inline_code_segments(row);
    let used = row.chars().count();
    if used < inner {
        print!("{}", repeat_char(' ', inner - used));
    }
    println!(" |");
}

fn print_inline_code_line(line: &str) {
    print_inline_code_segments(line);
    println!();
}

fn print_inline_code_segments(text_value: &str) {
    for segment in inline_code_segments(text_value) {
        match segment.kind {
            InlineKind::Text => print!("{}", segment.text),
            InlineKind::Code => print!("{}", style(segment.text).black().on_yellow().bold()),
        }
    }
}

fn render_brand_header() {
    let width = terminal_width();
    println!();
    println!("{}", style(repeat_char('=', width)).magenta().bold());
    println!("{}", style(APP_TITLE).bold().magenta());
    println!("{}", style(TAGLINE).cyan().bold());
    println!("{}", style("Architect -> Explorer -> Coder // approval-gated local ops").dim());
    println!("{}", style(repeat_char('=', width)).magenta().bold());
    println!();
}

fn tone_style(text: &str, tone: PanelTone) -> console::StyledObject<&str> {
    match tone {
        PanelTone::Magenta => style(text).magenta(),
        PanelTone::Cyan => style(text).cyan(),
        PanelTone::Yellow => style(text).yellow(),
        PanelTone::Dim => style(text).dim(),
    }
}

fn terminal_width() -> usize {
    let (_rows, cols) = Term::stdout().size();
    (cols as usize).clamp(72, 118)
}

fn panel_inner_width() -> usize {
    terminal_width().saturating_sub(4).max(24)
}

fn wrap_lines(text: &str, width: usize) -> Vec<String> {
    let mut out = Vec::new();
    for raw_line in text.lines() {
        let mut line = raw_line.trim_end().to_string();
        if line.is_empty() {
            out.push(String::new());
            continue;
        }
        while line.chars().count() > width {
            let split = split_at_width(&line, width);
            out.push(split.0.trim_end().to_string());
            line = split.1.trim_start().to_string();
        }
        out.push(line);
    }
    if out.is_empty() {
        out.push(String::new());
    }
    out
}

fn split_at_width(text: &str, width: usize) -> (String, String) {
    let mut split_byte = text.len();
    let mut last_space = None;
    for (idx, (byte_idx, ch)) in text.char_indices().enumerate() {
        if idx >= width {
            split_byte = last_space.unwrap_or(byte_idx);
            break;
        }
        if ch.is_whitespace() {
            last_space = Some(byte_idx);
        }
    }
    let (left, right) = text.split_at(split_byte);
    (left.to_string(), right.to_string())
}

fn truncate_plain(text: &str, width: usize) -> String {
    let count = text.chars().count();
    if count <= width {
        return text.to_string();
    }
    if width <= 3 {
        return repeat_char('.', width);
    }
    let mut value: String = text.chars().take(width - 3).collect();
    value.push_str("...");
    value
}

fn repeat_char(ch: char, width: usize) -> String {
    std::iter::repeat(ch).take(width).collect()
}

fn empty_as_unknown(value: &str) -> &str {
    if value.is_empty() {
        "unknown"
    } else {
        value
    }
}

pub(crate) fn response_actor(response: &CoreResponse) -> String {
    if response.responder.trim().is_empty() {
        "Architect".to_string()
    } else {
        title_case_agent(&response.responder)
    }
}

fn title_case_agent(value: &str) -> String {
    let mut chars = value.trim().chars();
    let Some(first) = chars.next() else {
        return "Agent".to_string();
    };
    format!("{}{}", first.to_uppercase(), chars.as_str())
}

fn call_no_args(function: &str) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr(function)?.call0()?.extract()
    })
}

fn call_list_models(validate_api_keys: bool) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("list_models")?.call1((validate_api_keys,))?.extract()
    })
}

fn call_process_prompt_with_progress(
    prompt: String,
    workspace: String,
    session_id: Option<String>,
    progress_path: String,
) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("process_prompt")?
            .call1((prompt, workspace, session_id, progress_path))?
            .extract()
    })
}

fn call_add_api_key(provider: String, api_key: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("add_api_key")?.call1((provider, api_key))?.extract()
    })
}

fn call_set_model(provider: String, model: String, base_url: Option<String>) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("set_model")?
            .call1((provider, model, base_url))?
            .extract()
    })
}

fn call_configure_context_window(value: Option<String>) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("configure_context_window")?
            .call1((value,))?
            .extract()
    })
}

fn call_answer_help_question(question: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("answer_help_question")?.call1((question,))?.extract()
    })
}

fn call_compact_protolink_history(
    session_id: String,
    strategy: String,
    limit: Option<usize>,
) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("compact_protolink_history")?
            .call1((session_id, strategy, limit))?
            .extract()
    })
}

fn call_reset_protolink_history(session_id: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("reset_protolink_history")?
            .call1((session_id,))?
            .extract()
    })
}

fn call_describe_protolink_history(session_id: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("describe_protolink_history")?
            .call1((session_id,))?
            .extract()
    })
}

fn call_doctor(workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("doctor")?.call1((workspace,))?.extract()
    })
}

fn call_context_status(workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("context_status")?.call1((workspace,))?.extract()
    })
}

fn call_refresh_context(workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("refresh_context")?.call1((workspace,))?.extract()
    })
}

fn call_context_pack(query: String, workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("context_pack")?.call1((query, workspace))?.extract()
    })
}

fn prepare_python_path(py: Python<'_>) -> PyResult<()> {
    let sys = py.import("sys")?;
    sys.setattr("dont_write_bytecode", true)?;
    let path = sys.getattr("path")?;
    for candidate in python_path_candidates() {
        if candidate.exists() {
            path.call_method1("insert", (0, candidate.to_string_lossy().to_string()))?;
        }
    }
    Ok(())
}

fn python_path_candidates() -> Vec<PathBuf> {
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let repo = repo_root_from(&cwd).unwrap_or(cwd.clone());
    vec![
        repo.join("core"),
        repo.join(".venv/lib/python3.14/site-packages"),
        repo.join(".venv/lib/python3.13/site-packages"),
        repo.join(".venv/lib/python3.12/site-packages"),
        cwd.join("python"),
    ]
}

fn repo_root_from(cwd: &Path) -> Option<PathBuf> {
    let mut current = Some(cwd);
    while let Some(path) = current {
        if path.join("whitepaper.md").exists() && path.join("core").exists() {
            return Some(path.to_path_buf());
        }
        current = path.parent();
    }
    None
}

pub(crate) fn require_project_dir_string() -> Result<String> {
    active_project_dir()
        .map(|path| path.to_string_lossy().to_string())
        .ok_or_else(|| anyhow!("No project selected. Run `proto-cli project set PATH` or use `/project` in the TUI."))
}

pub(crate) fn active_project_dir() -> Option<PathBuf> {
    let config = load_project_config();
    let path = config.active_project?;
    let path = PathBuf::from(path);
    if path.is_dir() {
        Some(path)
    } else {
        None
    }
}

pub(crate) fn stored_project_dir() -> Option<PathBuf> {
    load_project_config().active_project.map(PathBuf::from)
}

pub(crate) fn project_label() -> String {
    match stored_project_dir() {
        Some(path) if path.is_dir() => path.to_string_lossy().to_string(),
        Some(path) => format!("missing: {} - use /project", path.to_string_lossy()),
        None => "not selected - use /project".to_string(),
    }
}

pub(crate) fn project_short_label() -> String {
    match stored_project_dir() {
        Some(path) if path.is_dir() => path
            .file_name()
            .and_then(|name| name.to_str())
            .map(str::to_string)
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| path.to_string_lossy().to_string()),
        Some(_) => "missing project".to_string(),
        None => "no project".to_string(),
    }
}

pub(crate) fn project_config_path() -> PathBuf {
    protoagent_config_dir().join("project.json")
}

pub(crate) fn project_session_id(workspace: &str) -> String {
    let mut hasher = DefaultHasher::new();
    workspace.hash(&mut hasher);
    format!("protoagent-project-{:016x}", hasher.finish())
}

pub(crate) fn context_session_id(workspace: &str) -> Option<String> {
    context_memory_enabled().then(|| project_session_id(workspace))
}

pub(crate) fn context_memory_enabled() -> bool {
    load_project_config().context_memory_enabled.unwrap_or(true)
}

pub(crate) fn set_context_memory_enabled(enabled: bool) -> Result<()> {
    let mut config = load_project_config();
    config.context_memory_enabled = Some(enabled);
    save_project_config(&config)
}

pub(crate) fn context_memory_text() -> String {
    if context_memory_enabled() {
        "Memory  : on - project sessions persist through ProtoLink state. Use /context off for fresh task-local runs.".to_string()
    } else {
        "Memory  : off - each task starts with task-local ProtoLink state. Use /context on to resume project memory.".to_string()
    }
}

pub(crate) fn set_context_memory_text(enabled: bool) -> Result<String> {
    set_context_memory_enabled(enabled)?;
    Ok(context_memory_text())
}

pub(crate) fn set_active_project(input: &str) -> Result<PathBuf> {
    let selected = resolve_project_path(input)?;
    let selected_label = selected.to_string_lossy().to_string();
    let mut config = load_project_config();
    config.active_project = Some(selected_label.clone());
    config.recent_projects.retain(|path| path != &selected_label);
    config.recent_projects.insert(0, selected_label);
    config.recent_projects.truncate(12);
    save_project_config(&config)?;
    Ok(selected)
}

pub(crate) fn clear_active_project() -> Result<()> {
    let mut config = load_project_config();
    config.active_project = None;
    save_project_config(&config)
}

pub(crate) fn default_launch_workspace() -> PathBuf {
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.file_name().and_then(|name| name.to_str()) == Some("cli") {
        if let Some(parent) = cwd.parent() {
            if parent.join("whitepaper.md").exists() {
                return parent.to_path_buf();
            }
        }
    }
    cwd
}

fn workspace_dir_string() -> String {
    active_project_dir()
        .unwrap_or_else(default_launch_workspace)
        .to_string_lossy()
        .to_string()
}

fn load_project_config() -> ProjectConfig {
    let path = project_config_path();
    let Ok(raw) = fs::read_to_string(path) else {
        return ProjectConfig::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn save_project_config(config: &ProjectConfig) -> Result<()> {
    let path = project_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let raw = serde_json::to_string_pretty(config)?;
    fs::write(path, raw)?;
    Ok(())
}

fn resolve_project_path(input: &str) -> Result<PathBuf> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Err(anyhow!("Project path cannot be empty"));
    }
    let expanded = expand_home(trimmed);
    let path = if expanded.is_absolute() {
        expanded
    } else {
        default_launch_workspace().join(expanded)
    };
    let resolved = path
        .canonicalize()
        .map_err(|err| anyhow!("Could not open project folder `{}`: {err}", path.to_string_lossy()))?;
    if !resolved.is_dir() {
        return Err(anyhow!("Project path is not a folder: {}", resolved.to_string_lossy()));
    }
    Ok(resolved)
}

fn expand_home(input: &str) -> PathBuf {
    if input == "~" {
        return home_dir().unwrap_or_else(|| PathBuf::from(input));
    }
    if let Some(rest) = input.strip_prefix("~/") {
        if let Some(home) = home_dir() {
            return home.join(rest);
        }
    }
    PathBuf::from(input)
}

fn protoagent_config_dir() -> PathBuf {
    if let Ok(value) = env::var("PROTOAGENT_CONFIG_DIR") {
        if !value.trim().is_empty() {
            return expand_home(&value);
        }
    }
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".protoagent")
}

fn home_dir() -> Option<PathBuf> {
    env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
}
