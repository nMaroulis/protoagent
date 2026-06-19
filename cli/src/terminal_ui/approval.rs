use anyhow::Result;
use crossterm::event::{read, Event, KeyCode, KeyModifiers};

use crate::progress::RuntimeApproval;

use super::diff_view::{draw_approval_modal, show_diff_modal};
use super::state::TerminalApp;
use super::TerminalSurface;

pub(super) fn approval_prompt(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    approval: &RuntimeApproval,
) -> Result<bool> {
    loop {
        terminal.render(app, None)?;
        draw_approval_modal(approval)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return Ok(true),
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return Ok(false),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(false),
            KeyCode::Char('v') | KeyCode::Char('V') | KeyCode::Char('d') | KeyCode::Char('D')
                if !approval.diff.trim().is_empty() =>
            {
                show_diff_modal(terminal, app, "Approval Diff", &approval.diff)?;
            }
            _ => {}
        }
    }
}
