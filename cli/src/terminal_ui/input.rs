use std::collections::VecDeque;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// A multiline prompt buffer whose cursor always sits on a grapheme boundary.
pub(super) struct InputEditor {
    history: VecDeque<String>,
    buffer: String,
    cursor: usize,
    history_index: Option<usize>,
    draft: String,
}

impl InputEditor {
    pub(super) fn new(history: &VecDeque<String>) -> Self {
        Self::with_initial(history, "")
    }

    pub(super) fn with_initial(history: &VecDeque<String>, initial: &str) -> Self {
        let mut editor = Self {
            history: history.clone(),
            buffer: String::new(),
            cursor: 0,
            history_index: None,
            draft: String::new(),
        };
        editor.insert_str(initial);
        editor
    }

    pub(super) fn line(&self) -> String {
        self.buffer.clone()
    }
    pub(super) fn is_empty(&self) -> bool {
        self.buffer.is_empty()
    }

    /// Return a scrolling single-row view for existing modal input fields.
    pub(super) fn visible(&self, width: usize) -> (String, usize) {
        let (lines, column, _) = self.layout(width, 1);
        (lines.into_iter().next().unwrap_or_default(), column)
    }

    /// Wrap by terminal cells and keep the cursor within the visible row window.
    pub(super) fn layout(&self, width: usize, rows: usize) -> (Vec<String>, usize, usize) {
        let (lines, positions) = self.positions(width.max(1));
        let (_, column, row) = positions
            .iter()
            .find(|(byte, _, _)| *byte == self.cursor)
            .copied()
            .unwrap_or((0, 0, 0));
        let start = row.saturating_sub(rows.max(1) - 1);
        (
            lines.into_iter().skip(start).take(rows.max(1)).collect(),
            column,
            row - start,
        )
    }

    /// Build wrapped text and byte-to-cell cursor locations without splitting clusters.
    fn positions(&self, width: usize) -> (Vec<String>, Vec<(usize, usize, usize)>) {
        let mut lines = vec![String::new()];
        let mut positions = Vec::new();
        let mut column = 0;
        for (byte, grapheme) in self.buffer.grapheme_indices(true) {
            let cells = UnicodeWidthStr::width(grapheme);
            if grapheme != "\n" && column > 0 && column + cells > width {
                lines.push(String::new());
                column = 0;
            }
            positions.push((byte, column, lines.len() - 1));
            if grapheme == "\n" {
                lines.push(String::new());
                column = 0;
            } else {
                // A glyph wider than the entire terminal gets a one-cell marker.
                lines
                    .last_mut()
                    .unwrap()
                    .push_str(if cells > width { "�" } else { grapheme });
                column += cells.min(width);
            }
        }
        if column >= width {
            lines.push(String::new());
            column = 0;
        }
        positions.push((self.buffer.len(), column, lines.len() - 1));
        (lines, positions)
    }

    pub(super) fn insert(&mut self, ch: char) {
        self.insert_str(&ch.to_string());
    }

    /// Insert pasted text literally; newlines do not submit and tags do not open pickers.
    pub(super) fn insert_str(&mut self, text: &str) {
        let text = text
            .replace("\r\n", "\n")
            .replace('\r', "\n")
            .replace('\t', "  ");
        let text: String = text
            .chars()
            .filter(|ch| *ch == '\n' || !ch.is_control())
            .collect();
        self.buffer.insert_str(self.cursor, &text);
        self.cursor += text.len();
        self.snap_cursor();
        self.history_index = None;
    }

    fn snap_cursor(&mut self) {
        self.cursor = self
            .buffer
            .grapheme_indices(true)
            .map(|(index, _)| index)
            .find(|index| *index >= self.cursor)
            .unwrap_or(self.buffer.len());
        self.history_index = None;
    }

    pub(super) fn replace(&mut self, text: &str) {
        self.buffer.clear();
        self.cursor = 0;
        self.insert_str(text);
    }

    pub(super) fn backspace(&mut self) {
        if self.cursor > 0 {
            let previous = self.buffer[..self.cursor]
                .grapheme_indices(true)
                .next_back()
                .map(|(i, _)| i)
                .unwrap_or(0);
            self.buffer.replace_range(previous..self.cursor, "");
            self.cursor = previous;
            self.snap_cursor();
            self.history_index = None;
        }
    }

    pub(super) fn delete(&mut self) {
        if let Some(grapheme) = self.buffer[self.cursor..].graphemes(true).next() {
            let end = self.cursor + grapheme.len();
            self.buffer.replace_range(self.cursor..end, "");
            self.snap_cursor();
            self.history_index = None;
        }
    }

    pub(super) fn move_left(&mut self) {
        self.cursor = self.buffer[..self.cursor]
            .grapheme_indices(true)
            .next_back()
            .map(|(i, _)| i)
            .unwrap_or(0);
    }
    pub(super) fn move_right(&mut self) {
        if let Some(grapheme) = self.buffer[self.cursor..].graphemes(true).next() {
            self.cursor += grapheme.len();
        }
    }
    pub(super) fn move_home(&mut self) {
        self.cursor = self.buffer[..self.cursor]
            .rfind('\n')
            .map(|i| i + 1)
            .unwrap_or(0);
    }
    pub(super) fn move_end(&mut self) {
        self.cursor += self.buffer[self.cursor..]
            .find('\n')
            .unwrap_or(self.buffer.len() - self.cursor);
    }

    /// Move between visual lines; return false for a single-line history gesture.
    pub(super) fn move_vertical(&mut self, down: bool, width: usize) -> bool {
        let (lines, positions) = self.positions(width.max(1));
        if lines.len() == 1 {
            return false;
        }
        let (_, column, row) = positions
            .iter()
            .find(|(byte, _, _)| *byte == self.cursor)
            .copied()
            .unwrap();
        let target = if down {
            (row + 1).min(lines.len() - 1)
        } else {
            row.saturating_sub(1)
        };
        if let Some((byte, _, _)) = positions
            .iter()
            .rfind(|(_, col, y)| *y == target && *col <= column)
        {
            self.cursor = *byte;
        }
        true
    }

    pub(super) fn history_prev(&mut self) {
        if self.history.is_empty() {
            return;
        }
        let index = match self.history_index {
            Some(index) => index.saturating_sub(1),
            None => {
                self.draft = self.buffer.clone();
                self.history.len() - 1
            }
        };
        self.load_history(index);
    }
    pub(super) fn history_next(&mut self) {
        let Some(index) = self.history_index else {
            return;
        };
        if index + 1 < self.history.len() {
            self.load_history(index + 1);
        } else {
            self.history_index = None;
            self.buffer = self.draft.clone();
            self.cursor = self.buffer.len();
        }
    }
    fn load_history(&mut self, index: usize) {
        if let Some(value) = self.history.get(index) {
            self.buffer = value.clone();
            self.cursor = self.buffer.len();
            self.history_index = Some(index);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pastes_keep_newlines_and_literal_tags_without_control_bytes() {
        let mut editor = InputEditor::new(&VecDeque::new());
        editor.insert_str("one\r\n@src/main.rs\tthree\x1b");
        assert_eq!(editor.line(), "one\n@src/main.rs  three");
        let (lines, column, row) = editor.layout(40, 3);
        assert_eq!(lines.len(), 2);
        assert_eq!((column, row), (19, 1));
    }
    #[test]
    fn deletion_and_movement_preserve_emoji_and_combining_clusters() {
        let mut editor = InputEditor::with_initial(&VecDeque::new(), "a👩‍💻e\u{301}");
        editor.backspace();
        assert_eq!(editor.line(), "a👩‍💻");
        editor.move_left();
        editor.delete();
        assert_eq!(editor.line(), "a");
        editor.insert_str("界");
        assert_eq!(editor.layout(10, 3).1, 3);
    }
    #[test]
    fn wrapping_and_vertical_navigation_use_terminal_cells() {
        let mut editor = InputEditor::with_initial(&VecDeque::new(), "ab界cd\nnext");
        let (lines, column, row) = editor.layout(5, 2);
        assert_eq!(lines, vec!["d", "next"]);
        assert_eq!((column, row), (4, 1));
        assert!(editor.move_vertical(false, 5));
        editor.insert('!');
        assert_eq!(editor.line(), "ab界cd!\nnext");
    }
    #[test]
    fn removing_a_newline_that_joins_clusters_keeps_a_valid_cursor() {
        let mut editor = InputEditor::with_initial(&VecDeque::new(), "a\n\u{301}b");
        editor.move_home();
        editor.backspace();
        assert_eq!(editor.line(), "a\u{301}b");
        assert_eq!(editor.layout(20, 3).1, 1);
        editor.move_vertical(false, 20);
        editor.backspace();
        assert_eq!(editor.line(), "b");
    }

    #[test]
    fn history_restores_an_unsubmitted_multiline_draft() {
        let mut editor = InputEditor::new(&VecDeque::from(["old".to_string()]));
        editor.insert_str("draft\nsecond");
        editor.history_prev();
        assert_eq!(editor.line(), "old");
        editor.history_next();
        assert_eq!(editor.line(), "draft\nsecond");
    }
    #[test]
    fn explicit_newline_after_a_full_row_does_not_add_a_blank_row() {
        let editor = InputEditor::with_initial(&VecDeque::new(), "abc\nnext");
        assert_eq!(editor.layout(3, 4).0, vec!["abc", "nex", "t"]);
    }

    #[test]
    fn narrow_view_never_splits_wide_glyphs_or_loses_the_cursor() {
        let editor = InputEditor::with_initial(&VecDeque::new(), "界");
        assert_eq!(editor.layout(1, 1), (vec![String::new()], 0, 0));
    }
}
