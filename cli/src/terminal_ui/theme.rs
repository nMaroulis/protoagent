use anyhow::Result;
use crossterm::{
    cursor::MoveTo,
    queue,
    style::{Attribute, Color, Print, SetAttribute, SetBackgroundColor, SetForegroundColor},
    terminal,
};
use std::io::Stdout;

use super::state::Role;

pub(super) fn size() -> (u16, u16) {
    terminal::size().unwrap_or((100, 30))
}

pub(super) fn write_line(
    out: &mut Stdout,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    write_at(out, 0, y, width, text_value, fg, background, bold)
}

pub(super) fn write_at(
    out: &mut Stdout,
    x: u16,
    y: u16,
    width: u16,
    text_value: &str,
    fg: Color,
    background: Color,
    bold: bool,
) -> Result<()> {
    let mut value = clip_plain(text_value, width as usize);
    let len = value.chars().count();
    if len < width as usize {
        value.push_str(&" ".repeat(width as usize - len));
    }
    queue!(
        out,
        MoveTo(x, y),
        SetForegroundColor(fg),
        SetBackgroundColor(background),
        SetAttribute(if bold { Attribute::Bold } else { Attribute::Reset }),
        Print(value),
        SetAttribute(Attribute::Reset)
    )?;
    Ok(())
}

pub(super) fn clip_plain(text: &str, width: usize) -> String {
    text.chars().take(width).collect()
}

pub(super) fn role_color(role: Role) -> Color {
    match role {
        Role::User => magenta(),
        Role::Assistant => cyan(),
        Role::Command => muted(),
        Role::System => muted(),
        Role::Error => red(),
    }
}

pub(super) fn bg() -> Color {
    Color::Rgb { r: 7, g: 9, b: 16 }
}

pub(super) fn panel_bg() -> Color {
    Color::Rgb { r: 14, g: 16, b: 24 }
}

pub(super) fn surface_bg() -> Color {
    Color::Rgb { r: 32, g: 38, b: 55 }
}

pub(super) fn input_bg() -> Color {
    Color::Rgb { r: 18, g: 21, b: 30 }
}

pub(super) fn modal_shadow_bg() -> Color {
    Color::Rgb { r: 0, g: 0, b: 0 }
}

pub(super) fn modal_bg() -> Color {
    Color::Rgb { r: 18, g: 23, b: 35 }
}

pub(super) fn modal_list_bg() -> Color {
    Color::Rgb { r: 10, g: 14, b: 23 }
}

pub(super) fn modal_selection_bg() -> Color {
    Color::Rgb { r: 111, g: 229, b: 235 }
}

pub(super) fn modal_border() -> Color {
    Color::Rgb { r: 246, g: 207, b: 93 }
}

pub(super) fn text() -> Color {
    Color::Rgb { r: 238, g: 243, b: 248 }
}

pub(super) fn muted() -> Color {
    Color::Rgb { r: 154, g: 167, b: 184 }
}

pub(super) fn cyan() -> Color {
    Color::Rgb { r: 88, g: 220, b: 233 }
}

pub(super) fn magenta() -> Color {
    Color::Rgb { r: 224, g: 86, b: 216 }
}

pub(super) fn yellow() -> Color {
    Color::Rgb { r: 243, g: 198, b: 91 }
}

pub(super) fn green() -> Color {
    Color::Rgb { r: 116, g: 223, b: 159 }
}

pub(super) fn red() -> Color {
    Color::Rgb { r: 255, g: 122, b: 138 }
}

pub(super) fn black() -> Color {
    Color::Black
}
