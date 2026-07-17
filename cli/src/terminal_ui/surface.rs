use anyhow::Result;
use crossterm::{
    cursor::{Hide, MoveTo, Show},
    event::{
        read, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyModifiers, MouseEventKind,
    },
    execute, queue,
    style::ResetColor,
    terminal::{
        disable_raw_mode, enable_raw_mode, Clear, ClearType, DisableLineWrap, EnableLineWrap,
        EnterAlternateScreen, LeaveAlternateScreen, SetTitle,
    },
};
use std::io::{stdout, Write};
use std::time::{Duration, Instant};

use super::input::InputEditor;
use super::modal::draw_exit_modal;
use super::project::{format_file_tag, pick_project_file};
use super::render::{draw_header, draw_input, draw_transcript};
use super::state::{PanelView, Role, TerminalApp};
use super::theme::size;
use super::{HEADER_ROWS, INPUT_ROWS, WHEEL_LINES};

pub(super) struct TerminalSurface {
    active: bool,
    suppress_exit_escape_until: Option<Instant>,
}

impl TerminalSurface {
    pub(super) fn enter() -> Result<Self> {
        enable_raw_mode()?;
        let enter_result = execute!(
            stdout(),
            Clear(ClearType::Purge),
            EnterAlternateScreen,
            DisableLineWrap,
            EnableMouseCapture,
            Hide,
            SetTitle("ProtoAgent Terminal"),
            Clear(ClearType::All),
            Clear(ClearType::Purge)
        );
        if let Err(err) = enter_result {
            let _ = disable_raw_mode();
            return Err(err.into());
        }
        Ok(Self {
            active: true,
            suppress_exit_escape_until: None,
        })
    }

    pub(super) fn leave(&mut self) -> Result<()> {
        if self.active {
            let leave_result = execute!(
                stdout(),
                ResetColor,
                Show,
                DisableMouseCapture,
                EnableLineWrap,
                Clear(ClearType::All),
                LeaveAlternateScreen
            );
            let raw_result = disable_raw_mode();
            self.active = false;
            leave_result?;
            raw_result?;
        }
        Ok(())
    }

    pub(super) fn render(&mut self, app: &TerminalApp, editor: Option<&InputEditor>) -> Result<()> {
        let (width, height) = size();
        let mut out = stdout();
        queue!(out, Hide)?;
        draw_header(&mut out, width, app)?;
        draw_transcript(&mut out, width, height, app)?;
        let cursor = draw_input(&mut out, width, height, app, editor)?;
        if editor.is_some() {
            queue!(out, MoveTo(cursor.0, cursor.1), Show, ResetColor)?;
        } else {
            queue!(out, Hide, ResetColor)?;
        }
        out.flush()?;
        Ok(())
    }

    pub(super) fn read_input(&mut self, app: &mut TerminalApp) -> Result<Option<String>> {
        let mut editor = InputEditor::new(&app.input_history);
        // Mouse movement can be high-volume in some terminals, so repaint only after visible state changes.
        self.render(app, Some(&editor))?;
        loop {
            let mut needs_render = false;
            match read()? {
                Event::Key(key) => match key.code {
                    KeyCode::Enter => return Ok(Some(editor.line())),
                    KeyCode::Esc => {
                        if self.exit_escape_is_suppressed() {
                            continue;
                        }
                        if self.confirm_exit(app)? {
                            return Ok(None);
                        }
                        needs_render = true;
                    }
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        return Ok(None)
                    }
                    KeyCode::Char('d')
                        if key.modifiers.contains(KeyModifiers::CONTROL) && editor.is_empty() =>
                    {
                        return Ok(None);
                    }
                    KeyCode::PageUp => {
                        let before = app.scroll_offset;
                        app.scroll_up(chat_page_size());
                        needs_render = app.scroll_offset != before;
                    }
                    KeyCode::PageDown => {
                        let before = app.scroll_offset;
                        app.scroll_down(chat_page_size());
                        needs_render = app.scroll_offset != before;
                    }
                    KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        let before = app.scroll_offset;
                        app.scroll_up(100_000);
                        needs_render = app.scroll_offset != before;
                    }
                    KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        let before = app.scroll_offset;
                        app.jump_to_bottom();
                        needs_render = app.scroll_offset != before;
                    }
                    KeyCode::Char('@') if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                        if crate::active_project_dir().is_none() {
                            app.panel = PanelView::Project;
                            app.refresh(None);
                            app.push(
                                Role::Error,
                                "@",
                                "Choose a project with /project before tagging files.",
                            );
                            needs_render = true;
                        } else if let Some(path) = pick_project_file(self, app)? {
                            editor.insert_str(&format_file_tag(&path));
                            needs_render = true;
                        } else {
                            needs_render = true;
                        }
                    }
                    KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.insert(ch);
                        needs_render = true;
                    }
                    KeyCode::Backspace => {
                        editor.backspace();
                        needs_render = true;
                    }
                    KeyCode::Delete => {
                        editor.delete();
                        needs_render = true;
                    }
                    KeyCode::Left => {
                        editor.move_left();
                        needs_render = true;
                    }
                    KeyCode::Right => {
                        editor.move_right();
                        needs_render = true;
                    }
                    KeyCode::Home => {
                        editor.move_home();
                        needs_render = true;
                    }
                    KeyCode::End => {
                        editor.move_end();
                        needs_render = true;
                    }
                    KeyCode::Up => {
                        editor.history_prev();
                        needs_render = true;
                    }
                    KeyCode::Down => {
                        editor.history_next();
                        needs_render = true;
                    }
                    KeyCode::Tab => {
                        editor.insert_str("  ");
                        needs_render = true;
                    }
                    _ => {}
                },
                Event::Mouse(mouse) => match mouse.kind {
                    MouseEventKind::ScrollUp => {
                        let before = app.scroll_offset;
                        app.scroll_up(WHEEL_LINES);
                        needs_render = app.scroll_offset != before;
                    }
                    MouseEventKind::ScrollDown => {
                        let before = app.scroll_offset;
                        app.scroll_down(WHEEL_LINES);
                        needs_render = app.scroll_offset != before;
                    }
                    _ => {}
                },
                Event::Resize(_, _) => needs_render = true,
                _ => {}
            }
            if needs_render {
                self.render(app, Some(&editor))?;
            }
        }
    }

    pub(super) fn suppress_exit_escape(&mut self) {
        self.suppress_exit_escape_until = Some(Instant::now() + Duration::from_millis(500));
    }

    fn exit_escape_is_suppressed(&mut self) -> bool {
        let suppressed = self
            .suppress_exit_escape_until
            .map(|until| Instant::now() < until)
            .unwrap_or(false);
        if !suppressed {
            self.suppress_exit_escape_until = None;
        }
        suppressed
    }

    fn confirm_exit(&mut self, app: &TerminalApp) -> Result<bool> {
        loop {
            self.render(app, None)?;
            draw_exit_modal()?;
            match read()? {
                Event::Key(key) => {
                    return Ok(matches!(key.code, KeyCode::Esc)
                        || matches!(key.code, KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL)));
                }
                Event::Resize(_, _) => continue,
                _ => {}
            }
        }
    }
}

impl Drop for TerminalSurface {
    fn drop(&mut self) {
        let _ = self.leave();
    }
}

fn chat_page_size() -> usize {
    let (_, height) = size();
    height
        .saturating_sub(HEADER_ROWS + INPUT_ROWS)
        .saturating_sub(1)
        .max(1) as usize
}
