//! Directory navigation and a read-only source viewer; no model or shell process.

use anyhow::Result;
use crossterm::event::{read, Event, KeyCode, KeyModifiers, MouseEventKind};
use std::path::Path;

use super::modal::{draw_modal_backdrop, draw_modal_shadow, draw_modal_sides, modal_title};
use super::state::TerminalApp;
use super::theme::{
    black, clip_plain, cyan, modal_bg, modal_border, modal_list_bg, modal_selection_bg, muted,
    size, text, write_at,
};
use super::TerminalSurface;
use crate::project_files::{self, Directory, TextFile};

pub(super) fn open(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    argument: &str,
) -> Result<()> {
    let root = std::path::PathBuf::from(crate::require_project_dir_string()?).canonicalize()?;
    let argument = project_files::path_argument(argument)?;
    let target = project_files::resolve(&root, if argument.is_empty() { "." } else { &argument })?;
    if target.is_dir() {
        browse(
            terminal,
            app,
            &root,
            project_files::list_directory(&root, &target.to_string_lossy())?,
        )
    } else {
        view(
            terminal,
            app,
            project_files::read_text(&root, &target.to_string_lossy())?,
        )
    }
}

fn choices(directory: &Directory, root: &Path, filter: &str) -> Vec<(String, std::path::PathBuf)> {
    let mut choices = Vec::new();
    if directory.path != root && filter.is_empty() {
        choices.push((
            "DIR   ..".into(),
            directory.path.parent().unwrap().to_path_buf(),
        ));
    }
    let needle = filter.to_lowercase();
    choices.extend(
        directory
            .entries
            .iter()
            .filter(|entry| entry.name.to_lowercase().contains(&needle))
            .map(|entry| (entry.label(), entry.path.clone())),
    );
    choices
}

fn browse(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    root: &Path,
    mut directory: Directory,
) -> Result<()> {
    let mut filter = String::new();
    let mut selected = 0usize;
    let mut notice = String::new();
    let mut painted = None;
    loop {
        let choices = choices(&directory, root, &filter);
        selected = selected.min(choices.len().saturating_sub(1));
        let viewport = viewport();
        let start = selected.saturating_add(1).saturating_sub(viewport.rows);
        let rows = choices
            .iter()
            .enumerate()
            .skip(start)
            .take(viewport.rows)
            .map(|(index, (label, _))| (label.clone(), index == selected))
            .collect::<Vec<_>>();
        terminal.prepare_modal(app, &mut painted)?;
        let footer = if !notice.is_empty() {
            notice.clone()
        } else if directory.truncated {
            format!(
                "Listing capped at {} entries; known paths can be opened directly.",
                project_files::MAX_ENTRIES
            )
        } else {
            format!(
                "{} entries | hidden files included | viewing never sends source to a model",
                directory.entries.len()
            )
        };
        paint(
            "Project Files",
            &project_files::display_label(&directory.path.to_string_lossy()),
            "Esc close | Enter open | Backspace up/erase | arrows/page keys",
            &format!("Filter: {filter}"),
            &rows,
            &footer,
        )?;
        let event = read()?;
        match event {
            Event::Key(key)
                if key.code == KeyCode::Esc
                    || (key.code == KeyCode::Char('c')
                        && key.modifiers.contains(KeyModifiers::CONTROL)) =>
            {
                return Ok(())
            }
            Event::Key(key) => {
                notice.clear();
                match key.code {
                    KeyCode::Up => selected = selected.saturating_sub(1),
                    KeyCode::Down => {
                        selected = selected
                            .saturating_add(1)
                            .min(choices.len().saturating_sub(1))
                    }
                    KeyCode::PageUp => selected = selected.saturating_sub(viewport.rows.max(1)),
                    KeyCode::PageDown => {
                        selected = selected
                            .saturating_add(viewport.rows.max(1))
                            .min(choices.len().saturating_sub(1))
                    }
                    KeyCode::Home => selected = 0,
                    KeyCode::End => selected = choices.len().saturating_sub(1),
                    KeyCode::Backspace if !filter.is_empty() => {
                        filter.pop();
                        selected = 0;
                    }
                    KeyCode::Char(ch)
                        if !key
                            .modifiers
                            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
                    {
                        if filter.len() < 256 {
                            filter.push(ch);
                            selected = 0;
                        }
                    }
                    KeyCode::Enter | KeyCode::Backspace => {
                        let path = if key.code == KeyCode::Backspace {
                            if directory.path == root {
                                continue;
                            }
                            directory.path.parent().unwrap().to_path_buf()
                        } else if let Some((_, path)) = choices.get(selected) {
                            path.clone()
                        } else {
                            continue;
                        };
                        match project_files::resolve(root, &path.to_string_lossy()) {
                            Ok(path) if path.is_dir() => {
                                match project_files::list_directory(root, &path.to_string_lossy()) {
                                    Ok(next) => {
                                        directory = next;
                                        filter.clear();
                                        selected = 0;
                                    }
                                    Err(error) => notice = error.to_string(),
                                }
                            }
                            Ok(path) => {
                                match project_files::read_text(root, &path.to_string_lossy()) {
                                    Ok(file) => {
                                        view(terminal, app, file)?;
                                        painted = None;
                                    }
                                    Err(error) => notice = error.to_string(),
                                }
                            }
                            Err(error) => notice = error.to_string(),
                        }
                    }
                    _ => {}
                }
            }
            Event::Mouse(mouse) => match mouse.kind {
                MouseEventKind::ScrollDown => {
                    selected = selected
                        .saturating_add(3)
                        .min(choices.len().saturating_sub(1))
                }
                MouseEventKind::ScrollUp => selected = selected.saturating_sub(3),
                _ => {}
            },
            _ => {}
        }
    }
}

#[derive(Default)]
struct FileScroll {
    top: usize,
    column: usize,
}
impl FileScroll {
    fn move_key(&mut self, key: KeyCode, lines: usize, columns: usize, viewport: Viewport) {
        let maximum = lines.saturating_sub(viewport.rows.max(1));
        self.top = self.top.min(maximum);
        match key {
            KeyCode::Up => self.top = self.top.saturating_sub(1),
            KeyCode::Down => self.top = self.top.saturating_add(1).min(maximum),
            KeyCode::PageUp => self.top = self.top.saturating_sub(viewport.rows.max(1)),
            KeyCode::PageDown => {
                self.top = self.top.saturating_add(viewport.rows.max(1)).min(maximum)
            }
            KeyCode::Home => {
                self.top = 0;
                self.column = 0;
            }
            KeyCode::End => self.top = maximum,
            KeyCode::Left => self.column = self.column.saturating_sub(8),
            KeyCode::Right => {
                self.column = self.column.saturating_add(8).min(columns.saturating_sub(1))
            }
            _ => {}
        }
    }
}

fn view(terminal: &mut TerminalSurface, app: &TerminalApp, file: TextFile) -> Result<()> {
    // Read once; navigation only slices cached source, even on resize.
    let lines = file
        .content
        .lines()
        .map(|line| line.replace('\t', "    "))
        .collect::<Vec<_>>();
    let columns = lines
        .iter()
        .map(|line| line.chars().count())
        .max()
        .unwrap_or(0);
    let mut scroll = FileScroll::default();
    let mut painted = None;
    loop {
        let viewport = viewport();
        scroll.top = scroll
            .top
            .min(lines.len().saturating_sub(viewport.rows.max(1)));
        let rows = lines
            .iter()
            .enumerate()
            .skip(scroll.top)
            .take(viewport.rows)
            .map(|(index, line)| {
                let part = line
                    .chars()
                    .skip(scroll.column)
                    .take(viewport.width)
                    .collect::<String>();
                (format!("{:>5}  {part}", index + 1), false)
            })
            .collect::<Vec<_>>();
        terminal.prepare_modal(app, &mut painted)?;
        let footer = format!(
            "{} lines | column {} | read only{}",
            lines.len(),
            scroll.column + 1,
            if file.truncated {
                " | preview capped at 1 MiB"
            } else {
                ""
            }
        );
        paint(
            "File Viewer",
            &project_files::display_label(&file.path.to_string_lossy()),
            "Esc/q back | Up/Down | PgUp/PgDn | Home/End | Left/Right pan",
            &format!(
                "Showing lines {}-{}",
                scroll.top + usize::from(!lines.is_empty()),
                (scroll.top + viewport.rows).min(lines.len())
            ),
            &rows,
            &footer,
        )?;
        match read()? {
            Event::Key(key)
                if matches!(key.code, KeyCode::Esc | KeyCode::Char('q'))
                    || (key.code == KeyCode::Char('c')
                        && key.modifiers.contains(KeyModifiers::CONTROL)) =>
            {
                return Ok(())
            }
            Event::Key(key) => scroll.move_key(key.code, lines.len(), columns, viewport),
            Event::Mouse(mouse) => match mouse.kind {
                MouseEventKind::ScrollUp => scroll.top = scroll.top.saturating_sub(3),
                MouseEventKind::ScrollDown => scroll.top = scroll.top.saturating_add(3),
                _ => {}
            },
            _ => {}
        }
    }
}

#[derive(Clone, Copy)]
struct Viewport {
    rows: usize,
    width: usize,
}
fn viewport() -> Viewport {
    let (width, height) = size();
    Viewport {
        rows: height.saturating_sub(12) as usize,
        width: width.saturating_mul(4).saturating_div(5).saturating_sub(4) as usize,
    }
}

fn paint(
    title: &str,
    path: &str,
    help: &str,
    detail: &str,
    rows: &[(String, bool)],
    footer: &str,
) -> Result<()> {
    let (width, height) = size();
    let mut out = Vec::new();
    draw_modal_backdrop(&mut out, width, height)?;
    if width < 30 || height < 14 {
        write_at(
            &mut out,
            0,
            0,
            width,
            "Resize for file browsing. Esc returns.",
            cyan(),
            modal_bg(),
            false,
        )?;
        return super::theme::present_overlay(&out);
    }
    let w = width.saturating_mul(4).saturating_div(5).min(width - 4);
    let h = height - 4;
    let x = (width - w) / 2;
    let y = 2;
    draw_modal_shadow(&mut out, x, y, w, h)?;
    write_at(
        &mut out,
        x,
        y,
        w,
        &"=".repeat(w as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 1,
        w,
        &modal_title(title, w),
        black(),
        modal_border(),
        true,
    )?;
    write_at(
        &mut out,
        x + 1,
        y + 2,
        w - 2,
        path,
        cyan(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x + 1,
        y + 3,
        w - 2,
        help,
        muted(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x + 1,
        y + 4,
        w - 2,
        detail,
        cyan(),
        modal_bg(),
        false,
    )?;
    for index in 0..h.saturating_sub(8) {
        let (value, active) = rows
            .get(index as usize)
            .map(|(value, active)| (value.as_str(), *active))
            .unwrap_or(("", false));
        write_at(
            &mut out,
            x + 1,
            y + 5 + index,
            w - 2,
            &clip_plain(value, w.saturating_sub(3) as usize),
            if active { black() } else { text() },
            if active {
                modal_selection_bg()
            } else {
                modal_list_bg()
            },
            active,
        )?;
    }
    write_at(
        &mut out,
        x + 1,
        y + h - 2,
        w - 2,
        footer,
        muted(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + h - 1,
        w,
        &"=".repeat(w as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    draw_modal_sides(&mut out, x, y, w, h)?;
    super::theme::present_overlay(&out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_filtered_filename_is_selected_instead_of_the_parent_directory() {
        let directory = Directory {
            path: "/project/docs".into(),
            truncated: false,
            entries: vec![project_files::Entry {
                path: "/project/docs/notes.txt".into(),
                name: "notes.txt".into(),
                kind: project_files::EntryKind::File,
            }],
        };
        assert_eq!(
            choices(&directory, Path::new("/project"), "NOTES")[0].1,
            Path::new("/project/docs/notes.txt")
        );
        assert_eq!(
            choices(&directory, Path::new("/project"), "")[0].1,
            Path::new("/project")
        );
        assert!(choices(&directory, Path::new("/project"), "missing").is_empty());
    }
    #[test]
    fn navigation_clamps_short_files_and_resize_and_pans_long_source() {
        let mut scroll = FileScroll::default();
        let page = Viewport {
            rows: 10,
            width: 80,
        };
        scroll.move_key(KeyCode::PageDown, 100, 200, page);
        assert_eq!(scroll.top, 10);
        scroll.move_key(KeyCode::End, 100, 200, page);
        assert_eq!(scroll.top, 90);
        scroll.move_key(KeyCode::Down, 100, 200, page);
        assert_eq!(scroll.top, 90);
        scroll.move_key(KeyCode::Right, 100, 200, page);
        assert_eq!(scroll.column, 8);
        scroll.move_key(KeyCode::Up, 3, 200, page);
        assert_eq!(scroll.top, 0);
        scroll.move_key(KeyCode::Home, 0, 0, page);
        assert_eq!(scroll.column, 0);
    }
}
