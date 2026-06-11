use anyhow::{anyhow, Result};
use console::{style, Term};
use crossterm::{
    cursor::{MoveToColumn, Show},
    event::{read, Event, KeyCode, KeyModifiers},
    execute, queue,
    terminal::{
        disable_raw_mode, enable_raw_mode, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen, SetTitle,
    },
};
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use inquire::{Confirm, Password, Select, Text};
use pyo3::prelude::*;
use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, VecDeque};
use std::env;
use std::io::{stdout, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

const APP_TITLE: &str = "PROTOAGENT";
const TAGLINE: &str = "MIAMI-80 LOCAL-FIRST AGENT CONSOLE";
const INPUT_HISTORY_CAPACITY: usize = 10_000;
const HISTORY_PANEL_LIMIT: usize = 200;

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

#[derive(Default)]
struct SessionState {
    turn: usize,
    last_query: String,
    last_response: Option<CoreResponse>,
    input_history: VecDeque<String>,
}

impl SessionState {
    fn remember_input(&mut self, input: &str) {
        let input = input.trim();
        if input.is_empty() {
            return;
        }
        if self.input_history.back().map(String::as_str) == Some(input) {
            return;
        }
        if self.input_history.len() >= INPUT_HISTORY_CAPACITY {
            self.input_history.pop_front();
        }
        self.input_history.push_back(input.to_string());
    }
}

struct TerminalTakeover {
    active: bool,
}

impl TerminalTakeover {
    fn enter() -> Self {
        if env::var("PROTOAGENT_NO_ALT").is_ok() || env::var("PROTOAGENT_ALT_SCREEN").is_err() {
            let mut out = stdout();
            let _ = execute!(out, Show, SetTitle("ProtoAgent // Local Agent Console"));
            return Self { active: false };
        }

        let mut out = stdout();
        let active = execute!(
            out,
            EnterAlternateScreen,
            Show,
            SetTitle("ProtoAgent // Local Agent Console"),
            Clear(ClearType::All)
        )
        .is_ok();

        Self { active }
    }
}

impl Drop for TerminalTakeover {
    fn drop(&mut self) {
        let mut out = stdout();
        if self.active {
            let _ = execute!(out, Show, LeaveAlternateScreen);
        } else {
            let _ = execute!(out, Show);
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    env::set_var("PYTHONDONTWRITEBYTECODE", "1");
    let args: Vec<String> = env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        None | Some("start") => interactive().await,
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
        Some("doctor") => {
            print_header()?;
            show_doctor()
        }
        Some("dashboard") | Some("dash") | Some("status") => {
            show_dashboard()
        }
        Some("agents") => {
            print_header()?;
            show_agents()
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

async fn interactive() -> Result<()> {
    let _takeover = TerminalTakeover::enter();
    let term = Term::stdout();
    if env::var("PROTOAGENT_ALT_SCREEN").is_ok() {
        term.clear_screen()?;
    }
    let mut state = SessionState::default();

    show_dashboard()?;
    println!(
        "{}",
        style("Press /menu for the command palette. Type a task to launch the agent deck.")
            .dim()
            .italic()
    );

    loop {
        print_status_strip(&state);
        let prompt = interactive_prompt(&state);
        let prompt_width = interactive_prompt_width(&state);
        let Some(input) = read_primary_input(&prompt, prompt_width, &state.input_history)? else {
            break;
        };
        let input = input.trim();
        if input.is_empty() {
            continue;
        }
        state.remember_input(input);

        if input.starts_with('/') {
            match handle_slash_command(input, &term, &mut state).await {
                Ok(should_continue) => {
                    if !should_continue {
                        break;
                    }
                }
                Err(err) => render_error(&err.to_string()),
            }
            continue;
        }

        state.turn += 1;
        state.last_query = input.to_string();
        match run_orchestration(input).await {
            Ok(response) => state.last_response = Some(response),
            Err(err) => render_error(&err.to_string()),
        }
    }

    println!("{}", style("Session restored to your shell.").cyan().bold());
    Ok(())
}

async fn handle_slash_command(input: &str, term: &Term, state: &mut SessionState) -> Result<bool> {
    let mut parts = input.split_whitespace();
    let command = parts.next().unwrap_or("");
    match command {
        "/quit" | "/exit" => Ok(false),
        "/clear" => {
            term.clear_screen()?;
            show_dashboard()?;
            Ok(true)
        }
        "/help" => {
            print_interactive_help();
            Ok(true)
        }
        "/menu" | "/palette" => command_palette(state).await,
        "/dashboard" | "/dash" | "/status" => {
            show_dashboard()?;
            Ok(true)
        }
        "/models" => {
            show_models()?;
            Ok(true)
        }
        "/model" | "/provider" => {
            choose_model(None)?;
            Ok(true)
        }
        "/key" => {
            add_key(parts.next())?;
            Ok(true)
        }
        "/config" => {
            show_config()?;
            Ok(true)
        }
        "/doctor" => {
            show_doctor()?;
            Ok(true)
        }
        "/agents" => {
            show_agents()?;
            Ok(true)
        }
        "/last" => {
            show_last_response(state)?;
            Ok(true)
        }
        "/history" => {
            show_input_history(state)?;
            Ok(true)
        }
        "/diff" => {
            show_last_diff(state)?;
            Ok(true)
        }
        "/run" => {
            let query = parts.collect::<Vec<_>>().join(" ");
            if query.trim().is_empty() {
                println!("{}", style("Usage: /run your task").yellow());
            } else {
                state.turn += 1;
                state.last_query = query.clone();
                state.last_response = Some(run_orchestration(&query).await?);
            }
            Ok(true)
        }
        _ => {
            println!("{}", style("Unknown slash command. Try /menu or /help.").yellow());
            Ok(true)
        }
    }
}

async fn command_palette(state: &mut SessionState) -> Result<bool> {
    let choices = vec![
        "Run task",
        "Dashboard",
        "Model radar",
        "Choose model",
        "Add API key",
        "Doctor",
        "Agent topology",
        "Config",
        "Last response",
        "Input history",
        "Last diff",
        "Clear screen",
        "Quit session",
    ];
    let selected = Select::new("Command palette", choices).prompt()?;
    match selected {
        "Run task" => {
            let query = Text::new("Task").prompt()?;
            if !query.trim().is_empty() {
                state.turn += 1;
                state.last_query = query.trim().to_string();
                state.last_response = Some(run_orchestration(query.trim()).await?);
            }
            Ok(true)
        }
        "Dashboard" => {
            show_dashboard()?;
            Ok(true)
        }
        "Model radar" => {
            show_models()?;
            Ok(true)
        }
        "Choose model" => {
            choose_model(None)?;
            Ok(true)
        }
        "Add API key" => {
            add_key(None)?;
            Ok(true)
        }
        "Doctor" => {
            show_doctor()?;
            Ok(true)
        }
        "Agent topology" => {
            show_agents()?;
            Ok(true)
        }
        "Config" => {
            show_config()?;
            Ok(true)
        }
        "Last response" => {
            show_last_response(state)?;
            Ok(true)
        }
        "Input history" => {
            show_input_history(state)?;
            Ok(true)
        }
        "Last diff" => {
            show_last_diff(state)?;
            Ok(true)
        }
        "Clear screen" => {
            Term::stdout().clear_screen()?;
            show_dashboard()?;
            Ok(true)
        }
        "Quit session" => Ok(false),
        _ => Ok(true),
    }
}

fn print_header() -> Result<()> {
    render_brand_header();
    Ok(())
}

fn print_cli_help() {
    render_brand_header();
    println!("{}", style("Commands").bold().underlined());
    println!("  proto-cli start              Enter the full-screen agent console");
    println!("  proto-cli run \"task\"         Run one task");
    println!("  proto-cli dashboard          Show cockpit status");
    println!("  proto-cli models             Show detected local/API models");
    println!("  proto-cli model              Pick active provider/model");
    println!("  proto-cli key [provider]     Add API key");
    println!("  proto-cli config             Show current config");
    println!("  proto-cli doctor             Check Python/protolink/providers");
    println!("  proto-cli agents             Show Architect/Explorer/Coder topology");
    println!();
}

fn print_interactive_help() {
    let rows = vec![
        "/menu       Command palette".to_string(),
        "/dashboard  Cockpit overview".to_string(),
        "/models     Model radar for Ollama, LM Studio, OpenAI-compatible, llama.cpp, and APIs".to_string(),
        "/model      Select active provider/model".to_string(),
        "/key        Store a cloud provider key".to_string(),
        "/doctor     Runtime checks".to_string(),
        "/agents     Agent topology and tool isolation".to_string(),
        "/last       Re-render the last response".to_string(),
        "/history    Show retained prompt history".to_string(),
        "/diff       Re-render the last proposed diff".to_string(),
        "/clear      Redraw console".to_string(),
        "/quit       Leave the console".to_string(),
    ];
    print_panel("COMMANDS", &rows, PanelTone::Cyan);
}

async fn run_orchestration(query: &str) -> Result<CoreResponse> {
    render_run_banner(query);

    let m = MultiProgress::new();
    let spinner_style = ProgressStyle::with_template("{prefix:.bold.dim} {spinner:.cyan} {msg}")?
        .tick_chars(">|/-\\");

    let pb_architect = m.add(ProgressBar::new_spinner());
    pb_architect.set_style(spinner_style.clone());
    pb_architect.set_prefix("[architect]");
    pb_architect.set_message("routing request through the Python core");
    pb_architect.enable_steady_tick(Duration::from_millis(80));

    let pb_explorer = m.add(ProgressBar::new_spinner());
    pb_explorer.set_style(spinner_style.clone());
    pb_explorer.set_prefix("[explorer]");
    pb_explorer.set_message("mapping workspace context");
    pb_explorer.enable_steady_tick(Duration::from_millis(120));

    let pb_coder = m.add(ProgressBar::new_spinner());
    pb_coder.set_style(spinner_style);
    pb_coder.set_prefix("[coder]");
    pb_coder.set_message("preparing approval-safe output");
    pb_coder.enable_steady_tick(Duration::from_millis(100));

    let prompt = query.to_string();
    let workspace = workspace_dir_string();
    let json = tokio::task::spawn_blocking(move || call_process_prompt(prompt, workspace))
        .await?
        .map_err(|err| anyhow!("Python core error: {err:?}"))?;

    pb_architect.finish_with_message("request routed");
    pb_explorer.finish_with_message("context mapped");
    pb_coder.finish_with_message("output assembled");
    m.clear()?;

    let response: CoreResponse = serde_json::from_str(&json)?;
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
    let workspace = workspace_dir_string();

    let mut rows = vec![format!("Workspace : {}", workspace)];
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
        "/doctor checks runtime wiring".to_string(),
        "Type any coding task to dispatch Architect -> Explorer -> Coder".to_string(),
    ];
    print_panel("HOTKEYS", &commands, PanelTone::Cyan);
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

fn show_doctor() -> Result<()> {
    let report = load_doctor()?;
    print_panel(
        "DOCTOR",
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

fn show_last_response(state: &SessionState) -> Result<()> {
    if let Some(response) = &state.last_response {
        render_response(response)
    } else {
        print_panel("LAST RESPONSE", &["No response in this session yet.".to_string()], PanelTone::Yellow);
        Ok(())
    }
}

fn show_last_diff(state: &SessionState) -> Result<()> {
    if let Some(response) = &state.last_response {
        if response.diff.trim().is_empty() {
            print_panel("LAST DIFF", &["No diff in the last response.".to_string()], PanelTone::Yellow);
        } else {
            render_diff(&response.diff);
        }
    } else {
        print_panel("LAST DIFF", &["No response in this session yet.".to_string()], PanelTone::Yellow);
    }
    Ok(())
}

fn show_input_history(state: &SessionState) -> Result<()> {
    if state.input_history.is_empty() {
        print_panel("INPUT HISTORY", &["No prompt history in this session yet.".to_string()], PanelTone::Yellow);
        return Ok(());
    }

    let total = state.input_history.len();
    let start = total.saturating_sub(HISTORY_PANEL_LIMIT);
    let mut rows = Vec::new();
    if start > 0 {
        rows.push(format!(
            "Showing last {} of {} retained inputs.",
            HISTORY_PANEL_LIMIT, INPUT_HISTORY_CAPACITY
        ));
    } else {
        rows.push(format!("Retaining up to {} inputs in this session.", INPUT_HISTORY_CAPACITY));
    }
    for (idx, item) in state.input_history.iter().enumerate().skip(start) {
        rows.push(format!("{:04}  {}", idx + 1, item));
    }
    print_panel("INPUT HISTORY", &rows, PanelTone::Cyan);
    Ok(())
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

fn print_status_strip(state: &SessionState) {
    let active = active_label().unwrap_or_else(|_| "provider: unknown / model: unknown".to_string());
    let last = if state.last_query.is_empty() {
        "last: none".to_string()
    } else {
        format!("last: {}", truncate_plain(&state.last_query, 34))
    };
    println!(
        "{} {} {}",
        style(format!("[turn {:02}]", state.turn + 1)).magenta().bold(),
        style(active).cyan(),
        style(last).dim()
    );
}

fn active_label() -> Result<String> {
    let config = load_visible_config()?;
    let provider = config.active_provider;
    let model = config
        .providers
        .get(&provider)
        .map(|data| data.model.as_str())
        .unwrap_or("");
    Ok(format!(
        "provider: {} / model: {}",
        provider,
        if model.is_empty() { "not selected" } else { model }
    ))
}

fn interactive_prompt(state: &SessionState) -> String {
    format!("{} ", style(format!("proto[{:02}]>", state.turn + 1)).bold().magenta())
}

fn interactive_prompt_width(state: &SessionState) -> u16 {
    format!("proto[{:02}]> ", state.turn + 1).chars().count() as u16
}

fn read_primary_input(prompt: &str, prompt_width: u16, history: &VecDeque<String>) -> Result<Option<String>> {
    execute!(stdout(), Show)?;
    if enable_raw_mode().is_err() {
        return read_primary_input_fallback(prompt);
    }

    let mut raw = RawModeGuard { active: true };
    let mut editor = LineEditor::new(prompt, prompt_width, history);
    editor.render()?;

    loop {
        let event = read()?;
        let Event::Key(key) = event else {
            continue;
        };

        match key.code {
            KeyCode::Enter => {
                let line = editor.line();
                raw.disable()?;
                println!();
                return Ok(Some(line));
            }
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                raw.disable()?;
                println!();
                return Ok(None);
            }
            KeyCode::Char('d') if key.modifiers.contains(KeyModifiers::CONTROL) && editor.is_empty() => {
                raw.disable()?;
                println!();
                return Ok(None);
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
        }
        editor.render()?;
    }
}

fn read_primary_input_fallback(prompt: &str) -> Result<Option<String>> {
    print!("{prompt}");
    stdout().flush()?;

    let mut buffer = String::new();
    let bytes = std::io::stdin().read_line(&mut buffer)?;
    if bytes == 0 {
        return Ok(None);
    }
    Ok(Some(buffer.trim_end_matches(['\r', '\n']).to_string()))
}

struct RawModeGuard {
    active: bool,
}

impl RawModeGuard {
    fn disable(&mut self) -> Result<()> {
        if self.active {
            disable_raw_mode()?;
            self.active = false;
        }
        Ok(())
    }
}

impl Drop for RawModeGuard {
    fn drop(&mut self) {
        if self.active {
            let _ = disable_raw_mode();
        }
    }
}

struct LineEditor<'a> {
    prompt: &'a str,
    prompt_width: u16,
    history: &'a VecDeque<String>,
    buffer: Vec<char>,
    cursor: usize,
    history_index: Option<usize>,
    draft: Vec<char>,
}

impl<'a> LineEditor<'a> {
    fn new(prompt: &'a str, prompt_width: u16, history: &'a VecDeque<String>) -> Self {
        Self {
            prompt,
            prompt_width,
            history,
            buffer: Vec::new(),
            cursor: 0,
            history_index: None,
            draft: Vec::new(),
        }
    }

    fn render(&self) -> Result<()> {
        let width = actual_terminal_width();
        let prompt_width = self.prompt_width as usize;
        let available = width.saturating_sub(prompt_width).max(12);
        let offset = if self.cursor >= available {
            self.cursor + 1 - available
        } else {
            0
        };
        let visible: String = self.buffer.iter().skip(offset).take(available).collect();
        let cursor_col = self.prompt_width.saturating_add((self.cursor.saturating_sub(offset)) as u16);

        let mut out = stdout();
        queue!(out, MoveToColumn(0), Clear(ClearType::CurrentLine), Show)?;
        write!(out, "{}{}", self.prompt, visible)?;
        queue!(out, MoveToColumn(cursor_col))?;
        out.flush()?;
        Ok(())
    }

    fn line(&self) -> String {
        self.buffer.iter().collect()
    }

    fn is_empty(&self) -> bool {
        self.buffer.is_empty()
    }

    fn insert(&mut self, ch: char) {
        self.buffer.insert(self.cursor, ch);
        self.cursor += 1;
        self.history_index = None;
    }

    fn insert_str(&mut self, text: &str) {
        for ch in text.chars() {
            self.insert(ch);
        }
    }

    fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.buffer.remove(self.cursor);
            self.history_index = None;
        }
    }

    fn delete(&mut self) {
        if self.cursor < self.buffer.len() {
            self.buffer.remove(self.cursor);
            self.history_index = None;
        }
    }

    fn move_left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    fn move_right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.buffer.len());
    }

    fn move_home(&mut self) {
        self.cursor = 0;
    }

    fn move_end(&mut self) {
        self.cursor = self.buffer.len();
    }

    fn history_prev(&mut self) {
        if self.history.is_empty() {
            return;
        }
        let next_index = match self.history_index {
            Some(index) => index.saturating_sub(1),
            None => {
                self.draft = self.buffer.clone();
                self.history.len() - 1
            }
        };
        self.load_history(next_index);
    }

    fn history_next(&mut self) {
        let Some(index) = self.history_index else {
            return;
        };
        if index + 1 < self.history.len() {
            self.load_history(index + 1);
        } else {
            self.history_index = None;
            self.buffer = self.draft.clone();
            self.cursor = self.buffer.len();
        }
    }

    fn load_history(&mut self, index: usize) {
        if let Some(value) = self.history.get(index) {
            self.history_index = Some(index);
            self.buffer = value.chars().collect();
            self.cursor = self.buffer.len();
        }
    }
}

fn render_error(message: &str) {
    print_panel("ERROR", &[message.to_string()], PanelTone::Yellow);
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

fn actual_terminal_width() -> usize {
    let (_rows, cols) = Term::stdout().size();
    (cols as usize).max(40)
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

fn call_no_args(function: &str) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module.getattr(function)?.call0()?.extract()
    })
}

fn call_process_prompt(prompt: String, workspace: String) -> PyResult<String> {
    Python::attach(|py| {
        prepare_python_path(py)?;
        let module = py.import("protoagent_core.agent_engine")?;
        module
            .getattr("process_prompt")?
            .call1((prompt, workspace))?
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

fn workspace_dir_string() -> String {
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.file_name().and_then(|name| name.to_str()) == Some("cli") {
        if let Some(parent) = cwd.parent() {
            if parent.join("whitepaper.md").exists() {
                return parent.to_string_lossy().to_string();
            }
        }
    }
    cwd.to_string_lossy().to_string()
}
