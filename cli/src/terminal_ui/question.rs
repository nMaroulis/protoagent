use anyhow::Result;
use crossterm::event::{poll, read, Event, KeyCode};
use std::time::Duration;

use crate::progress::{ProgressFile, RuntimeQuestion};
use crate::question::{QuestionEditor, QuestionReply, QUESTION_KEYS};

use super::modal::draw_input_modal;
use super::state::TerminalApp;
use super::TerminalSurface;

pub(super) fn question_prompt(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    progress: &ProgressFile,
    question: &RuntimeQuestion,
) -> Result<QuestionReply> {
    let mut editor = QuestionEditor::new();
    let mut modal_dimensions = None;
    let mut offset = 0usize;
    let mut dirty = true;
    loop {
        if !progress.question_pending(question) {
            return Ok(QuestionReply::Expired);
        }
        let (width, height) = super::theme::size();
        if dirty || modal_dimensions != Some((width, height)) {
            let columns = (width.saturating_mul(3) / 4).saturating_sub(6).max(1) as usize;
            let mut body = crate::wrap_lines(&question.question, columns);
            if !question.options.is_empty() {
                body.push("Suggestions (Tab to use one):".into());
                for option in &question.options {
                    body.extend(crate::wrap_lines(&format!("• {option}"), columns));
                }
            }
            let hint = if editor.notice.is_empty() {
                QUESTION_KEYS
            } else {
                &editor.notice
            };
            let hints = crate::wrap_lines(hint, columns);
            let count = height.saturating_sub(11 + hints.len() as u16).max(1) as usize;
            offset = offset.min(body.len().saturating_sub(count));
            let mut rows: Vec<_> = body.iter().skip(offset).take(count).cloned().collect();
            rows.extend(hints);
            if body.len() > count {
                rows.push("PageUp/PageDown scroll question".into());
            }
            terminal.prepare_modal(app, &mut modal_dimensions)?;
            draw_input_modal("Architect has a question", &rows, &editor.input, false)?;
            dirty = false;
        }
        // The worker continues on its own thread. Polling lets native timeout,
        // cancellation and budget expiry close the modal without another key.
        if poll(Duration::from_millis(super::INPUT_POLL_MS))? {
            let event = read()?;
            match &event {
                Event::Key(key) if key.code == KeyCode::PageUp => offset = offset.saturating_sub(5),
                Event::Key(key) if key.code == KeyCode::PageDown => {
                    offset = offset.saturating_add(5)
                }
                _ => {
                    if let Some(reply) = editor.handle(event, question) {
                        return Ok(reply);
                    }
                }
            }
            dirty = true;
        }
    }
}
