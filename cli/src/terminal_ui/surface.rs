use anyhow::Result;
use crossterm::{
    cursor::{DisableBlinking, EnableBlinking, Hide, MoveTo, Show},
    event::{
        poll, read, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste,
        EnableMouseCapture, Event, KeyCode, KeyModifiers, MouseEventKind,
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

use super::commands::matching_commands;
use super::input::InputEditor;
use super::modal::{draw_exit_modal, pick_choice_modal};
use super::project::{format_file_tag, pick_project_file};
use super::render::{
    composer_height, draw_header, draw_input, draw_runtime_status, draw_transcript, TranscriptCache,
};
use super::state::{PanelView, Role, TerminalApp};
use super::theme::size;
use super::{HEADER_ROWS, WHEEL_LINES};

pub(super) struct TerminalSurface {
    active: bool,
    suppress_exit_escape_until: Option<Instant>,
    transcript: TranscriptCache,
    dimensions: Option<(u16, u16)>,
    header_frame: Vec<u8>,
    input_frame: Vec<u8>,
    status_frame: Vec<u8>,
    input_height: u16,
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
            EnableBracketedPaste,
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
            transcript: TranscriptCache::default(),
            dimensions: None,
            header_frame: Vec::new(),
            input_frame: Vec::new(),
            status_frame: Vec::new(),
            input_height: 0,
        })
    }

    pub(super) fn leave(&mut self) -> Result<()> {
        if self.active {
            let leave_result = execute!(
                stdout(),
                ResetColor,
                EnableBlinking,
                Show,
                DisableMouseCapture,
                DisableBracketedPaste,
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
        self.render_frame(app, editor, true)
    }

    pub(super) fn render_streaming(&mut self, app: &TerminalApp) -> Result<()> {
        self.render_frame(app, None, false)
    }

    pub(super) fn prepare_modal(
        &mut self,
        app: &TerminalApp,
        painted_size: &mut Option<(u16, u16)>,
    ) -> Result<()> {
        let current = size();
        if *painted_size != Some(current) {
            self.render(app, None)?;
            *painted_size = Some(current);
        }
        Ok(())
    }

    fn render_frame(
        &mut self,
        app: &TerminalApp,
        editor: Option<&InputEditor>,
        force: bool,
    ) -> Result<()> {
        let frame = self.compose_frame(app, editor, force, size())?;
        if frame.is_empty() {
            return Ok(());
        }
        let mut out = stdout().lock();
        out.write_all(&frame)?;
        out.flush()?;
        Ok(())
    }

    fn compose_frame(
        &mut self,
        app: &TerminalApp,
        editor: Option<&InputEditor>,
        force: bool,
        (width, height): (u16, u16),
    ) -> Result<Vec<u8>> {
        let input_height = composer_height(width, editor);
        let force =
            force || self.dimensions != Some((width, height)) || self.input_height != input_height;
        // Compose before touching the terminal, then submit one buffered write
        // instead of exposing each drawing command as a partial update.
        let mut out = Vec::new();
        // Supported terminals present the complete frame together, avoiding
        // a flash between clearing transcript rows and repainting the answer.
        let frame = (|| -> Result<()> {
            if width < 40 || height < HEADER_ROWS + input_height + 1 {
                self.draw_small_terminal(&mut out, width, height, force)?;
                return Ok(());
            }
            let mut header = Vec::new();
            draw_header(&mut header, width, app)?;
            if force || header != self.header_frame {
                out.write_all(&header)?;
                self.header_frame = header;
            }
            draw_transcript(
                &mut out,
                width,
                height.saturating_sub(composer_height(width, editor)),
                app,
                &mut self.transcript,
                force,
            )?;
            let mut input = Vec::new();
            let cursor = draw_input(&mut input, width, height, app, editor)?;
            if force || input != self.input_frame {
                out.write_all(&input)?;
                self.input_frame = input;
            }
            let mut status = Vec::new();
            draw_runtime_status(&mut status, width, height, app)?;
            if force || status != self.status_frame {
                out.write_all(&status)?;
                self.status_frame = status;
            }
            if let Some(editor) = editor {
                if editor.is_empty() {
                    queue!(out, DisableBlinking)?;
                } else {
                    queue!(out, EnableBlinking)?;
                }
                queue!(out, MoveTo(cursor.0, cursor.1), Show, ResetColor)?;
            }
            Ok(())
        })();
        if frame.is_ok() {
            self.dimensions = Some((width, height));
            self.input_height = input_height;
        }
        frame?;
        if out.is_empty() {
            return Ok(out);
        }
        let mut synchronized = Vec::with_capacity(out.len() + 40);
        synchronized.write_all(b"\x1b[?2026h")?;
        queue!(synchronized, DisableLineWrap, Hide)?;
        synchronized.extend(out);
        queue!(synchronized, ResetColor)?;
        synchronized.write_all(b"\x1b[?2026l")?;
        Ok(synchronized)
    }

    fn draw_small_terminal(
        &mut self,
        out: &mut Vec<u8>,
        width: u16,
        height: u16,
        force: bool,
    ) -> Result<()> {
        if !force {
            return Ok(());
        }
        use super::theme::{bg, cyan, muted, write_line};
        for row in 0..height {
            write_line(out, row, width, "", muted(), bg(), false)?;
        }
        if height > 0 {
            write_line(
                out,
                0,
                width,
                "ProtoAgent · resize terminal",
                cyan(),
                bg(),
                false,
            )?;
        }
        if height > 1 {
            write_line(
                out,
                1,
                width,
                "Minimum 40 columns × 16 rows",
                muted(),
                bg(),
                false,
            )?;
        }
        Ok(())
    }

    pub(super) fn read_input(&mut self, app: &mut TerminalApp) -> Result<Option<String>> {
        let mut editor = InputEditor::new(&app.input_history);
        // Mouse movement can be high-volume in some terminals, so repaint only after visible state changes.
        self.render_frame(app, Some(&editor), false)?;
        let blink_period = Duration::from_millis(700);
        let mut next_blink = Instant::now() + blink_period;
        let mut cursor_visible = true;
        loop {
            if editor.is_empty() && !poll(next_blink.saturating_duration_since(Instant::now()))? {
                // Blink only the hardware cursor; idle frames and model state
                // do not need to be repainted for the placeholder animation.
                cursor_visible = !cursor_visible;
                if cursor_visible {
                    execute!(stdout(), Show)?;
                } else {
                    execute!(stdout(), Hide)?;
                }
                next_blink = Instant::now() + blink_period;
                continue;
            }
            let mut needs_render = false;
            let mut force_render = false;
            match read()? {
                Event::Key(key) => match key.code {
                    KeyCode::Char('l') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        needs_render = true;
                        force_render = true;
                    }
                    KeyCode::Char('j') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.insert('\n');
                        needs_render = true;
                    }
                    KeyCode::Enter
                        if key
                            .modifiers
                            .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT) =>
                    {
                        editor.insert('\n');
                        needs_render = true;
                    }
                    KeyCode::Enter => return Ok(Some(editor.line())),
                    KeyCode::Char('p') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.history_prev();
                        needs_render = true;
                    }
                    KeyCode::Char('n') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        editor.history_next();
                        needs_render = true;
                    }
                    KeyCode::Char('r') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                        force_render = true;
                        let history: Vec<_> =
                            app.input_history.iter().rev().take(1000).cloned().collect();
                        let choices: Vec<_> = history
                            .iter()
                            .map(|item| item.replace('\n', " ↵ "))
                            .collect();
                        if let Some(index) = pick_choice_modal(
                            self,
                            app,
                            "Prompt History",
                            &["Type to filter · Enter recalls without submitting".to_string()],
                            &choices,
                            0,
                        )? {
                            editor.replace(&history[index]);
                        }
                        needs_render = true;
                    }
                    KeyCode::Esc => {
                        if self.exit_escape_is_suppressed() {
                            continue;
                        }
                        if self.confirm_exit(app)? {
                            return Ok(None);
                        }
                        needs_render = true;
                        force_render = true;
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
                        app.scroll_up(chat_page_size(&editor));
                        needs_render = app.scroll_offset != before;
                    }
                    KeyCode::PageDown => {
                        let before = app.scroll_offset;
                        app.scroll_down(chat_page_size(&editor));
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
                        force_render = true;
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
                        if !editor.move_vertical(false, size().0.saturating_sub(7).max(1) as usize)
                        {
                            editor.history_prev();
                        }
                        needs_render = true;
                    }
                    KeyCode::Down => {
                        if !editor.move_vertical(true, size().0.saturating_sub(7).max(1) as usize) {
                            editor.history_next();
                        }
                        needs_render = true;
                    }
                    KeyCode::Tab => {
                        force_render = true;
                        if editor.line().starts_with('/') && !editor.line().contains('\n') {
                            let matches = matching_commands(&editor.line());
                            let choices: Vec<_> = matches
                                .iter()
                                .map(|(command, help)| format!("{command}  — {help}"))
                                .collect();
                            if let Some(index) = pick_choice_modal(
                                self,
                                app,
                                "Commands",
                                &["Enter inserts a command; it does not run it".to_string()],
                                &choices,
                                0,
                            )? {
                                editor.replace(&format!("{} ", matches[index].0));
                            }
                        } else {
                            editor.insert_str("  ");
                        }
                        needs_render = true;
                    }
                    _ => {}
                },
                Event::Paste(text) => {
                    editor.insert_str(&text);
                    needs_render = true;
                }
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
                self.render_frame(app, Some(&editor), force_render)?;
                cursor_visible = true;
                next_blink = Instant::now() + blink_period;
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

fn chat_page_size(editor: &InputEditor) -> usize {
    let (width, height) = size();
    height
        .saturating_sub(HEADER_ROWS + composer_height(width, Some(editor)))
        .saturating_sub(1)
        .max(1) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_surface() -> TerminalSurface {
        TerminalSurface {
            active: false,
            suppress_exit_escape_until: None,
            transcript: TranscriptCache::default(),
            dimensions: None,
            header_frame: Vec::new(),
            input_frame: Vec::new(),
            status_frame: Vec::new(),
            input_height: 0,
        }
    }

    #[test]
    fn unchanged_frames_write_nothing_and_force_restores_every_region() {
        let mut terminal = test_surface();
        let mut app = TerminalApp::for_test();
        app.push(Role::User, "You", "Keep the earlier question visible.");
        app.begin_response("guide");
        let paint = |terminal: &mut TerminalSurface, force| {
            String::from_utf8(terminal.compose_frame(&app, None, force, (80, 30)).unwrap()).unwrap()
        };
        let first = paint(&mut terminal, false);
        assert!(first.starts_with("\x1b[?2026h"));
        assert!(first.ends_with("\x1b[?2026l"));
        assert!(first.contains("PROTOAGENT TERMINAL"));
        assert!(first.contains("Keep the earlier question visible."));
        // Ordinary spinner ticks keep the existing incremental rendering.
        assert!(paint(&mut terminal, false).is_empty());
        let restored = paint(&mut terminal, true);
        assert!(restored.contains("PROTOAGENT TERMINAL"));
        assert!(restored.contains("Keep the earlier question visible."));
        assert!(restored.contains("Esc / Ctrl-C cancel"));
        assert!(restored.contains("PROJECT"));
        assert!(!restored.contains("\x1b[2J"));
    }

    #[test]
    fn typing_and_activity_only_paint_changed_regions() {
        let mut terminal = test_surface();
        let mut app = TerminalApp::for_test();
        app.push(Role::User, "You", "Existing conversation");
        let mut editor = InputEditor::new(&app.input_history);
        let first = terminal
            .compose_frame(&app, Some(&editor), false, (100, 30))
            .unwrap();
        editor.insert_str("next question");
        let typed = terminal
            .compose_frame(&app, Some(&editor), false, (100, 30))
            .unwrap();
        let text = String::from_utf8(typed.clone()).unwrap();
        assert!(text.contains("next question"));
        assert!(!text.contains("Existing conversation"));
        assert!(!text.contains("PROTOAGENT TERMINAL"));
        assert!(
            typed.len() < first.len() / 3,
            "typed={} full={}",
            typed.len(),
            first.len()
        );
        app.activity = "connecting".into();
        let activity = String::from_utf8(
            terminal
                .compose_frame(&app, None, false, (100, 30))
                .unwrap(),
        )
        .unwrap();
        assert!(activity.contains("connecting"));
        let activity = String::from_utf8(
            terminal
                .compose_frame(&app, None, false, (100, 30))
                .unwrap(),
        )
        .unwrap();
        assert!(activity.is_empty());
    }

    #[test]
    fn tiny_terminal_stays_in_bounds_and_resize_restores_conversation() {
        let mut terminal = test_surface();
        let mut app = TerminalApp::for_test();
        app.push(Role::User, "You", "Conversation survives resize");
        for (width, height) in [(0, 0), (1, 1), (20, 5), (80, 12)] {
            let frame = terminal
                .compose_frame(&app, None, false, (width, height))
                .unwrap();
            let frame = String::from_utf8(frame).unwrap();
            assert!(!frame.contains("Conversation survives resize"));
            for escape in frame.split("\x1b[").skip(1) {
                if let Some(position) = escape.split('H').next() {
                    if let Some((row, column)) = position.split_once(';') {
                        if let (Ok(row), Ok(column)) = (row.parse::<u16>(), column.parse::<u16>()) {
                            assert!(row <= height && column <= width.max(1));
                        }
                    }
                }
            }
        }
        let restored = String::from_utf8(
            terminal
                .compose_frame(&app, None, false, (100, 30))
                .unwrap(),
        )
        .unwrap();
        assert!(restored.contains("Conversation survives resize"));
        assert!(restored.contains("PROTOAGENT TERMINAL"));
    }
}
