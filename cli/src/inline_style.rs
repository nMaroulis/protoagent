#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InlineKind {
    Text,
    Code,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct InlineSegment {
    pub(crate) kind: InlineKind,
    pub(crate) text: String,
}

pub(crate) fn inline_code_segments(text: &str) -> Vec<InlineSegment> {
    let mut segments = Vec::new();
    let mut cursor = 0usize;

    while cursor < text.len() {
        let Some(open) = find_single_backtick(text, cursor) else {
            push_segment(&mut segments, InlineKind::Text, &text[cursor..]);
            break;
        };
        let Some(close) = find_single_backtick(text, open + 1) else {
            push_segment(&mut segments, InlineKind::Text, &text[cursor..]);
            break;
        };
        if close == open + 1 {
            push_segment(&mut segments, InlineKind::Text, &text[cursor..=close]);
            cursor = close + 1;
            continue;
        }
        push_segment(&mut segments, InlineKind::Text, &text[cursor..open]);
        push_segment(&mut segments, InlineKind::Code, &text[open..=close]);
        cursor = close + 1;
    }

    if segments.is_empty() {
        segments.push(InlineSegment {
            kind: InlineKind::Text,
            text: String::new(),
        });
    }
    segments
}

fn find_single_backtick(text: &str, start: usize) -> Option<usize> {
    text[start..]
        .char_indices()
        .map(|(idx, _)| start + idx)
        .find(|idx| is_single_backtick(text, *idx))
}

fn is_single_backtick(text: &str, idx: usize) -> bool {
    if !text[idx..].starts_with('`') {
        return false;
    }
    let previous = text[..idx].chars().next_back();
    let next = text[idx + 1..].chars().next();
    previous != Some('`') && next != Some('`')
}

fn push_segment(segments: &mut Vec<InlineSegment>, kind: InlineKind, text: &str) {
    if text.is_empty() {
        return;
    }
    if let Some(last) = segments.last_mut() {
        if last.kind == kind {
            last.text.push_str(text);
            return;
        }
    }
    segments.push(InlineSegment {
        kind,
        text: text.to_string(),
    });
}

#[cfg(test)]
mod tests {
    use super::{inline_code_segments, InlineKind};

    #[test]
    fn extracts_single_backtick_spans() {
        let segments = inline_code_segments("Explorer read `abc.py` and `README.md`.");

        assert_eq!(segments.len(), 5);
        assert_eq!(segments[1].kind, InlineKind::Code);
        assert_eq!(segments[1].text, "`abc.py`");
        assert_eq!(segments[3].kind, InlineKind::Code);
        assert_eq!(segments[3].text, "`README.md`");
    }

    #[test]
    fn leaves_unmatched_and_fenced_backticks_plain() {
        let unmatched = inline_code_segments("file `abc.py is open");
        assert_eq!(unmatched.len(), 1);
        assert_eq!(unmatched[0].kind, InlineKind::Text);

        let fenced = inline_code_segments("```rust");
        assert_eq!(fenced.len(), 1);
        assert_eq!(fenced[0].kind, InlineKind::Text);
    }
}
