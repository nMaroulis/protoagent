use anyhow::Result;
use crossterm::{
    cursor::{MoveTo, Show},
    event::{read, Event, KeyCode, KeyModifiers},
    queue,
    style::{Print, SetBackgroundColor, SetForegroundColor},
};
use std::collections::VecDeque;
use std::io::{stdout, Stdout, Write};

use super::input::InputEditor;
use super::state::TerminalApp;
use super::theme::{
    black, clip_plain, cyan, input_bg, modal_bg, modal_border, modal_list_bg, modal_selection_bg, modal_shadow_bg,
    muted, size, text, write_at, yellow,
};
use super::TerminalSurface;

pub(super) fn prompt_line_modal(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    title: &str,
    rows: &[String],
    initial: &str,
) -> Result<Option<String>> {
    let history = VecDeque::new();
    let mut editor = InputEditor::with_initial(&history, initial);
    loop {
        terminal.render(app, None)?;
        draw_input_modal(title, rows, &editor)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => return Ok(Some(editor.line())),
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => editor.insert(ch),
            KeyCode::Backspace => editor.backspace(),
            KeyCode::Delete => editor.delete(),
            KeyCode::Left => editor.move_left(),
            KeyCode::Right => editor.move_right(),
            KeyCode::Home => editor.move_home(),
            KeyCode::End => editor.move_end(),
            _ => {}
        }
    }
}

pub(super) fn pick_choice_modal(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    title: &str,
    rows: &[String],
    choices: &[String],
    initial: usize,
) -> Result<Option<usize>> {
    if choices.is_empty() {
        return Ok(None);
    }

    let mut filter = String::new();
    let mut selected = initial.min(choices.len().saturating_sub(1));
    loop {
        let matches = filtered_choices(choices, &filter);
        selected = selected.min(matches.len().saturating_sub(1));
        terminal.render(app, None)?;
        draw_choice_picker_modal(title, rows, &filter, &matches, selected)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => {
                if let Some((original_index, _)) = matches.get(selected) {
                    return Ok(Some(*original_index));
                }
            }
            KeyCode::Esc => return Ok(None),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(None),
            KeyCode::Backspace => {
                filter.pop();
                selected = 0;
            }
            KeyCode::Up => selected = selected.saturating_sub(1),
            KeyCode::Down => {
                if selected + 1 < matches.len() {
                    selected += 1;
                }
            }
            KeyCode::PageUp => selected = selected.saturating_sub(8),
            KeyCode::PageDown => selected = (selected + 8).min(matches.len().saturating_sub(1)),
            KeyCode::Home => selected = 0,
            KeyCode::End => selected = matches.len().saturating_sub(1),
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                filter.push(ch);
                selected = 0;
            }
            _ => {}
        }
    }
}

pub(super) fn draw_modal_backdrop(_out: &mut Stdout, _width: u16, _height: u16) -> Result<()> {
    Ok(())
}

pub(super) fn draw_modal_shadow(out: &mut Stdout, x: u16, y: u16, width: u16, height: u16) -> Result<()> {
    let (screen_width, screen_height) = size();
    let shadow_x = x.saturating_add(2);
    let shadow_y = y.saturating_add(1);
    let shadow_width = width.min(screen_width.saturating_sub(shadow_x));
    if shadow_width == 0 {
        return Ok(());
    }
    for row in shadow_y..shadow_y.saturating_add(height).min(screen_height) {
        write_at(out, shadow_x, row, shadow_width, "", muted(), modal_shadow_bg(), false)?;
    }
    Ok(())
}

pub(super) fn modal_title(title: &str, width: u16) -> String {
    let title = format!(" {title}");
    let back = " ← Esc back ";
    let title_width = title.chars().count();
    let back_width = back.chars().count();
    if title_width + back_width >= width as usize {
        return clip_plain(&format!("{title} {back}"), width as usize);
    }
    format!("{}{}{}", title, " ".repeat(width as usize - title_width - back_width), back)
}

pub(super) fn draw_modal(title: &str, rows: &[String]) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(2).saturating_div(3).clamp(42, width.saturating_sub(4));
    let modal_height = (rows.len() as u16 + 4).clamp(7, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
    for idx in 0..modal_height.saturating_sub(4) {
        let row = rows.get(idx as usize).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        &"=".repeat(modal_width as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    out.flush()?;
    Ok(())
}

pub(super) fn draw_input_modal(title: &str, rows: &[String], editor: &InputEditor) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(3).saturating_div(4).clamp(48, width.saturating_sub(4));
    let modal_height = (rows.len() as u16 + 6).clamp(8, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let inner_width = modal_width.saturating_sub(4).max(8) as usize;
    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
    for idx in 0..modal_height.saturating_sub(5) {
        let row = rows.get(idx as usize).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    let input_y = y + modal_height - 3;
    let prompt = " > ";
    let available = inner_width.saturating_sub(prompt.len()).max(8);
    let (visible, cursor) = editor.visible(available);
    write_at(&mut out, x + 1, input_y, modal_width.saturating_sub(2), "", text(), input_bg(), false)?;
    queue!(
        out,
        MoveTo(x + 2, input_y),
        SetForegroundColor(cyan()),
        SetBackgroundColor(input_bg()),
        Print(prompt),
        SetForegroundColor(text()),
        Print(clip_plain(&visible, available)),
        MoveTo(x + 2 + prompt.len() as u16 + cursor as u16, input_y),
        Show
    )?;
    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        &"=".repeat(modal_width as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    out.flush()?;
    Ok(())
}

fn draw_choice_picker_modal(
    title: &str,
    rows: &[String],
    filter: &str,
    choices: &[(usize, String)],
    selected: usize,
) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(4).saturating_div(5).clamp(52, width.saturating_sub(4));
    let modal_height = height.saturating_mul(2).saturating_div(3).clamp(11, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let info_rows = rows
        .len()
        .min(modal_height.saturating_sub(8) as usize);
    let list_start = y + 4 + info_rows as u16;
    let list_rows = modal_height
        .saturating_sub(info_rows as u16)
        .saturating_sub(5) as usize;
    let inner = modal_width.saturating_sub(4).max(10) as usize;
    let mut out = stdout();

    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title(title, modal_width), black(), modal_border(), true)?;
    for idx in 0..info_rows {
        let row = rows.get(idx).map(String::as_str).unwrap_or("");
        write_at(&mut out, x, y + idx as u16 + 2, modal_width, row, text(), modal_bg(), false)?;
    }
    let filter_y = y + 2 + info_rows as u16;
    write_at(
        &mut out,
        x,
        filter_y,
        modal_width,
        &format!(" Filter : {}", if filter.is_empty() { "(type to filter)" } else { filter }),
        cyan(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        filter_y + 1,
        modal_width,
        " Enter selects. ← Esc back. Up/Down/Page moves.",
        yellow(),
        modal_bg(),
        false,
    )?;

    let start = if selected >= list_rows {
        selected + 1 - list_rows
    } else {
        0
    };
    for row_index in 0..list_rows {
        let choice_index = start + row_index;
        let row_y = list_start + row_index as u16;
        let Some((_, choice)) = choices.get(choice_index) else {
            write_at(&mut out, x + 1, row_y, modal_width.saturating_sub(2), "", muted(), modal_list_bg(), false)?;
            continue;
        };
        let active = choice_index == selected;
        let marker = if active { ">" } else { " " };
        write_at(
            &mut out,
            x + 1,
            row_y,
            modal_width.saturating_sub(2),
            &format!("{marker} {}", clip_plain(choice, inner.saturating_sub(2))),
            if active { black() } else { text() },
            if active { modal_selection_bg() } else { modal_list_bg() },
            active,
        )?;
    }

    let footer = if choices.is_empty() {
        " No matches".to_string()
    } else {
        format!(" {} match(es)", choices.len())
    };
    write_at(&mut out, x, y + modal_height - 1, modal_width, &footer, modal_border(), modal_bg(), true)?;
    out.flush()?;
    Ok(())
}

fn filtered_choices(choices: &[String], filter: &str) -> Vec<(usize, String)> {
    if filter.trim().is_empty() {
        return choices
            .iter()
            .enumerate()
            .take(200)
            .map(|(index, choice)| (index, choice.clone()))
            .collect();
    }
    let needle = filter.to_lowercase();
    choices
        .iter()
        .enumerate()
        .filter(|(_, choice)| choice.to_lowercase().contains(&needle))
        .take(200)
        .map(|(index, choice)| (index, choice.clone()))
        .collect()
}
