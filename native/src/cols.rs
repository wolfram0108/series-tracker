//! Records kept by columns: the one form of the rows of a table and of a layout (and of any list of records) in the base,
//! in the count and between the modules. A reader takes a column by its name and reads its values in place: nothing is parsed, nothing is
//! made. A field added later is one more column; a record written before it has none, and its readers get the default.
//!
//! Layout (little endian): "COLS" u32 version, u32 rows, u32 columns, then per column: u16 name length, name, u8 kind,
//! u32 offset, u32 length (of its data, from the start of the field); then the data of the columns.
//! Kinds: 's' text (u32 offsets[rows+1], bytes), 'S' text or none (bitmap, then as 's'), 'i' integer or none (bitmap,
//! i64[rows]), 'b' flag (u8[rows]), 'f' number or none (bitmap, f64[rows]), 'L' list of integers (u32 offsets[rows+1],
//! i64[...]).

use std::collections::HashMap;

pub enum Column<'a> {
    Text(Vec<String>),
    MaybeText(Vec<Option<String>>),
    // Texts borrowed from where they are (a row of a table): written with no copy of their own.
    Strs(Vec<&'a str>),
    MaybeStrs(Vec<Option<&'a str>>),
    IntLists(Vec<&'a [i64]>),
    Int(Vec<Option<i64>>),
    Flag(Vec<bool>),
    Float(Vec<Option<f64>>),
    Ints(Vec<Vec<i64>>),
}

fn bitmap(present: impl Iterator<Item = bool>, rows: usize, out: &mut Vec<u8>) {
    let mut bits = vec![0u8; (rows + 7) / 8];
    for (i, p) in present.enumerate() {
        if p {
            bits[i / 8] |= 1 << (i % 8);
        }
    }
    out.extend_from_slice(&bits);
}

fn texts<'a>(values: impl Iterator<Item = &'a str>, out: &mut Vec<u8>) {
    let values: Vec<&str> = values.collect();
    out.reserve((values.len() + 1) * 4 + values.iter().map(|v| v.len()).sum::<usize>());
    let mut offset = 0u32;
    out.extend_from_slice(&offset.to_le_bytes());
    for v in &values {
        offset += v.len() as u32;
        out.extend_from_slice(&offset.to_le_bytes());
    }
    for v in &values {
        out.extend_from_slice(v.as_bytes());
    }
}

pub fn encode(rows: usize, columns: &[(&str, Column<'_>)]) -> Vec<u8> {
    // The data of every column goes straight after the directory, which is written first with its places left blank and
    // filled once the columns are written: the field is made in one buffer, nothing is copied twice.
    let directory: usize = columns.iter().map(|(name, _)| 2 + name.len() + 1 + 4 + 4).sum();
    let mut out = Vec::with_capacity(16 + directory + rows * 64 * columns.len().max(1));
    out.extend_from_slice(b"COLS");
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(rows as u32).to_le_bytes());
    out.extend_from_slice(&(columns.len() as u32).to_le_bytes());
    let mut places = Vec::with_capacity(columns.len());
    for (name, _) in columns {
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(name.as_bytes());
        places.push(out.len());
        out.extend_from_slice(&[0u8; 9]);
    }
    for ((_, column), place) in columns.iter().zip(places) {
        let start = out.len();
        let data = &mut out;
        let kind = match column {
            Column::Text(v) => {
                texts(v.iter().map(String::as_str), data);
                b's'
            }
            Column::MaybeText(v) => {
                bitmap(v.iter().map(Option::is_some), rows, data);
                texts(v.iter().map(|x| x.as_deref().unwrap_or("")), data);
                b'S'
            }
            Column::IntLists(v) => {
                lists(v.iter().copied(), data);
                b'L'
            }
            Column::Strs(v) => {
                texts(v.iter().copied(), data);
                b's'
            }
            Column::MaybeStrs(v) => {
                bitmap(v.iter().map(Option::is_some), rows, data);
                texts(v.iter().map(|x| x.unwrap_or("")), data);
                b'S'
            }
            Column::Int(v) => {
                bitmap(v.iter().map(Option::is_some), rows, data);
                data.reserve(v.len() * 8);
                for x in v {
                    data.extend_from_slice(&x.unwrap_or(0).to_le_bytes());
                }
                b'i'
            }
            Column::Flag(v) => {
                data.extend(v.iter().map(|x| *x as u8));
                b'b'
            }
            Column::Float(v) => {
                bitmap(v.iter().map(Option::is_some), rows, data);
                for x in v {
                    data.extend_from_slice(&x.unwrap_or(0.0).to_le_bytes());
                }
                b'f'
            }
            Column::Ints(v) => {
                lists(v.iter().map(Vec::as_slice), data);
                b'L'
            }
        };
        let length = (out.len() - start) as u32;
        out[place] = kind;
        out[place + 1..place + 5].copy_from_slice(&(start as u32).to_le_bytes());
        out[place + 5..place + 9].copy_from_slice(&length.to_le_bytes());
    }
    out
}

fn lists<'a>(values: impl Iterator<Item = &'a [i64]> + Clone, out: &mut Vec<u8>) {
    let mut offset = 0u32;
    out.extend_from_slice(&offset.to_le_bytes());
    for x in values.clone() {
        offset += x.len() as u32;
        out.extend_from_slice(&offset.to_le_bytes());
    }
    for x in values.flatten() {
        out.extend_from_slice(&x.to_le_bytes());
    }
}

/// A field of records kept by columns, read in place.
pub struct View<'a> {
    pub rows: usize,
    columns: HashMap<&'a str, (u8, &'a [u8])>,
}

fn u32_at(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}

impl<'a> View<'a> {
    pub fn of(bytes: &'a [u8]) -> Result<View<'a>, String> {
        if bytes.len() < 16 || &bytes[..4] != b"COLS" {
            return Err("not a field of columns".into());
        }
        let rows = u32_at(bytes, 8) as usize;
        let count = u32_at(bytes, 12) as usize;
        let mut at = 16;
        let mut columns = HashMap::new();
        for _ in 0..count {
            let len = u16::from_le_bytes(bytes[at..at + 2].try_into().unwrap()) as usize;
            let name = std::str::from_utf8(&bytes[at + 2..at + 2 + len]).map_err(|e| e.to_string())?;
            at += 2 + len;
            let kind = bytes[at];
            let offset = u32_at(bytes, at + 1) as usize;
            let length = u32_at(bytes, at + 5) as usize;
            at += 9;
            columns.insert(name, (kind, bytes.get(offset..offset + length).ok_or("a column out of the field")?));
        }
        Ok(View { rows, columns })
    }

    pub fn text(&self, name: &str) -> Texts<'a> {
        match self.columns.get(name) {
            Some((b's', data)) => Texts { present: None, offsets: data, bytes: &data[(self.rows + 1) * 4..] },
            Some((b'S', data)) => {
                let bits = (self.rows + 7) / 8;
                let rest = &data[bits..];
                Texts { present: Some(&data[..bits]), offsets: rest, bytes: &rest[(self.rows + 1) * 4..] }
            }
            // A column the record was written without: every value is the default.
            _ => Texts { present: Some(&[]), offsets: &[], bytes: &[] },
        }
    }

    pub fn ints(&self, name: &str) -> Ints<'a> {
        match self.columns.get(name) {
            Some((b'L', data)) => Ints { offsets: data, values: &data[(self.rows + 1) * 4..] },
            _ => Ints { offsets: &[], values: &[] },
        }
    }

    pub fn int(&self, name: &str) -> Int<'a> {
        match self.columns.get(name) {
            Some((b'i', data)) => {
                let bits = (self.rows + 7) / 8;
                Int { present: &data[..bits], values: &data[bits..] }
            }
            _ => Int { present: &[], values: &[] },
        }
    }

    pub fn flag(&self, name: &str) -> Flags<'a> {
        match self.columns.get(name) {
            Some((b'b', data)) => Flags { values: data },
            _ => Flags { values: &[] },
        }
    }
}

pub struct Int<'a> {
    present: &'a [u8],
    values: &'a [u8],
}

impl<'a> Int<'a> {
    pub fn get(&self, row: usize) -> Option<i64> {
        if self.present.get(row / 8).map_or(true, |b| b & (1 << (row % 8)) == 0) {
            return None;
        }
        Some(i64::from_le_bytes(self.values[row * 8..row * 8 + 8].try_into().unwrap()))
    }
}

pub struct Flags<'a> {
    values: &'a [u8],
}

impl<'a> Flags<'a> {
    /// False for a record written without the column.
    pub fn get(&self, row: usize) -> bool {
        self.values.get(row).map_or(false, |v| *v != 0)
    }
}

impl<'a> View<'a> {
    /// Every record as a JSON object of its columns, in the order they are written: for a reader that knows none of them
    /// (the dump of a run).
    pub fn records_json(&self, bytes: &[u8]) -> String {
        let mut names: Vec<(&str, u8, usize)> = Vec::new();
        let count = u32_at(bytes, 12) as usize;
        let mut at = 16;
        for _ in 0..count {
            let len = u16::from_le_bytes(bytes[at..at + 2].try_into().unwrap()) as usize;
            let name = std::str::from_utf8(&bytes[at + 2..at + 2 + len]).unwrap_or("");
            at += 2 + len;
            names.push((name, bytes[at], u32_at(bytes, at + 1) as usize));
            at += 9;
        }
        names.sort_by_key(|(_, _, offset)| *offset);
        let mut out = String::from("[");
        for row in 0..self.rows {
            if row > 0 {
                out.push(',');
            }
            out.push('{');
            for (i, (name, kind, _)) in names.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(name).unwrap());
                out.push(':');
                let value = match kind {
                    b's' | b'S' => self.text(name).get(row).map_or(serde_json::Value::Null, |t| t.into()),
                    b'i' => self.int(name).get(row).map_or(serde_json::Value::Null, Into::into),
                    b'b' => self.flag(name).get(row).into(),
                    b'L' => self.ints(name).get(row).collect::<Vec<i64>>().into(),
                    b'f' => {
                        let (_, data) = self.columns[name];
                        let bits = (self.rows + 7) / 8;
                        if data.get(row / 8).map_or(false, |b| b & (1 << (row % 8)) != 0) {
                            f64::from_le_bytes(data[bits + row * 8..bits + row * 8 + 8].try_into().unwrap()).into()
                        } else {
                            serde_json::Value::Null
                        }
                    }
                    _ => serde_json::Value::Null,
                };
                out.push_str(&value.to_string());
            }
            out.push('}');
        }
        out.push(']');
        out
    }
}

pub struct Texts<'a> {
    present: Option<&'a [u8]>,
    offsets: &'a [u8],
    bytes: &'a [u8],
}

impl<'a> Texts<'a> {
    /// The text of a row; None where the column holds none for it (or has no such column).
    pub fn get(&self, row: usize) -> Option<&'a str> {
        if let Some(bits) = self.present {
            if bits.get(row / 8).map_or(true, |b| b & (1 << (row % 8)) == 0) {
                return None;
            }
        }
        if self.offsets.is_empty() {
            return None;
        }
        let (start, end) = (u32_at(self.offsets, row * 4) as usize, u32_at(self.offsets, row * 4 + 4) as usize);
        // The field was written by `encode` from text: its bytes are UTF-8 by construction.
        Some(unsafe { std::str::from_utf8_unchecked(&self.bytes[start..end]) })
    }
}

pub struct Ints<'a> {
    offsets: &'a [u8],
    values: &'a [u8],
}

impl<'a> Ints<'a> {
    pub fn get(&self, row: usize) -> impl Iterator<Item = i64> + 'a {
        let (start, end) = if self.offsets.is_empty() { (0, 0) }
            else { (u32_at(self.offsets, row * 4) as usize, u32_at(self.offsets, row * 4 + 4) as usize) };
        let values = self.values;
        (start..end).map(move |i| i64::from_le_bytes(values[i * 8..i * 8 + 8].try_into().unwrap()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_field_added_later_reads_as_the_default_in_a_record_written_before_it_and_is_passed_over_by_an_older_reader() {
        let before = encode(2, &[("key", Column::Text(vec!["a".into(), "b".into()])),
                                 ("heights", Column::Ints(vec![vec![720, 1080], vec![]]))]);
        let after = encode(2, &[("key", Column::Text(vec!["a".into(), "b".into()])),
                                ("heights", Column::Ints(vec![vec![720, 1080], vec![]])),
                                ("language", Column::MaybeText(vec![Some("rus".into()), None]))]);
        // The reader that knows the new field, on a record written before it: the default, nothing broken.
        let old = View::of(&before).unwrap();
        assert_eq!((old.text("language").get(0), old.text("key").get(1)), (None, Some("b")));
        assert_eq!(old.ints("heights").get(0).collect::<Vec<_>>(), vec![720, 1080]);
        // A reader that does not know it, on a record written with it: the fields it knows, as they were.
        let new = View::of(&after).unwrap();
        assert_eq!((new.text("key").get(0), new.ints("heights").get(1).count()), (Some("a"), 0));
        assert_eq!((new.text("language").get(0), new.text("language").get(1)), (Some("rus"), None));
    }
}

/// A field of records kept by columns, owned, with its directory read once: a value is read by the number of its column,
/// with no lookup by name (a layout read again and again by the interface).
pub struct Store {
    pub bytes: Vec<u8>,
    pub rows: usize,
    // (kind, offset, length) of each column asked for, in the order of the names given; None for a column the record
    // was written without.
    columns: Vec<Option<(u8, usize, usize)>>,
}

impl Store {
    pub fn of(bytes: Vec<u8>, names: &[&str]) -> Result<Store, String> {
        let (rows, columns) = {
            let view = View::of(&bytes)?;
            let base = bytes.as_ptr() as usize;
            let columns = names.iter().map(|name| view.columns.get(name).map(|(kind, data)| {
                (*kind, data.as_ptr() as usize - base, data.len())
            })).collect();
            (view.rows, columns)
        };
        Ok(Store { bytes, rows, columns })
    }

    fn data(&self, column: usize) -> Option<(u8, &[u8])> {
        self.columns[column].map(|(kind, at, len)| (kind, &self.bytes[at..at + len]))
    }

    fn present(&self, data: &[u8], row: usize) -> bool {
        data.get(row / 8).map_or(false, |b| b & (1 << (row % 8)) != 0)
    }

    pub fn text(&self, column: usize, row: usize) -> Option<&str> {
        let (kind, data) = self.data(column)?;
        let bits = (self.rows + 7) / 8;
        let rest = match kind {
            b's' => data,
            b'S' if self.present(data, row) => &data[bits..],
            _ => return None,
        };
        let (start, end) = (u32_at(rest, row * 4) as usize, u32_at(rest, row * 4 + 4) as usize);
        let bytes = &rest[(self.rows + 1) * 4..];
        // Written by `encode` from text: UTF-8 by construction.
        Some(unsafe { std::str::from_utf8_unchecked(&bytes[start..end]) })
    }

    /// The text of a row, empty where the record holds none.
    pub fn str(&self, column: usize, row: usize) -> &str {
        self.text(column, row).unwrap_or("")
    }

    pub fn int(&self, column: usize, row: usize) -> Option<i64> {
        match self.data(column)? {
            (b'i', data) if self.present(data, row) => {
                let at = (self.rows + 7) / 8 + row * 8;
                Some(i64::from_le_bytes(data[at..at + 8].try_into().unwrap()))
            }
            _ => None,
        }
    }

    pub fn float(&self, column: usize, row: usize) -> Option<f64> {
        match self.data(column)? {
            (b'f', data) if self.present(data, row) => {
                let at = (self.rows + 7) / 8 + row * 8;
                Some(f64::from_le_bytes(data[at..at + 8].try_into().unwrap()))
            }
            _ => None,
        }
    }

    pub fn flag(&self, column: usize, row: usize) -> bool {
        matches!(self.data(column), Some((b'b', data)) if data.get(row).map_or(false, |v| *v != 0))
    }

    pub fn ints(&self, column: usize, row: usize) -> Vec<i64> {
        match self.data(column) {
            Some((b'L', data)) => {
                let (start, end) = (u32_at(data, row * 4) as usize, u32_at(data, row * 4 + 4) as usize);
                let values = &data[(self.rows + 1) * 4..];
                (start..end).map(|i| i64::from_le_bytes(values[i * 8..i * 8 + 8].try_into().unwrap())).collect()
            }
            _ => Vec::new(),
        }
    }
}
