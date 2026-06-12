use std::collections::VecDeque;

pub(super) struct InputEditor {
    history: VecDeque<String>,
    buffer: Vec<char>,
    cursor: usize,
    history_index: Option<usize>,
    draft: Vec<char>,
}

impl InputEditor {
    pub(super) fn new(history: &VecDeque<String>) -> Self {
        Self {
            history: history.clone(),
            buffer: Vec::new(),
            cursor: 0,
            history_index: None,
            draft: Vec::new(),
        }
    }

    pub(super) fn with_initial(history: &VecDeque<String>, initial: &str) -> Self {
        let buffer = initial.chars().collect::<Vec<_>>();
        let cursor = buffer.len();
        Self {
            history: history.clone(),
            buffer,
            cursor,
            history_index: None,
            draft: Vec::new(),
        }
    }

    pub(super) fn line(&self) -> String {
        self.buffer.iter().collect()
    }

    pub(super) fn is_empty(&self) -> bool {
        self.buffer.is_empty()
    }

    pub(super) fn visible(&self, width: usize) -> (String, usize) {
        let offset = if self.cursor >= width {
            self.cursor + 1 - width
        } else {
            0
        };
        let visible = self.buffer.iter().skip(offset).take(width).collect();
        (visible, self.cursor.saturating_sub(offset))
    }

    pub(super) fn insert(&mut self, ch: char) {
        self.buffer.insert(self.cursor, ch);
        self.cursor += 1;
        self.history_index = None;
    }

    pub(super) fn insert_str(&mut self, text: &str) {
        for ch in text.chars() {
            self.insert(ch);
        }
    }

    pub(super) fn backspace(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.buffer.remove(self.cursor);
            self.history_index = None;
        }
    }

    pub(super) fn delete(&mut self) {
        if self.cursor < self.buffer.len() {
            self.buffer.remove(self.cursor);
            self.history_index = None;
        }
    }

    pub(super) fn move_left(&mut self) {
        self.cursor = self.cursor.saturating_sub(1);
    }

    pub(super) fn move_right(&mut self) {
        self.cursor = (self.cursor + 1).min(self.buffer.len());
    }

    pub(super) fn move_home(&mut self) {
        self.cursor = 0;
    }

    pub(super) fn move_end(&mut self) {
        self.cursor = self.buffer.len();
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
            self.history_index = Some(index);
            self.buffer = value.chars().collect();
            self.cursor = self.buffer.len();
        }
    }
}
