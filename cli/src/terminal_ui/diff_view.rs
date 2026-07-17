use anyhow::Result;
use crossterm::event::{read, Event, KeyCode, KeyModifiers};
use crossterm::style::Color;
use std::io::{stdout, Write};

use crate::diff::{
    compact_diff_stats, format_file_heading, format_guttered_line, parse_diff, DiffLineKind,
    DiffReview,
};
use crate::progress::RuntimeApproval;

use super::modal::{draw_modal_backdrop, draw_modal_shadow, draw_modal_sides, modal_title};
use super::state::TerminalApp;
use super::theme::{
    black, clip_plain, cyan, green, modal_bg, modal_border, modal_list_bg, muted, red, size,
    surface_bg, text, write_at, yellow,
};
use super::TerminalSurface;

struct DiffRenderLine {
    text: String,
    fg: Color,
    bg: Color,
    bold: bool,
}

pub(super) fn diff_review_summary(diff: &str) -> String {
    let review = parse_diff(diff);
    if review.files.is_empty() {
        return "No structured diff could be parsed. Use /diff raw to inspect the raw payload."
            .to_string();
    }

    let mut rows = vec![format!(
        "{} file(s), +{} -{}, {} hunk(s)",
        review.files.len(),
        review.additions,
        review.removals,
        review.hunks
    )];
    for file in review.files.iter().take(8) {
        rows.push(format_file_heading(file));
    }
    if review.files.len() > 8 {
        rows.push(format!("...{} more file(s)", review.files.len() - 8));
    }
    rows.push("Opened diff reviewer. Use /diff raw for the raw unified diff.".to_string());
    rows.join("\n")
}

pub(super) fn show_diff_modal(
    terminal: &mut TerminalSurface,
    app: &TerminalApp,
    title: &str,
    diff: &str,
) -> Result<()> {
    let review = parse_diff(diff);
    let mut scroll = 0usize;
    loop {
        terminal.render(app, None)?;
        let max_scroll = draw_diff_modal(title, &review, scroll)?;
        scroll = scroll.min(max_scroll);
        let Event::Key(key) = read()? else {
            continue;
        };
        match key.code {
            KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') | KeyCode::Char('Q') => {
                return Ok(())
            }
            KeyCode::Up => scroll = scroll.saturating_sub(1),
            KeyCode::Down => scroll = (scroll + 1).min(max_scroll),
            KeyCode::PageUp => scroll = scroll.saturating_sub(12),
            KeyCode::PageDown => scroll = (scroll + 12).min(max_scroll),
            KeyCode::Home if key.modifiers.contains(KeyModifiers::CONTROL) => scroll = 0,
            KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => scroll = max_scroll,
            _ => {}
        }
    }
}

pub(super) fn draw_approval_modal(approval: &RuntimeApproval) -> Result<()> {
    let (width, height) = size();
    let modal_width = width
        .saturating_mul(4)
        .saturating_div(5)
        .clamp(54, width.saturating_sub(4));
    let modal_height = 14u16.min(height.saturating_sub(4)).max(11);
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;

    write_at(
        &mut out,
        x,
        y,
        modal_width,
        &"=".repeat(modal_width as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 1,
        modal_width,
        &modal_title("Policy Approval", modal_width),
        black(),
        modal_border(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 2,
        modal_width,
        " Protolink paused this action before execution.",
        text(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 3,
        modal_width,
        &format!(" Action    : {}", approval.action_name),
        muted(),
        modal_bg(),
        false,
    )?;
    write_at(
        &mut out,
        x,
        y + 4,
        modal_width,
        &format!(" Capability: {}", approval.capabilities()),
        cyan(),
        modal_bg(),
        true,
    )?;
    let target = if approval.target.is_empty() {
        "not reported"
    } else {
        approval.target.as_str()
    };
    write_at(
        &mut out,
        x,
        y + 5,
        modal_width,
        &format!(" Target    : {target}"),
        text(),
        modal_bg(),
        false,
    )?;
    let diff_summary = if approval.diff.trim().is_empty() {
        "no diff payload attached".to_string()
    } else {
        compact_diff_stats(&parse_diff(&approval.diff))
    };
    write_at(
        &mut out,
        x,
        y + 6,
        modal_width,
        &format!(" Diff      : {diff_summary}"),
        yellow(),
        modal_bg(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 7,
        modal_width,
        &format!(" Request   : {} / {}", approval.run_id, approval.request_id),
        muted(),
        modal_bg(),
        false,
    )?;

    write_at(
        &mut out,
        x,
        y + 8,
        modal_width,
        &format!(
            " Intent    : {}",
            clip_plain(
                &approval.description,
                modal_width.saturating_sub(14) as usize
            )
        ),
        muted(),
        modal_bg(),
        false,
    )?;

    let button_y = y + modal_height.saturating_sub(3);
    let mut button_x = x + 2;
    draw_button(
        &mut out,
        button_x,
        button_y,
        14,
        "[Y] APPLY",
        black(),
        green(),
    )?;
    button_x += 16;
    if approval.diff.trim().is_empty() {
        draw_button(
            &mut out,
            button_x,
            button_y,
            18,
            "[V] NO DIFF",
            muted(),
            surface_bg(),
        )?;
    } else {
        draw_button(
            &mut out,
            button_x,
            button_y,
            18,
            "[V] REVIEW DIFF",
            black(),
            yellow(),
        )?;
    }
    button_x += 20;
    draw_button(&mut out, button_x, button_y, 13, "[N] DENY", black(), red())?;

    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        " Esc back denies without applying",
        modal_border(),
        modal_bg(),
        true,
    )?;
    draw_modal_sides(&mut out, x, y, modal_width, modal_height)?;
    out.flush()?;
    Ok(())
}

fn draw_diff_modal(title: &str, review: &DiffReview, scroll: usize) -> Result<usize> {
    let (width, height) = size();
    let modal_width = width
        .saturating_mul(9)
        .saturating_div(10)
        .clamp(60, width.saturating_sub(2));
    let modal_height = height
        .saturating_mul(4)
        .saturating_div(5)
        .clamp(14, height.saturating_sub(2));
    let x = width.saturating_sub(modal_width) / 2;
    let y = height.saturating_sub(modal_height) / 2;
    let body_top = y + 5;
    let body_rows = modal_height.saturating_sub(7) as usize;
    let body_width = modal_width.saturating_sub(4).max(20) as usize;
    let lines = render_diff_lines(review);
    let max_scroll = lines.len().saturating_sub(body_rows);
    let start = scroll.min(max_scroll);

    let mut out = stdout();
    draw_modal_backdrop(&mut out, width, height)?;
    draw_modal_shadow(&mut out, x, y, modal_width, modal_height)?;
    write_at(
        &mut out,
        x,
        y,
        modal_width,
        &"=".repeat(modal_width as usize),
        modal_border(),
        modal_bg(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 1,
        modal_width,
        &modal_title(title, modal_width),
        black(),
        modal_border(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 2,
        modal_width,
        &format!(" {}", compact_diff_stats(review)),
        yellow(),
        modal_bg(),
        true,
    )?;
    write_at(
        &mut out,
        x,
        y + 3,
        modal_width,
        " Up/Down/Page scroll. Enter or q closes. Esc back. /diff raw prints the unified diff.",
        muted(),
        modal_bg(),
        false,
    )?;
    let number_width = review.line_number_width();
    let column_header = format!(
        "tag  {:>width$} {:>width$} | change",
        "old",
        "new",
        width = number_width
    );
    write_at(
        &mut out,
        x + 1,
        y + 4,
        modal_width.saturating_sub(2),
        &column_header,
        cyan(),
        modal_list_bg(),
        true,
    )?;

    for row_index in 0..body_rows {
        let row_y = body_top + row_index as u16;
        let Some(line) = lines.get(start + row_index) else {
            write_at(
                &mut out,
                x + 1,
                row_y,
                modal_width.saturating_sub(2),
                "",
                muted(),
                modal_list_bg(),
                false,
            )?;
            continue;
        };
        write_at(
            &mut out,
            x + 1,
            row_y,
            modal_width.saturating_sub(2),
            &clip_plain(&line.text, body_width),
            line.fg,
            line.bg,
            line.bold,
        )?;
    }

    let footer = if lines.is_empty() {
        " No diff lines".to_string()
    } else {
        format!(
            " line {}-{} of {}",
            start + 1,
            (start + body_rows).min(lines.len()),
            lines.len()
        )
    };
    write_at(
        &mut out,
        x,
        y + modal_height - 1,
        modal_width,
        &footer,
        modal_border(),
        modal_bg(),
        true,
    )?;
    draw_modal_sides(&mut out, x, y, modal_width, modal_height)?;
    out.flush()?;
    Ok(max_scroll)
}

fn render_diff_lines(review: &DiffReview) -> Vec<DiffRenderLine> {
    let mut rows = Vec::new();
    let number_width = review.line_number_width();
    for file in &review.files {
        rows.push(DiffRenderLine {
            text: format!("FILE  {}", format_file_heading(file)),
            fg: diff_file_fg(),
            bg: diff_file_bg(),
            bold: true,
        });
        for line in &file.lines {
            let (fg, bg, bold) = match line.kind {
                DiffLineKind::Meta => (diff_meta_fg(), diff_meta_bg(), false),
                DiffLineKind::Hunk => (diff_hunk_fg(), diff_hunk_bg(), true),
                DiffLineKind::Add => (diff_add_fg(), diff_add_bg(), true),
                DiffLineKind::Remove => (diff_remove_fg(), diff_remove_bg(), true),
                DiffLineKind::Context => (text(), modal_list_bg(), false),
            };
            rows.push(DiffRenderLine {
                text: format!(
                    "{:<4} {}",
                    diff_kind_label(line.kind),
                    format_guttered_line(line, number_width)
                ),
                fg,
                bg,
                bold,
            });
        }
        rows.push(DiffRenderLine {
            text: String::new(),
            fg: muted(),
            bg: modal_list_bg(),
            bold: false,
        });
    }

    if rows.is_empty() {
        rows.push(DiffRenderLine {
            text: "No structured diff lines found.".to_string(),
            fg: muted(),
            bg: modal_list_bg(),
            bold: false,
        });
    }
    rows
}

fn diff_kind_label(kind: DiffLineKind) -> &'static str {
    match kind {
        DiffLineKind::Add => "ADD+",
        DiffLineKind::Remove => "DEL-",
        DiffLineKind::Hunk => "HUNK",
        DiffLineKind::Meta => "META",
        DiffLineKind::Context => "",
    }
}

fn draw_button(
    out: &mut std::io::Stdout,
    x: u16,
    y: u16,
    width: u16,
    label: &str,
    fg: Color,
    bg: Color,
) -> Result<()> {
    write_at(out, x, y, width, label, fg, bg, true)
}

fn diff_file_fg() -> Color {
    Color::Rgb {
        r: 164,
        g: 239,
        b: 246,
    }
}

fn diff_file_bg() -> Color {
    Color::Rgb {
        r: 10,
        g: 34,
        b: 48,
    }
}

fn diff_add_fg() -> Color {
    Color::Rgb {
        r: 185,
        g: 244,
        b: 205,
    }
}

fn diff_add_bg() -> Color {
    Color::Rgb {
        r: 12,
        g: 43,
        b: 32,
    }
}

fn diff_remove_fg() -> Color {
    Color::Rgb {
        r: 255,
        g: 185,
        b: 195,
    }
}

fn diff_remove_bg() -> Color {
    Color::Rgb {
        r: 52,
        g: 22,
        b: 33,
    }
}

fn diff_hunk_fg() -> Color {
    Color::Rgb {
        r: 255,
        g: 228,
        b: 156,
    }
}

fn diff_hunk_bg() -> Color {
    Color::Rgb {
        r: 45,
        g: 38,
        b: 22,
    }
}

fn diff_meta_fg() -> Color {
    Color::Rgb {
        r: 135,
        g: 148,
        b: 166,
    }
}

fn diff_meta_bg() -> Color {
    Color::Rgb {
        r: 13,
        g: 18,
        b: 29,
    }
}
