use anyhow::Result;
use crossterm::event::{read, Event, KeyCode, KeyModifiers};

use crate::progress::RuntimeApproval;

use super::diff_view::{draw_approval_modal, show_diff_modal};
use super::modal::draw_modal;
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
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                return Ok(false)
            }
            KeyCode::Char('v') | KeyCode::Char('V') | KeyCode::Char('d') | KeyCode::Char('D')
                if !approval.diff.trim().is_empty() || !approval.preview.trim().is_empty() =>
            {
                if !approval.diff.trim().is_empty() {
                    show_diff_modal(terminal, app, "Approval Diff", &approval.diff)?;
                } else {
                    show_command_preview(terminal, app, &approval.preview)?;
                }
            }
            _ => {}
        }
    }
}

/// Show the complete argv/cwd preview with scrolling before approving execution.
fn show_command_preview(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    preview: &str,
) -> Result<()> {
    let mut offset = 0usize;
    loop {
        let (width, height) = super::theme::size();
        let columns = (width.saturating_mul(2) / 3).saturating_sub(8).max(1) as usize;
        let lines = crate::wrap_lines(preview, columns);
        let count = height.saturating_sub(10).max(1) as usize;
        offset = offset.min(lines.len().saturating_sub(count));
        let mut rows: Vec<_> = lines.iter().skip(offset).take(count).cloned().collect();
        rows.push("Up/Down scroll · Enter/Esc returns to approval".to_string());
        terminal.render(app, None)?;
        draw_modal("Command Preview", &rows)?;
        if let Event::Key(key) = read()? {
            match key.code {
                KeyCode::Enter | KeyCode::Esc => return Ok(()),
                KeyCode::Up => offset = offset.saturating_sub(1),
                KeyCode::Down => offset = offset.saturating_add(1),
                KeyCode::PageUp => offset = offset.saturating_sub(count),
                KeyCode::PageDown => offset = offset.saturating_add(count),
                _ => {}
            }
        }
    }
}
