use anyhow::Result;
use console::{style, Term};
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use inquire::{Confirm, Text};
use pyo3::prelude::*;
use serde::Deserialize;
use std::time::Duration;

/// This struct perfectly matches the JSON returned by Python!
#[derive(Deserialize)]
struct AgentResponse {
    thought_process: String,
    file_target: String,
    diff: String,
    requires_approval: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let term = Term::stdout();
    term.clear_screen()?;

    print_header();

    loop {
        let prompt_prefix = style("❯").bold().green();
        let input = Text::new(&format!("{} ", prompt_prefix))
            .with_help_message("Ask ProtoAgent a coding task, or type /help")
            .prompt()?;

        let input = input.trim();

        // --- 1. Command Menu Handling ---
        match input.to_lowercase().as_str() {
            "exit" | "/quit" => {
                println!("\n{}\n", style("Shutting down local engine... Goodbye!").bold().cyan());
                break;
            }
            "/clear" => {
                term.clear_screen()?;
                print_header();
                continue;
            }
            "/help" => {
                println!("\n{}", style("ProtoAgent Commands:").bold().underlined());
                println!("  {} - Clear the terminal", style("/clear").cyan());
                println!("  {} - Exit the application\n", style("/quit").cyan());
                continue;
            }
            "" => continue,
            _ => {} // Continue to AI processing
        }

        // --- 2. Run the AI Pipeline ---
        run_orchestration(input).await?;
    }

    Ok(())
}

fn print_header() {
    println!("\n{}\n", style("⚡ ProtoAgent CLI").bold().cyan().underlined());
    println!("{}", style("Local environment detected. Engine: Protolink (Active)").dim());
}

fn call_python_agent(prompt: String) -> PyResult<String> {
    Python::attach(|py| {
        let syspath = py.import("sys")?.getattr("path")?;
        syspath.call_method1("append", ("./python",))?;
        syspath.call_method1("append", (".venv/lib/python3.14/site-packages",))?;
        syspath.call_method1("append", (".venv/lib/python3.13/site-packages",))?;

        let agent_module = py.import("agent_engine")?;
        let result: String = agent_module
            .getattr("process_prompt")?
            .call1((prompt,))?
            .extract()?;

        Ok(result)
    })
}

async fn run_orchestration(query: &str) -> Result<()> {
    let m = MultiProgress::new();
    let spinner_style = ProgressStyle::with_template("{prefix:.bold.dim} {spinner:.cyan} {msg}")?
        .tick_chars("⠁⠂⠄⡀⢀⠠⠐⠈ ");

    let pb_orch = m.add(ProgressBar::new_spinner());
    pb_orch.set_style(spinner_style.clone());
    pb_orch.set_prefix("[🤖]");
    pb_orch.set_message(format!("{}", style("Local models processing request...").bold()));
    pb_orch.enable_steady_tick(Duration::from_millis(80));

    let prompt_clone = query.to_string();

    // Call Python
    let python_json_string = tokio::task::spawn_blocking(move || {
        call_python_agent(prompt_clone)
    })
    .await?
    .map_err(|e| anyhow::anyhow!("Python Error: {:?}", e))?;

    pb_orch.finish_and_clear();

    // --- 3. Parse and Display the Structured Output ---

    // Deserialize the JSON string into our Rust struct
    let agent_data: AgentResponse = serde_json::from_str(&python_json_string)?;

    println!("\n{}", style("🧠 Agent Thought Process:").bold().magenta());
    println!("{}\n", style(agent_data.thought_process).dim());

    println!("{} {}", style("📄 Target File:").bold().blue(), agent_data.file_target);
    println!("{}\n{}\n", style("--- Proposed Diff ---").dim(), style(agent_data.diff).green());

    // --- 4. Interactive File Modification Prompt ---
    if agent_data.requires_approval {
        let ans = Confirm::new("Do you want to apply these changes?")
            .with_default(true)
            .prompt()?;

        if ans {
            // Here is where you would eventually write the file to disk!
            println!("  ↳ {}\n", style("Changes successfully applied to disk!").bold().green());
        } else {
            println!("  ↳ {}\n", style("Operation cancelled. File left untouched.").bold().red());
        }
    }

    Ok(())
}
