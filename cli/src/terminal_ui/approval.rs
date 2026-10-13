use anyhow::Result;
use crossterm::event::{poll, read, Event, KeyCode, KeyModifiers};
use std::time::Duration;

use crate::progress::{ProgressFile, RuntimeApproval};

use super::diff_view::{draw_approval_modal, show_approval_diff};
use super::modal::draw_modal;
use super::state::TerminalApp;
use super::TerminalSurface;

pub(super) fn approval_prompt(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    approval: &RuntimeApproval,
    progress: &ProgressFile,
) -> Result<Option<bool>> {
    let mut modal_dimensions = None;
    let mut dirty = true;
    loop {
        if !progress.approval_pending(approval) {
            return Ok(None);
        }
        if dirty || modal_dimensions != Some(super::theme::size()) {
            terminal.prepare_modal(app, &mut modal_dimensions)?;
            draw_approval_modal(approval)?;
            dirty = false;
        }
        if !poll(Duration::from_millis(super::INPUT_POLL_MS))? {
            continue;
        }
        let Event::Key(key) = read()? else {
            dirty = true;
            continue;
        };
        match key.code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return Ok(Some(true)),
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return Ok(Some(false)),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                return Ok(Some(false))
            }
            KeyCode::Char('v') | KeyCode::Char('V') | KeyCode::Char('d') | KeyCode::Char('D')
                if !approval.diff.trim().is_empty() || !approval.preview.trim().is_empty() =>
            {
                if !approval.diff.trim().is_empty() {
                    if let Some(decision) = show_approval_diff(terminal, app, approval, progress)? {
                        return Ok(Some(decision));
                    }
                } else {
                    if let Some(decision) = show_command_preview(terminal, app, approval, progress)?
                    {
                        return Ok(Some(decision));
                    }
                }
                modal_dimensions = None;
                dirty = true;
            }
            _ => {}
        }
    }
}

/// Show the complete argv/cwd preview with scrolling before approving execution.
fn show_command_preview(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    approval: &RuntimeApproval,
    progress: &ProgressFile,
) -> Result<Option<bool>> {
    let mut offset = 0usize;
    let mut modal_dimensions = None;
    let mut dirty = true;
    loop {
        if !progress.approval_pending(approval) {
            return Ok(None);
        }
        let (width, height) = super::theme::size();
        let columns = (width.saturating_mul(2) / 3).saturating_sub(8).max(1) as usize;
        let lines = crate::wrap_lines(&approval.preview, columns);
        let count = height.saturating_sub(12).max(1) as usize;
        offset = offset.min(lines.len().saturating_sub(count));
        let mut rows: Vec<_> = lines.iter().skip(offset).take(count).cloned().collect();
        rows.push("Viewing details has not approved or run this action.".to_string());
        rows.push("Y approve execution · N deny · Enter/Esc back · Up/Down scroll".to_string());
        if dirty || modal_dimensions != Some((width, height)) {
            terminal.prepare_modal(app, &mut modal_dimensions)?;
            draw_modal(
                if approval.is_command() {
                    "Command details — approval still required"
                } else {
                    "Action details — approval still required"
                },
                &rows,
            )?;
            dirty = false;
        }
        if !poll(Duration::from_millis(super::INPUT_POLL_MS))? {
            continue;
        }
        let event = read()?;
        if let Event::Key(key) = event {
            match key.code {
                KeyCode::Enter | KeyCode::Esc => return Ok(None),
                KeyCode::Char('y') | KeyCode::Char('Y') => return Ok(Some(true)),
                KeyCode::Char('n') | KeyCode::Char('N') => return Ok(Some(false)),
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    return Ok(Some(false))
                }
                KeyCode::Up => offset = offset.saturating_sub(1),
                KeyCode::Down => offset = offset.saturating_add(1),
                KeyCode::PageUp => offset = offset.saturating_sub(count),
                KeyCode::PageDown => offset = offset.saturating_add(count),
                _ => {}
            }
        }
        dirty = true;
    }
}
