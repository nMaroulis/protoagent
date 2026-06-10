use anyhow::{anyhow, Result};
use console::{style, Term};
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use inquire::{Confirm, Password, Select, Text};
use pyo3::prelude::*;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Deserialize)]
struct CoreResponse {
    status: String,
    #[serde(default)]
    headline: String,
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
    error: String,
}

#[derive(Debug, Deserialize)]
struct AgentManifest {
    name: String,
    role: String,
    tools: Vec<String>,
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
            run_orchestration(&query).await
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
    let term = Term::stdout();
    term.clear_screen()?;
    print_header()?;
    println!("{}", style("Type /help for commands. Type a task to run the agent deck.").dim());

    loop {
        let prompt = format!("{} ", style("proto>").bold().cyan());
        let input = Text::new(&prompt)
            .with_help_message("task, /models, /model, /key, /doctor, /quit")
            .prompt()?;
        let input = input.trim();
        if input.is_empty() {
            continue;
        }

        if input.starts_with('/') {
            match handle_slash_command(input, &term).await {
                Ok(should_continue) => {
                    if !should_continue {
                        break;
                    }
                }
                Err(err) => println!("{} {err}", style("error:").red().bold()),
            }
            continue;
        }

        run_orchestration(input).await?;
    }

    println!("{}", style("Neon link closed.").cyan().bold());
    Ok(())
}

async fn handle_slash_command(input: &str, term: &Term) -> Result<bool> {
    let mut parts = input.split_whitespace();
    let command = parts.next().unwrap_or("");
    match command {
        "/quit" | "/exit" => Ok(false),
        "/clear" => {
            term.clear_screen()?;
            print_header()?;
            Ok(true)
        }
        "/help" => {
            print_interactive_help();
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
        "/run" => {
            let query = parts.collect::<Vec<_>>().join(" ");
            if query.trim().is_empty() {
                println!("{}", style("Usage: /run your task").yellow());
            } else {
                run_orchestration(&query).await?;
            }
            Ok(true)
        }
        _ => {
            println!("{}", style("Unknown slash command. Try /help.").yellow());
            Ok(true)
        }
    }
}

fn print_header() -> Result<()> {
    println!();
    println!("{}", style("PROTOAGENT").bold().magenta());
    println!("{}", style("NEON LOCAL AGENT DECK // MIAMI-80 TERMINAL MODE").cyan());
    println!("{}", style("Architect -> Explorer -> Coder | powered by core/protoagent_core").dim());
    println!();
    Ok(())
}

fn print_cli_help() {
    println!("{}", style("ProtoAgent CLI").bold().cyan());
    println!("  proto-cli start              Start interactive mode");
    println!("  proto-cli run \"task\"         Run one task");
    println!("  proto-cli models             Show detected local/API models");
    println!("  proto-cli model              Pick active provider/model");
    println!("  proto-cli key [provider]     Add API key");
    println!("  proto-cli config             Show current config");
    println!("  proto-cli doctor             Check Python/protolink/providers");
}

fn print_interactive_help() {
    println!();
    println!("{}", style("Commands").bold().underlined());
    println!("  {}   Browse Ollama, LM Studio, llama.cpp, and API models", style("/models").cyan());
    println!("  {}    Select active provider and model", style("/model").cyan());
    println!("  {}      Add an API key for OpenAI, Anthropic, Gemini, or DeepSeek", style("/key").cyan());
    println!("  {}   Show redacted config", style("/config").cyan());
    println!("  {}   Check Python core, protolink, and active provider", style("/doctor").cyan());
    println!("  {}    Clear the terminal", style("/clear").cyan());
    println!("  {}     Exit", style("/quit").cyan());
    println!();
}

async fn run_orchestration(query: &str) -> Result<()> {
    let m = MultiProgress::new();
    let spinner_style = ProgressStyle::with_template("{prefix:.bold.dim} {spinner:.cyan} {msg}")?
        .tick_chars(">|/-\\");

    let pb = m.add(ProgressBar::new_spinner());
    pb.set_style(spinner_style);
    pb.set_prefix("[deck]");
    pb.set_message("Architect is routing through the Python core...");
    pb.enable_steady_tick(Duration::from_millis(90));

    let prompt = query.to_string();
    let workspace = workspace_dir_string();
    let json = tokio::task::spawn_blocking(move || call_process_prompt(prompt, workspace))
        .await?
        .map_err(|err| anyhow!("Python core error: {err:?}"))?;

    pb.finish_and_clear();
    let response: CoreResponse = serde_json::from_str(&json)?;
    render_response(&response)?;

    if response.requires_approval || !response.actions.is_empty() {
        let approve = Confirm::new("Apply the proposed action payloads?")
            .with_default(false)
            .prompt()?;
        if approve {
            apply_actions(&response.actions, &response.workspace)?;
        } else {
            println!("{}", style("No files changed.").yellow().bold());
        }
    }

    Ok(())
}

fn render_response(response: &CoreResponse) -> Result<()> {
    println!(
        "{} {}",
        style("status:").bold().cyan(),
        style(&response.status).bold()
    );
    if !response.headline.is_empty() {
        println!("{} {}", style("headline:").bold().magenta(), response.headline);
    }
    if !response.provider.is_empty() {
        let model = if response.model.is_empty() {
            "not selected"
        } else {
            response.model.as_str()
        };
        println!("{} {} / {}", style("model:").bold().cyan(), response.provider, model);
    }
    if response.elapsed_ms > 0 {
        println!("{} {} ms", style("elapsed:").bold().cyan(), response.elapsed_ms);
    }
    if !response.warning.is_empty() {
        println!("{} {}", style("warning:").yellow().bold(), response.warning);
    }

    if !response.events.is_empty() {
        println!();
        println!("{}", style("Agent Trace").bold().underlined());
        for event in &response.events {
            println!("  {} {}", style(">").magenta(), event);
        }
    }

    if !response.thought_process.is_empty() {
        println!();
        println!("{}", style("Core Notes").bold().underlined());
        println!("{}", style(&response.thought_process).dim());
    }

    if !response.file_target.is_empty() {
        println!();
        println!("{} {}", style("target:").bold().cyan(), response.file_target);
    }

    if !response.diff.trim().is_empty() {
        println!();
        println!("{}", style("Proposed Diff").bold().underlined());
        println!("{}", style(&response.diff).green());
    }

    if !response.actions.is_empty() {
        println!();
        println!(
            "{} {} pending action(s)",
            style("approval:").bold().yellow(),
            response.actions.len()
        );
    }
    println!();
    Ok(())
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

fn show_models() -> Result<()> {
    let inventory = load_inventory()?;
    println!("{}", style("Model Radar").bold().underlined());
    println!(
        "{} {} / {}",
        style("active:").cyan().bold(),
        inventory.active_provider,
        if inventory.active_model.is_empty() {
            "not selected"
        } else {
            inventory.active_model.as_str()
        }
    );
    println!("{} {}", style("config:").cyan().bold(), inventory.config_path);
    println!();

    for provider in &inventory.providers {
        let status = status_style(&provider.status);
        println!(
            "{} {} [{}] {} {}",
            style(&provider.name).bold().magenta(),
            style(format!("({})", provider.id)).dim(),
            provider.kind,
            status,
            if provider.configured {
                style("ready").green().dim().to_string()
            } else {
                style("setup").yellow().dim().to_string()
            }
        );
        if !provider.base_url.is_empty() {
            println!("  base: {}", style(&provider.base_url).dim());
        }
        if provider.models.is_empty() {
            let hint = if provider.hint.is_empty() {
                "No models reported."
            } else {
                provider.hint.as_str()
            };
            println!("  {}", style(hint).yellow());
        } else {
            for model in provider.models.iter().take(10) {
                let size = if model.size_label.is_empty() {
                    String::new()
                } else {
                    format!(" {}", style(format!("[{}]", model.size_label)).dim())
                };
                let source = if model.source.is_empty() {
                    String::new()
                } else {
                    format!(" {}", style(format!("via {}", model.source)).dim())
                };
                let modified = if model.modified_at.is_empty() {
                    String::new()
                } else {
                    format!(" {}", style(&model.modified_at).dim())
                };
                println!("  - {}{}{}{}", model.id, size, source, modified);
            }
            if provider.models.len() > 10 {
                println!("  {}", style(format!("...and {} more", provider.models.len() - 10)).dim());
            }
        }
        println!();
    }
    Ok(())
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
    println!(
        "{} {} / {}",
        style("selected:").green().bold(),
        provider.id,
        model
    );
    Ok(())
}

fn add_key(preselected_provider: Option<&str>) -> Result<()> {
    let providers = vec![
        "openai".to_string(),
        "anthropic".to_string(),
        "gemini".to_string(),
        "deepseek".to_string(),
    ];
    let provider = match preselected_provider {
        Some(value) if providers.iter().any(|provider| provider == value) => value.to_string(),
        Some(value) => return Err(anyhow!("{value} is not a supported API-key provider")),
        None => Select::new("Cloud provider", providers).prompt()?,
    };

    let api_key = Password::new("API key")
        .without_confirmation()
        .prompt()?;
    call_add_api_key(provider.clone(), api_key)
        .map_err(|err| anyhow!("Python config error: {err:?}"))?;
    println!("{} {}", style("stored key for").green().bold(), provider);

    if Confirm::new("Choose a model for this provider now?")
        .with_default(true)
        .prompt()?
    {
        choose_model(Some(&provider))?;
    }
    Ok(())
}

fn show_config() -> Result<()> {
    let json = call_no_args("get_config").map_err(|err| anyhow!("Python config error: {err:?}"))?;
    let config: VisibleConfig = serde_json::from_str(&json)?;
    println!("{}", style("Configuration").bold().underlined());
    println!("{} {}", style("path:").cyan().bold(), config.config_path);
    println!("{} {}", style("active:").cyan().bold(), config.active_provider);
    println!();

    let mut providers: Vec<_> = config.providers.iter().collect();
    providers.sort_by(|a, b| a.0.cmp(b.0));
    for (id, provider) in providers {
        println!("{} {}", style(id).bold().magenta(), provider.label);
        println!(
            "  model: {}",
            if provider.model.is_empty() {
                style("not selected").yellow().to_string()
            } else {
                provider.model.clone()
            }
        );
        if !provider.base_url.is_empty() {
            println!("  base: {}", style(&provider.base_url).dim());
        }
        if provider.api_key_set {
            let source = if provider.from_env { "env" } else { "config" };
            println!("  key: {} ({source})", provider.api_key);
        }
    }
    println!();
    Ok(())
}

fn show_doctor() -> Result<()> {
    let json = call_doctor(workspace_dir_string()).map_err(|err| anyhow!("Python doctor error: {err:?}"))?;
    let report: DoctorReport = serde_json::from_str(&json)?;
    println!("{}", style("Doctor").bold().underlined());
    println!("{} {}", style("python:").cyan().bold(), report.python);
    println!("{} {}", style("platform:").cyan().bold(), report.platform);
    println!("{} {}", style("workspace:").cyan().bold(), report.workspace);
    println!("{} {}", style("config:").cyan().bold(), report.config_path);
    println!(
        "{} {}",
        style("protolink:").cyan().bold(),
        if report.protolink.installed {
            style("installed").green().to_string()
        } else {
            style(format!("missing ({})", report.protolink.error)).yellow().to_string()
        }
    );
    println!(
        "{} {} / {} [{}]",
        style("active:").cyan().bold(),
        report.active_provider,
        if report.active_model.is_empty() {
            "not selected"
        } else {
            report.active_model.as_str()
        },
        report.active_provider_status
    );
    println!();
    println!("{}", style("Agents").bold().underlined());
    for agent in report.agents {
        println!(
            "  {} {} - {}",
            style(agent.name).magenta().bold(),
            style(format!("({})", agent.role)).dim(),
            agent.tools.join(", ")
        );
    }
    println!();
    Ok(())
}

fn load_inventory() -> Result<ModelInventory> {
    let json = call_no_args("list_models").map_err(|err| anyhow!("Python model discovery error: {err:?}"))?;
    Ok(serde_json::from_str(&json)?)
}

fn status_style(status: &str) -> String {
    match status {
        "online" | "configured" | "detected" => style(status).green().bold().to_string(),
        "needs-key" | "not-found" => style(status).yellow().bold().to_string(),
        _ => style(status).red().bold().to_string(),
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
        module.getattr("process_prompt")?.call1((prompt, workspace))?.extract()
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
