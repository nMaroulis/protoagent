use anyhow::{anyhow, Result};
use crossterm::event::{read, Event, KeyCode};
use serde_json::Value;

use crate::{call_apply_action, CoreResponse};

use super::diff_view::{draw_approval_modal, show_diff_modal};
use super::state::TerminalApp;
use super::TerminalSurface;

pub(super) fn approval_prompt(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    response: &CoreResponse,
) -> Result<bool> {
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

pub(super) fn apply_actions(actions: &[Value], workspace: &str) -> Result<String> {
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
