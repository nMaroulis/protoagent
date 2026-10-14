//! UI adapters for the engine's live user-question callback.

use anyhow::Result;
use crossterm::{
    cursor::MoveToColumn,
    event::{
        poll, read, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEventKind,
        KeyModifiers,
    },
    execute,
    style::Print,
    terminal::{disable_raw_mode, enable_raw_mode, Clear, ClearType},
};
use std::collections::VecDeque;
use std::io::{stdout, IsTerminal, Write};
use std::time::Duration;

use crate::progress::{ProgressFile, RuntimeQuestion};
use crate::terminal_ui::input::InputEditor;

pub(crate) const QUESTION_KEYS: &str = "Tab suggestions · Enter send · Esc skip · Ctrl-C cancel";

#[derive(Debug, PartialEq)]
pub(crate) enum QuestionReply {
    Answered(String),
    Declined,
    Expired,
    Canceled,
}

pub(crate) struct QuestionEditor {
    pub(crate) input: InputEditor,
    pub(crate) notice: String,
    next_option: usize,
}

impl QuestionEditor {
    pub(crate) fn new() -> Self {
        Self {
            input: InputEditor::new(&VecDeque::new()),
            notice: String::new(),
            next_option: 0,
        }
    }

    pub(crate) fn handle(
        &mut self,
        event: Event,
        question: &RuntimeQuestion,
    ) -> Option<QuestionReply> {
        let key = match event {
            Event::Key(key) if key.kind != KeyEventKind::Release => key,
            Event::Paste(text) => {
                self.insert(&text, question.max_answer_chars);
                return None;
            }
            _ => return None,
        };
        match key.code {
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                return Some(QuestionReply::Canceled)
            }
            KeyCode::Esc => return Some(QuestionReply::Declined),
            KeyCode::Enter => {
                let answer = self.input.line();
                if !answer.trim().is_empty() && answer.chars().count() <= question.max_answer_chars
                {
                    return Some(QuestionReply::Answered(answer));
                }
                self.notice = "Enter an answer, or press Esc to skip.".into();
            }
            KeyCode::Tab if !question.options.is_empty() => {
                self.input.replace(&question.options[self.next_option]);
                self.next_option = (self.next_option + 1) % question.options.len();
                self.notice.clear();
            }
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.insert(&ch.to_string(), question.max_answer_chars)
            }
            KeyCode::Backspace => {
                self.input.backspace();
                self.notice.clear();
            }
            KeyCode::Delete => {
                self.input.delete();
                self.notice.clear();
            }
            KeyCode::Left => self.input.move_left(),
            KeyCode::Right => self.input.move_right(),
            KeyCode::Home => self.input.move_home(),
            KeyCode::End => self.input.move_end(),
            _ => {}
        }
        None
    }

    fn insert(&mut self, text: &str, limit: usize) {
        let text: String = text
            .replace(['\n', '\r', '\t'], " ")
            .chars()
            .filter(|ch| !ch.is_control())
            .collect();
        if self.input.line().chars().count() + text.chars().count() > limit {
            self.notice =
                format!("Answer limit: {limit} characters. Shorten the text before sending.");
        } else {
            self.input.insert_str(&text);
            self.notice.clear();
        }
    }
}

/// Poll the native request's lifetime rather than leaving a blocking stdin read.
pub(crate) fn shell_question(
    progress: &ProgressFile,
    question: &RuntimeQuestion,
) -> Result<QuestionReply> {
    if !std::io::stdin().is_terminal() || !stdout().is_terminal() {
        return Ok(QuestionReply::Declined);
    }
    println!("\nArchitect has a question:\n{}", question.question);
    for option in &question.options {
        println!("  • {option}");
    }
    println!("{QUESTION_KEYS}");
    enable_raw_mode()?;
    struct RawMode;
    impl Drop for RawMode {
        fn drop(&mut self) {
            let _ = execute!(stdout(), DisableBracketedPaste);
            let _ = disable_raw_mode();
        }
    }
    let _raw_mode = RawMode;
    execute!(stdout(), EnableBracketedPaste)?;
    let mut editor = QuestionEditor::new();
    let mut dirty = true;
    let result = loop {
        if !progress.question_pending(question) {
            break QuestionReply::Expired;
        }
        if dirty {
            let width = crossterm::terminal::size()?.0.saturating_sub(4).max(1) as usize;
            let (visible, cursor) = editor.input.visible(width);
            let suffix = if editor.notice.is_empty() {
                String::new()
            } else {
                format!(" [{}]", editor.notice)
            };
            let mut out = stdout().lock();
            execute!(
                out,
                MoveToColumn(0),
                Clear(ClearType::CurrentLine),
                Print(format!("> {visible}{suffix}")),
                MoveToColumn(0),
                crossterm::cursor::MoveRight(2 + cursor as u16)
            )?;
            out.flush()?;
            dirty = false;
        }
        if poll(Duration::from_millis(40))? {
            let event = read()?;
            if let Some(reply) = editor.handle(event, question) {
                break reply;
            }
            dirty = true;
        }
    };
    execute!(
        stdout(),
        MoveToColumn(0),
        Clear(ClearType::CurrentLine),
        Print("\r\n")
    )?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyEvent;

    fn question() -> RuntimeQuestion {
        RuntimeQuestion {
            request_id: "input".into(),
            run_id: "run".into(),
            task_id: Some("task".into()),
            action_id: "action".into(),
            question: "Format?".into(),
            options: vec!["JSON".into(), "CSV".into()],
            max_answer_chars: 8,
            presentation_id: 0,
        }
    }

    fn key(code: KeyCode) -> Event {
        Event::Key(KeyEvent::new(code, KeyModifiers::NONE))
    }

    #[test]
    fn suggestions_require_explicit_selection_and_free_text_always_works() {
        let question = question();
        let mut editor = QuestionEditor::new();
        assert_eq!(editor.handle(key(KeyCode::Enter), &question), None);
        assert!(editor.input.is_empty());
        editor.handle(key(KeyCode::Tab), &question);
        assert_eq!(editor.input.line(), "JSON");
        editor.handle(key(KeyCode::Tab), &question);
        assert_eq!(editor.input.line(), "CSV");
        editor.input.replace("custom");
        assert_eq!(
            editor.handle(key(KeyCode::Enter), &question),
            Some(QuestionReply::Answered("custom".into()))
        );
    }

    #[test]
    fn paste_is_literal_bounded_and_unicode_editing_survives() {
        let question = question();
        let mut editor = QuestionEditor::new();
        editor.handle(Event::Paste("界\n@x\x1b".into()), &question);
        assert_eq!(editor.input.line(), "界 @x");
        editor.handle(Event::Paste("too long".into()), &question);
        assert_eq!(editor.input.line(), "界 @x");
        assert!(!editor.notice.is_empty());
        editor.handle(key(KeyCode::Home), &question);
        editor.handle(key(KeyCode::Delete), &question);
        assert_eq!(editor.input.line(), " @x");
    }

    #[test]
    fn skipping_and_canceling_are_distinct() {
        let mut editor = QuestionEditor::new();
        assert_eq!(
            editor.handle(key(KeyCode::Esc), &question()),
            Some(QuestionReply::Declined)
        );
        let cancel = Event::Key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL));
        assert_eq!(
            editor.handle(cancel, &question()),
            Some(QuestionReply::Canceled)
        );
    }
}
