//! Styled applied-edit summaries from trusted runtime metadata, without ANSI in core data.

use serde_json::Value;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) enum Tone {
    #[default]
    Plain,
    Title,
    Added,
    Removed,
    Muted,
    Warning,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Segment {
    pub(crate) text: String,
    pub(crate) tone: Tone,
    pub(crate) bold: bool,
}

fn segment(text: impl Into<String>, tone: Tone, bold: bool) -> Segment {
    Segment {
        text: text.into(),
        tone,
        bold,
    }
}

pub(crate) fn answer_body<'a>(answer: &'a str, review: &Value) -> (&'a str, bool) {
    let Some(summary) = review["summary"]
        .as_str()
        .filter(|summary| !summary.is_empty())
    else {
        return (answer, false);
    };
    match answer.trim_end().strip_suffix(summary.trim_end()) {
        Some(body) => (body.trim_end(), true),
        None => (answer, false),
    }
}

pub(crate) fn rows(review: &Value, width: usize) -> Vec<Vec<Segment>> {
    let number = |key: &str| review[key].as_u64().unwrap_or(0);
    let count = number("file_count");
    let files = review["files"].as_array().cloned().unwrap_or_default();
    let mut lines = vec![vec![segment("Edits: ", Tone::Title, true)]];
    if files.is_empty() {
        lines[0].push(segment("no file edits recorded", Tone::Muted, false));
    } else {
        lines[0].push(segment(
            format!("{count} {}", if count == 1 { "file" } else { "files" }),
            Tone::Plain,
            true,
        ));
        if number("edit_count") > count {
            lines[0].push(segment(
                format!(" · {} edits", number("edit_count")),
                Tone::Muted,
                false,
            ));
        }
        lines[0].push(segment("  ", Tone::Plain, false));
        lines[0].push(segment(
            format!("+{}", number("additions")),
            Tone::Added,
            true,
        ));
        lines[0].push(segment(" / ", Tone::Muted, false));
        lines[0].push(segment(
            format!("-{}", number("removals")),
            Tone::Removed,
            true,
        ));
    }
    for file in files.iter().take(12) {
        let kind = if file["uncertain"].as_bool().unwrap_or(false) {
            "uncertain"
        } else {
            file["kind"].as_str().unwrap_or("modified")
        };
        let mut line = vec![
            segment("  ", Tone::Plain, false),
            segment(file["path"].as_str().unwrap_or(""), Tone::Plain, true),
            segment(
                format!("  {kind}"),
                if kind == "uncertain" {
                    Tone::Warning
                } else {
                    Tone::Muted
                },
                false,
            ),
        ];
        let added = file["additions"].as_u64().unwrap_or(0);
        let removed = file["removals"].as_u64().unwrap_or(0);
        if added + removed > 0 {
            line.extend([
                segment("  ", Tone::Plain, false),
                segment(format!("+{added}"), Tone::Added, false),
                segment(" / ", Tone::Muted, false),
                segment(format!("-{removed}"), Tone::Removed, false),
            ]);
        }
        lines.push(line);
        if let Some(note) = file["note"].as_str().filter(|note| !note.is_empty()) {
            lines.push(vec![segment(format!("    {note}"), Tone::Muted, false)]);
        }
    }
    if files.len() > 12 || review["truncated"].as_bool().unwrap_or(false) {
        lines.push(vec![segment(
            "  More files: /checkpoints",
            Tone::Muted,
            false,
        )]);
    }
    if review["has_diff"].as_bool().unwrap_or(false) {
        lines.push(vec![
            segment("  /diff", Tone::Title, false),
            segment(" · review changes", Tone::Muted, false),
        ]);
    }
    wrap(lines, width.max(1))
}

pub(crate) fn wrap(lines: Vec<Vec<Segment>>, width: usize) -> Vec<Vec<Segment>> {
    let mut output = Vec::new();
    for line in lines {
        let mut row: Vec<Segment> = Vec::new();
        let mut used = 0;
        for part in line {
            for glyph in part.text.graphemes(true) {
                let cells = glyph.width().min(width);
                if used + cells > width {
                    output.push(std::mem::take(&mut row));
                    used = 0;
                }
                let glyph = if glyph.width() > width { "�" } else { glyph };
                if let Some(last) = row
                    .last_mut()
                    .filter(|last| last.tone == part.tone && last.bold == part.bold)
                {
                    last.text.push_str(glyph);
                } else {
                    row.push(segment(glyph, part.tone, part.bold));
                }
                used += cells;
            }
        }
        output.push(row);
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn counters_keep_their_color_when_wrapped_and_filenames_are_literal() {
        let review = json!({"file_count":1,"edit_count":1,"additions":28,"removals":20,"has_diff":true,
            "files":[{"path":"**目录**/server.py","kind":"modified","additions":28,"removals":20}]});
        let lines = rows(&review, 19);
        assert!(lines
            .iter()
            .all(|row| row.iter().map(|part| part.text.width()).sum::<usize>() <= 19));
        let added: String = lines
            .iter()
            .flatten()
            .filter(|part| part.tone == Tone::Added)
            .map(|part| part.text.as_str())
            .collect();
        let removed: String = lines
            .iter()
            .flatten()
            .filter(|part| part.tone == Tone::Removed)
            .map(|part| part.text.as_str())
            .collect();
        assert_eq!(added, "+28+28");
        assert_eq!(removed, "-20-20");
        let flat: String = lines
            .iter()
            .flatten()
            .map(|part| part.text.as_str())
            .collect();
        assert!(flat.contains("**目录**/server.py"));
    }

    #[test]
    fn only_the_exact_runtime_summary_tail_is_replaced() {
        let review = json!({"summary":"Edits: native summary"});
        assert_eq!(
            answer_body("Answer.\n\nEdits: native summary", &review),
            ("Answer.", true)
        );
        assert_eq!(
            answer_body("Edits: native summary is an example", &review),
            ("Edits: native summary is an example", false)
        );
    }
}
