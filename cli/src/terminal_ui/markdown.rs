//! Presentation-only Markdown for complete answers and their growing prefixes.
//! Parse before wrapping so styles survive line breaks and transport chunks.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(super) enum Code {
    #[default]
    None,
    Inline,
    Block,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(super) struct Style {
    pub(super) bold: bool,
    pub(super) italic: bool,
    pub(super) code: Code,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Span {
    pub(super) text: String,
    pub(super) style: Style,
}

#[derive(Default)]
struct InlineState {
    strong: Option<&'static str>,
    emphasis: Option<char>,
    ticks: usize,
}

impl InlineState {
    fn style(&self, heading: bool) -> Style {
        Style {
            bold: heading || self.strong.is_some(),
            italic: self.emphasis.is_some(),
            code: if self.ticks > 0 {
                Code::Inline
            } else {
                Code::None
            },
        }
    }

    fn parse(&mut self, line: &str, live_tail: bool, heading: bool) -> Vec<Span> {
        let mut spans = vec![];
        let mut offset = 0;
        while offset < line.len() {
            let rest = &line[offset..];
            let ch = rest.chars().next().unwrap();
            let previous = line[..offset].chars().next_back();
            if self.ticks == 0 && ch == '\\' {
                if let Some(escaped) = rest[1..].chars().next().filter(char::is_ascii_punctuation) {
                    push(&mut spans, &escaped.to_string(), self.style(heading));
                    offset += 1 + escaped.len_utf8();
                    continue;
                }
            }
            if ch == '`' {
                let count = rest.chars().take_while(|&ch| ch == '`').count();
                if self.ticks == 0 {
                    self.ticks = count;
                } else if count == self.ticks {
                    self.ticks = 0;
                } else if !(live_tail && count == rest.len() && count < self.ticks) {
                    push(&mut spans, &rest[..count], self.style(heading));
                }
                offset += count;
                continue;
            }
            if self.ticks == 0 && matches!(ch, '*' | '_') {
                // Hold a split opening/closing delimiter at the live edge.
                // Full messages are reparsed; no transport-specific state leaks.
                if live_tail
                    && rest.len() == 1
                    && (self.strong.is_some() || previous.is_none_or(|ch| ch.is_whitespace()))
                {
                    break;
                }
                let strong = if rest.starts_with("**") {
                    Some("**")
                } else if rest.starts_with("__") {
                    Some("__")
                } else {
                    None
                };
                let count = if strong.is_some() { 2 } else { 1 };
                let next = rest[count..].chars().next();
                let opens = next.is_some_and(|ch| !ch.is_whitespace())
                    && (previous.is_none_or(|ch| !ch.is_alphanumeric())
                        || (ch == '*'
                            && rest[count..].contains(if count == 2 { "**" } else { "*" })));
                let closes = previous.is_some_and(|ch| !ch.is_whitespace());
                if let Some(marker) = strong {
                    if self.strong == Some(marker) && closes {
                        self.strong = None;
                        offset += count;
                        continue;
                    }
                    if self.strong.is_none() && (opens || (live_tail && rest.len() == count)) {
                        self.strong = Some(marker);
                        offset += count;
                        continue;
                    }
                } else if self.emphasis == Some(ch) && closes {
                    self.emphasis = None;
                    offset += count;
                    continue;
                } else if self.emphasis.is_none() && opens {
                    self.emphasis = Some(ch);
                    offset += count;
                    continue;
                }
                // A literal double marker inside an identifier must stay a
                // pair, rather than letting its second character open emphasis.
                push(&mut spans, &rest[..count], self.style(heading));
                offset += count;
                continue;
            }
            let grapheme = rest.graphemes(true).next().unwrap();
            push(&mut spans, grapheme, self.style(heading));
            offset += grapheme.len();
        }
        spans
    }
}

pub(super) fn layout(text: &str, width: usize, streaming: bool) -> Vec<Vec<Span>> {
    let mut rows = vec![];
    let mut inline = InlineState::default();
    let mut fence: Option<(char, usize)> = None;
    // Tabs must occupy predictable cells before wrapping or positioning spans.
    let expanded = text.replace('\t', "    ");
    let source: Vec<_> = expanded.split('\n').collect();
    for (index, line) in source.iter().enumerate() {
        let live_tail = streaming && index + 1 == source.len();
        let trimmed = line.trim_start_matches(' ');
        let indent = line.len() - trimmed.len();
        let marker = trimmed.chars().next().filter(|ch| matches!(ch, '`' | '~'));
        let count = marker.map_or(0, |marker| {
            trimmed.chars().take_while(|&ch| ch == marker).count()
        });
        let spans = if let Some((mark, length)) = fence {
            if indent <= 3
                && marker == Some(mark)
                && trimmed[count..].trim().is_empty()
                && (count >= length || live_tail)
            {
                if count >= length {
                    fence = None;
                }
                // Retain the closing row so its arrival cannot pull text up.
                vec![]
            } else {
                vec![Span {
                    text: (*line).into(),
                    style: Style {
                        code: Code::Block,
                        ..Default::default()
                    },
                }]
            }
        } else if indent <= 3 && count >= 3 {
            fence = Some((marker.unwrap(), count));
            inline = InlineState::default();
            vec![Span {
                text: trimmed[count..].trim().into(),
                style: Style {
                    bold: true,
                    code: Code::Block,
                    ..Default::default()
                },
            }]
        } else {
            let hashes = trimmed.chars().take_while(|&ch| ch == '#').count();
            let heading =
                indent <= 3 && (1..=6).contains(&hashes) && trimmed[hashes..].starts_with(' ');
            inline.parse(
                if heading {
                    trimmed[hashes..].trim_start()
                } else {
                    line
                },
                live_tail,
                heading,
            )
        };
        rows.extend(wrap(&spans, width.max(1)));
    }
    rows
}

fn push(spans: &mut Vec<Span>, value: &str, style: Style) {
    if value.is_empty() {
        return;
    }
    if let Some(last) = spans.last_mut().filter(|span| span.style == style) {
        last.text.push_str(value);
    } else {
        spans.push(Span {
            text: value.into(),
            style,
        });
    }
}

fn wrap(spans: &[Span], width: usize) -> Vec<Vec<Span>> {
    let glyphs: Vec<_> = spans
        .iter()
        .flat_map(|span| {
            span.text
                .graphemes(true)
                .map(move |glyph| (glyph, span.style))
        })
        .collect();
    let mut rows = vec![];
    let mut start = 0;
    while start < glyphs.len() {
        let mut end = start;
        let mut cells = 0;
        let mut space = None;
        while end < glyphs.len() {
            let (glyph, style) = glyphs[end];
            let used = glyph.width().min(width);
            if cells + used > width {
                break;
            }
            if glyph.trim().is_empty() && style.code == Code::None && end > start {
                space = Some(end);
            }
            cells += used;
            end += 1;
        }
        let split = if end < glyphs.len() {
            space.unwrap_or(end)
        } else {
            end
        };
        let mut row = vec![];
        for &(glyph, style) in &glyphs[start..split] {
            push(
                &mut row,
                if glyph.width() > width { "�" } else { glyph },
                style,
            );
        }
        rows.push(row);
        start = split;
        if split < end {
            while start < glyphs.len()
                && glyphs[start].0.trim().is_empty()
                && glyphs[start].1.code == Code::None
            {
                start += 1;
            }
        }
    }
    if rows.is_empty() {
        rows.push(vec![]);
    }
    rows
}

#[cfg(test)]
mod tests {
    use super::*;

    fn visible(rows: &[Vec<Span>]) -> String {
        rows.iter()
            .map(|row| {
                row.iter()
                    .map(|span| span.text.as_str())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn split_bold_delimiters_do_not_flash_or_lose_formatting() {
        for source in ["**bold", "**bold*", "**bold**"] {
            let rows = layout(source, 80, true);
            assert_eq!(visible(&rows), "bold");
            assert!(rows[0].iter().all(|span| span.style.bold));
        }
        for source in ["*", "**"] {
            assert_eq!(visible(&layout(source, 80, true)), "");
        }
        let rows = layout("**bold** plain", 80, false);
        assert_eq!(visible(&rows), "bold plain");
        assert!(!rows[0].last().unwrap().style.bold);
    }

    #[test]
    fn inline_code_streams_without_parsing_its_contents_as_emphasis() {
        for source in ["`a ** b", "`a ** b`"] {
            let rows = layout(source, 80, true);
            assert_eq!(visible(&rows), "a ** b");
            assert!(rows[0]
                .iter()
                .all(|span| span.style.code == Code::Inline && !span.style.bold));
        }
        for source in ["``a ` b", "``a ` b`", "``a ` b``"] {
            assert_eq!(visible(&layout(source, 80, true)), "a ` b");
        }
        let rows = layout("`code` plain", 80, false);
        assert_eq!(rows[0].last().unwrap().style, Style::default());
    }

    #[test]
    fn styles_cross_lines_and_wrap_using_visible_grapheme_width() {
        let rows = layout("**café 界👩‍💻e\u{301}\nnext**", 5, true);
        assert_eq!(visible(&rows), "café\n界👩‍💻e\u{301}\nnext");
        for row in rows {
            assert!(row.iter().map(|span| span.text.width()).sum::<usize>() <= 5);
            assert!(row.iter().all(|span| span.style.bold));
        }
        assert_eq!(visible(&layout("界", 1, true)), "�");
    }

    #[test]
    fn fenced_code_keeps_indentation_and_literal_markup_during_generation() {
        let source = "```rust\n\tlet value = \"**bold**\";\n";
        for suffix in ["", "`", "``", "```"] {
            let rows = layout(&format!("{source}{suffix}"), 80, true);
            assert_eq!(visible(&rows), "rust\n    let value = \"**bold**\";\n");
            assert!(rows[1]
                .iter()
                .all(|span| span.style.code == Code::Block && !span.style.bold));
        }
        let rows = layout("~~~\n    12345678\n~~~\nplain", 6, false);
        assert_eq!(visible(&rows), "\n    12\n345678\n\nplain");
        assert_eq!(rows.last().unwrap()[0].style, Style::default());
    }

    #[test]
    fn keeps_lists_identifiers_escapes_and_nested_emphasis_readable() {
        let source = "# Heading\n* item\nsnake_case double__underscore a * b\n\\*literal\\* **bold _italic_**";
        let rows = layout(source, 100, false);
        assert_eq!(
            visible(&rows),
            "Heading\n* item\nsnake_case double__underscore a * b\n*literal* bold italic"
        );
        assert!(rows[0][0].style.bold);
        assert!(rows[2].iter().all(|span| span.style == Style::default()));
        let nested = rows[3].last().unwrap();
        assert!(nested.style.bold && nested.style.italic);
    }

    #[test]
    fn finalization_preserves_formatted_rows_and_trailing_newlines() {
        for source in [
            "**bold** `code` _italic_\n",
            "## Heading\n\n```python\n  print('hi')\n```",
            "***both***",
        ] {
            for width in [1, 8, 20, 80] {
                assert_eq!(layout(source, width, true), layout(source, width, false));
            }
        }
    }
}
