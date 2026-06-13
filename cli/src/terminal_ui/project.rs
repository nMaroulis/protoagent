use anyhow::Result;
use crossterm::event::{read, Event, KeyCode, KeyModifiers};
use std::collections::VecDeque;
use std::fs;
use std::io::{stdout, Write};
use std::path::Path;

use super::input::InputEditor;
use super::modal::{draw_input_modal, draw_modal, draw_modal_backdrop, draw_modal_shadow, draw_modal_sides, modal_title};
use super::state::{PanelView, Role, TerminalApp};
use super::theme::{
    black, clip_plain, cyan, modal_bg, modal_border, modal_list_bg, modal_selection_bg, muted, size, text,
    write_at, yellow,
};
use super::TerminalSurface;

pub(super) fn handle_project_command(
    app: &mut TerminalApp,
    terminal: &mut TerminalSurface,
    command: &str,
    arg: &str,
) -> Result<()> {
    if matches!(arg, "clear" | "unset") {
        crate::clear_active_project()?;
        app.panel = PanelView::Project;
        app.refresh(None);
        app.push(Role::Command, command, "Project cleared. Choose a folder before running tasks.");
        return Ok(());
    }

    let selected = if arg.is_empty() || matches!(arg, "choose" | "set") {
        match project_prompt(terminal, app)? {
            Some(path) => path,
            None => {
                app.panel = PanelView::Project;
                app.refresh(None);
                app.push(Role::Command, command, "Project selection cancelled.");
                return Ok(());
            }
        }
    } else if let Some(rest) = arg.strip_prefix("set ") {
        rest.trim().to_string()
    } else if let Some(rest) = arg.strip_prefix("open ") {
        rest.trim().to_string()
    } else {
        arg.to_string()
    };

    match crate::set_active_project(&selected) {
        Ok(path) => {
            app.panel = PanelView::Project;
            app.refresh(None);
            app.push(
                Role::Command,
                command,
                &format!(
                    "Opened project: {}\nFuture starts reopen this folder. Type @ in the prompt to tag files.",
                    path.to_string_lossy()
                ),
            );
        }
        Err(err) => {
            app.panel = PanelView::Project;
            app.refresh(None);
            app.push(Role::Error, command, &format!("Could not open project: {err}"));
        }
    }
    Ok(())
}

pub(super) fn pick_project_file(terminal: &mut TerminalSurface, app: &TerminalApp) -> Result<Option<String>> {
    let Some(root) = crate::active_project_dir() else {
        return Ok(None);
    };
    let files = collect_project_files(&root, 800)?;
    if files.is_empty() {
        draw_modal(
            "No Files Found",
            &[
                format!("Project: {}", root.to_string_lossy()),
                "No taggable text files were found.".to_string(),
                "Press any key to continue.".to_string(),
            ],
        )?;
        let _ = read();
        return Ok(None);
    }

    let mut filter = String::new();
    let mut selected = 0usize;
    loop {
        let matches = filtered_files(&files, &filter);
        selected = selected.min(matches.len().saturating_sub(1));
        terminal.render(app, None)?;
        draw_file_picker_modal(&root, &filter, &matches, selected)?;
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Enter => {
                if let Some(path) = matches.get(selected) {
                    return Ok(Some((*path).clone()));
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
            KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                filter.push(ch);
                selected = 0;
            }
            _ => {}
        }
    }
}

pub(super) fn format_file_tag(path: &str) -> String {
    if path.chars().any(char::is_whitespace) {
        format!("@\"{}\" ", path.replace('"', "\\\""))
    } else {
        format!("@{} ", path)
    }
}

fn project_prompt(terminal: &mut TerminalSurface, app: &TerminalApp) -> Result<Option<String>> {
    let initial = crate::active_project_dir()
        .unwrap_or_else(crate::default_launch_workspace)
        .to_string_lossy()
        .to_string();
    let history = VecDeque::new();
    let mut editor = InputEditor::with_initial(&history, &initial);
    loop {
        terminal.render(app, None)?;
        draw_input_modal(
            "Choose Project Folder",
            &[
                "Enter a folder for this session and future starts.".to_string(),
                "Use . for the launch directory, ~ for home, or an absolute path.".to_string(),
                "Enter confirms. Esc cancels.".to_string(),
            ],
            &editor,
        )?;
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

fn filtered_files(files: &[String], filter: &str) -> Vec<String> {
    if filter.trim().is_empty() {
        return files.iter().take(200).cloned().collect();
    }
    let needle = filter.to_lowercase();
    files
        .iter()
        .filter(|path| path.to_lowercase().contains(&needle))
        .take(200)
        .cloned()
        .collect()
}

fn collect_project_files(root: &Path, limit: usize) -> Result<Vec<String>> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let mut entries = fs::read_dir(&dir)?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .collect::<Vec<_>>();
        entries.sort_by(|a, b| {
            let a_dir = a.is_dir();
            let b_dir = b.is_dir();
            b_dir.cmp(&a_dir).then_with(|| a.file_name().cmp(&b.file_name()))
        });

        for path in entries {
            let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
                continue;
            };
            if name.starts_with('.') || ignored_picker_name(name) {
                continue;
            }
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            if !path.is_file() || looks_binary_for_picker(&path) {
                continue;
            }
            if let Ok(relative) = path.strip_prefix(root) {
                out.push(relative.to_string_lossy().replace('\\', "/"));
                if out.len() >= limit {
                    out.sort();
                    return Ok(out);
                }
            }
        }
    }
    out.sort();
    Ok(out)
}

fn ignored_picker_name(name: &str) -> bool {
    matches!(
        name,
        ".git" | ".hg" | ".svn" | ".venv" | "__pycache__" | "node_modules" | "target" | "dist" | "build"
    )
}

fn looks_binary_for_picker(path: &Path) -> bool {
    let Some(ext) = path.extension().and_then(|ext| ext.to_str()).map(str::to_lowercase) else {
        return false;
    };
    matches!(
        ext.as_str(),
        "png"
            | "jpg"
            | "jpeg"
            | "gif"
            | "webp"
            | "pdf"
            | "zip"
            | "tar"
            | "gz"
            | "bin"
            | "so"
            | "dylib"
            | "class"
            | "pyc"
    )
}

fn draw_file_picker_modal(root: &Path, filter: &str, files: &[String], selected: usize) -> Result<()> {
    let (width, height) = size();
    let modal_width = width.saturating_mul(4).saturating_div(5).clamp(50, width.saturating_sub(4));
    let modal_height = height.saturating_mul(2).saturating_div(3).clamp(10, height.saturating_sub(4));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let list_rows = modal_height.saturating_sub(7) as usize;
    let inner = modal_width.saturating_sub(4).max(10) as usize;
    let mut out = stdout();

    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(&mut out, x, y, modal_width, &"=".repeat(modal_width as usize), modal_border(), modal_bg(), true)?;
    write_at(&mut out, x, y + 1, modal_width, &modal_title("Tag File With @", modal_width), black(), modal_border(), true)?;
    write_at(
        &mut out,
        x,
        y + 2,
        modal_width,
        &format!(" Project: {}", root.to_string_lossy()),
        muted(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 3,
        modal_width,
        &format!(" Filter : {}", if filter.is_empty() { "(type to filter)" } else { filter }),
        cyan(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 4,
        modal_width,
        " Enter inserts @file. Esc back. Up/Down moves.",
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
        let file_index = start + row_index;
        let row_y = y + 5 + row_index as u16;
        let Some(path) = files.get(file_index) else {
            write_at(&mut out, x + 1, row_y, modal_width.saturating_sub(2), "", muted(), modal_list_bg(), false)?;
            continue;
        };
        let active = file_index == selected;
        let marker = if active { ">" } else { " " };
        write_at(
            &mut out,
            x + 1,
            row_y,
            modal_width.saturating_sub(2),
            &format!("{marker} {}", clip_plain(path, inner.saturating_sub(2))),
            if active { black() } else { text() },
            if active { modal_selection_bg() } else { modal_list_bg() },
            active,
        )?;
    }
    let footer = if files.is_empty() {
        " No matching files".to_string()
    } else {
        format!(" {} match(es)", files.len())
    };
    write_at(&mut out, x, y + modal_height - 1, modal_width, &footer, modal_border(), modal_bg(), true)?;
    draw_modal_sides(&mut out, x, y, modal_width, modal_height)?;
    out.flush()?;
    Ok(())
}
