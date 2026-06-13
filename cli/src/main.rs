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

mod progress;
mod sessions;
mod terminal_ui;
mod timeline;

use progress::{latest_progress_message, ProgressFile};

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
    requires_approval: bool,
    #[serde(default)]
    actions: Vec<Value>,
    #[serde(default)]
    events: Vec<String>,
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
    error: String,
}

#[derive(Debug, Deserialize)]
struct AgentManifest {
    name: String,
    role: String,
    tools: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
struct ProjectConfig {
    active_project: Option<String>,
    #[serde(default)]
    recent_projects: Vec<String>,
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
        Some("sessions") | Some("session") => {
            print_header()?;
            show_sessions()
        }
        Some("help") | Some("--help") | Some("-h") => {
            print_cli_help();
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
    println!("  proto-cli sessions           Show saved project sessions");
    println!();
}

async fn run_orchestration(query: &str) -> Result<CoreResponse> {
    render_run_banner(query);

    let prompt = query.to_string();
    let workspace = require_project_dir_string()?;
    let session_id = project_session_id(&workspace);

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

    if response.requires_approval || !response.actions.is_empty() {
        render_approval_gate(&response);
        let approve = Confirm::new("Apply the proposed action payloads?")
            .with_default(false)
            .prompt()?;
        if approve {
            apply_actions(&response.actions, &response.workspace)?;
        } else {
            println!("{}", style("Approval denied. No files changed.").yellow().bold());
        }
    }

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
    print_panel("RUN SUMMARY", &summary, PanelTone::Magenta);

    if !response.events.is_empty() {
        render_agent_trace(&response.events);
        render_agent_timeline(&response.events);
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

    if !response.actions.is_empty() {
        print_panel(
            "PENDING ACTIONS",
            &[format!("{} approval payload(s) waiting at the gate", response.actions.len())],
            PanelTone::Yellow,
        );
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

fn render_agent_trace(events: &[String]) {
    println!("{}", style("AGENT TRACE").bold().underlined().cyan());
    for (idx, event) in events.iter().enumerate() {
        let label = match idx % 3 {
            0 => style("ARCH").magenta().bold(),
            1 => style("EXPL").cyan().bold(),
            _ => style("CODE").yellow().bold(),
        };
        println!("  {} {} {}", style(format!("{:02}", idx + 1)).dim(), label, event);
    }
    println!();
}

fn render_agent_timeline(events: &[String]) {
    println!("{}", style("AGENT TIMELINE").bold().underlined().cyan());
    for line in timeline::format_timeline(events, 18).lines() {
        println!("  {}", line);
    }
    println!();
}

fn render_diff(diff: &str) {
    println!("{}", style("PROPOSED DIFF").bold().underlined().cyan());
    for line in diff.lines() {
        if line.starts_with('+') && !line.starts_with("+++") {
            println!("{}", style(line).green());
        } else if line.starts_with('-') && !line.starts_with("---") {
            println!("{}", style(line).red());
        } else if line.starts_with("@@") {
            println!("{}", style(line).cyan().bold());
        } else {
            println!("{}", style(line).dim());
        }
    }
    println!();
}

fn render_approval_gate(response: &CoreResponse) {
    let mut rows = vec![
        "Human approval required before side effects are applied.".to_string(),
        format!("Workspace : {}", empty_as_unknown(&response.workspace)),
        format!("Actions   : {}", response.actions.len()),
    ];
    if !response.file_target.is_empty() {
        rows.push(format!("Target    : {}", response.file_target));
    }
    print_panel("APPROVAL GATE", &rows, PanelTone::Yellow);
}

fn apply_actions(actions: &[Value], workspace: &str) -> Result<()> {
    for action in actions {
        let action_json = serde_json::to_string(action)?;
        let result = call_apply_action(action_json, workspace.to_string())
            .map_err(|err| anyhow!("Python apply error: {err:?}"))?;
        let parsed: Value = serde_json::from_str(&result)?;
        let path = parsed
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or("(unknown path)");
        println!("{} {}", style("applied:").green().bold(), path);
    }
    Ok(())
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
    let inventory = load_inventory()?;
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
        ],
        PanelTone::Magenta,
    );

    for provider in &inventory.providers {
        render_provider_card(provider);
    }
    Ok(())
}

fn render_provider_strip(inventory: &ModelInventory) {
    let mut rows = Vec::new();
    for provider in &inventory.providers {
        rows.push(format!(
            "{:<18} {:<11} {:>2} model(s) {}",
            provider.name,
            provider.status,
            provider.models.len(),
            if provider.configured { "ready" } else { "setup" }
        ));
    }
    print_panel("PROVIDERS", &rows, PanelTone::Dim);
}

fn render_provider_card(provider: &ModelProvider) {
    let mut rows = vec![
        format!("Kind   : {}", provider.kind),
        format!("Status : {} ({})", provider.status, if provider.configured { "ready" } else { "setup" }),
    ];
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
    let tone = match provider.status.as_str() {
        "online" | "configured" | "detected" => PanelTone::Cyan,
        "needs-key" | "not-found" => PanelTone::Yellow,
        _ => PanelTone::Dim,
    };
    print_panel(&format!("{} ({})", provider.name, provider.id), &rows, tone);
}

fn choose_model(preselected_provider: Option<&str>) -> Result<()> {
    let inventory = load_inventory()?;
    let provider = match preselected_provider {
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
                        "{} ({}) - {} model(s), {}",
                        provider.name,
                        provider.id,
                        provider.models.len(),
                        provider.status
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
    print_panel("KEY STORED", &[format!("Provider: {}", provider)], PanelTone::Cyan);

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
        if provider.api_key_set {
            let source = if provider.from_env { "env" } else { "config" };
            rows.push(format!("Key   : {} ({source})", provider.api_key));
        }
        print_panel(id, &rows, PanelTone::Dim);
    }
    Ok(())
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
                        "installed {}, agent runtime ready, streaming {}",
                        empty_as_unknown(&report.protolink.version),
                        if report.protolink.streaming_ready { "ready" } else { "unavailable" }
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
        .map(|agent| format!("{} ({}) -> {}", agent.name, agent.role, agent.tools.join(", ")))
        .collect();
    print_panel("AGENTS", &rows, PanelTone::Cyan);
    Ok(())
}

fn show_agents() -> Result<()> {
    print_agent_graph();
    if let Ok(report) = load_doctor() {
        let rows: Vec<String> = report
            .agents
            .iter()
            .map(|agent| format!("{} ({}) -> {}", agent.name, agent.role, agent.tools.join(", ")))
            .collect();
        print_panel("TOOL ISOLATION", &rows, PanelTone::Cyan);
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
        "[ARCHITECT] intent, routing, approval gate".to_string(),
        "   |".to_string(),
        "   +--> [EXPLORER] read_file, list_directory, search_regex, git status".to_string(),
        "   |".to_string(),
        "   +--> [CODER] generate_unified_diff, create_new_file".to_string(),
        "   |".to_string(),
        "   v".to_string(),
        "[HUMAN APPROVAL] before writes land on disk".to_string(),
    ];
    print_panel("AGENT DECK", &rows, PanelTone::Magenta);
}

fn load_inventory() -> Result<ModelInventory> {
    let json = call_no_args("list_models").map_err(|err| anyhow!("Python model discovery error: {err:?}"))?;
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
            println!("| {:<inner$} |", truncate_plain(&wrapped, inner), inner = inner);
        }
    }
    println!("{}", tone_style(&format!("+{}+", repeat_char('-', width.saturating_sub(2))), tone).bold());
    println!();
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

fn call_process_prompt_with_progress(
    prompt: String,
    workspace: String,
    session_id: String,
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

fn call_doctor(workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr("doctor")?.call1((workspace,))?.extract()
    })
}

fn call_apply_action(action_json: String, workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("apply_action")?
            .call1((action_json, workspace))?
            .extract()
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
