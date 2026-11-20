//! The rows of a layout (core/contracts/composition.py, CompositionRow) kept by columns: made by the count, stored as they
//! are, read in place by every reader — the window gets their JSON written from the columns, the module asks what it needs
//! of them (the episodes they cover, what changed between two layouts, the compilations) without a model made.

use serde::Serialize;
use std::collections::{HashMap, HashSet};

use crate::cols::{self, Column, Store};

pub const COLUMNS: &[&str] = &[
    "series_id", "source_id", "row_key", "excluded", "exclusion", "season", "episode_start", "episode_end", "part",
    "voice_group", "voice_label", "voice_name", "voice_unknown", "player", "player_label", "group", "text", "heights",
    "state", "published", "link", "video", "timeline", "track", "language", "subtitle_type", "stale",
];

pub mod c {
    pub const SERIES_ID: usize = 0;
    pub const SOURCE_ID: usize = 1;
    pub const ROW_KEY: usize = 2;
    pub const EXCLUDED: usize = 3;
    pub const EXCLUSION: usize = 4;
    pub const SEASON: usize = 5;
    pub const START: usize = 6;
    pub const END: usize = 7;
    pub const PART: usize = 8;
    pub const VOICE_GROUP: usize = 9;
    pub const VOICE_LABEL: usize = 10;
    pub const VOICE_NAME: usize = 11;
    pub const VOICE_UNKNOWN: usize = 12;
    pub const PLAYER: usize = 13;
    pub const PLAYER_LABEL: usize = 14;
    pub const GROUP: usize = 15;
    pub const TEXT: usize = 16;
    pub const HEIGHTS: usize = 17;
    pub const STATE: usize = 18;
    pub const PUBLISHED: usize = 19;
    pub const LINK: usize = 20;
    pub const VIDEO: usize = 21;
    pub const TIMELINE: usize = 22;
    pub const TRACK: usize = 23;
    pub const LANGUAGE: usize = 24;
    pub const SUBTITLE_TYPE: usize = 25;
    pub const STALE: usize = 26;
}

// The exclusions made before the constructor: the row is asked nothing and names no voice-over group.
pub const LEFT_BEFORE_RULES: &[&str] = &["source", "settings"];

/// A row of a layout, its fields borrowed from where they come from (the table, the answer, the count).
#[derive(Serialize, Clone)]
pub struct Row<'a> {
    pub series_id: i64,
    pub source_id: i64,
    pub row_key: &'a str,
    pub excluded: bool,
    pub exclusion: Option<&'a str>,
    pub season: Option<i64>,
    pub episode_start: Option<i64>,
    pub episode_end: Option<i64>,
    pub part: Option<i64>,
    pub voice_group: Option<std::borrow::Cow<'a, str>>,
    pub voice_label: std::borrow::Cow<'a, str>,
    pub voice_name: std::borrow::Cow<'a, str>,
    pub voice_unknown: Option<i64>,
    pub player: &'a str,
    pub player_label: &'a str,
    pub group: &'a str,
    pub text: &'a str,
    pub heights: Vec<i64>,
    pub state: &'a str,
    pub published: Option<&'a str>,
    pub link: &'a str,
    pub video: &'a str,
    pub timeline: &'a str,
    pub track: &'a str,
    pub language: &'a str,
    pub subtitle_type: &'a str,
    pub stale: bool,
    // Written last, as the model writes its computed field.
    pub in_output: bool,
}

impl<'a> Row<'a> {
    /// The rules gave the row its numbers and the groups took it (CompositionRow.in_output).
    pub fn output(&self) -> bool {
        !self.excluded && self.season.is_some() && self.episode_start.is_some()
            && (self.voice_group.is_some() || self.track == "subtitles")
    }

    /// The (season, episode) pairs it covers; none for an excluded row or one without a season.
    pub fn covered(&self) -> Vec<(i64, i64)> {
        covered(self.excluded, self.season, self.episode_start, self.episode_end)
    }
}

fn covered(excluded: bool, season: Option<i64>, start: Option<i64>, end: Option<i64>) -> Vec<(i64, i64)> {
    match (excluded, season, start) {
        (false, Some(season), Some(start)) => {
            let end = end.unwrap_or(start).max(start);
            (start..=end).map(|n| (season, n)).collect()
        }
        _ => Vec::new(),
    }
}

/// The rows as columns.
pub fn encode(rows: &[&Row]) -> Vec<u8> {
    let s = |f: &dyn for<'r> Fn(&'r Row<'_>) -> &'r str| Column::Strs(rows.iter().map(|r| f(r)).collect());
    let m = |f: &dyn for<'r> Fn(&'r Row<'_>) -> Option<&'r str>| Column::MaybeStrs(rows.iter().map(|r| f(r)).collect());
    let i = |f: &dyn Fn(&Row) -> Option<i64>| Column::Int(rows.iter().map(|r| f(r)).collect());
    let b = |f: &dyn Fn(&Row) -> bool| Column::Flag(rows.iter().map(|r| f(r)).collect());
    let columns = vec![
        ("series_id", i(&|r| Some(r.series_id))), ("source_id", i(&|r| Some(r.source_id))),
        ("row_key", s(&|r| r.row_key)), ("excluded", b(&|r| r.excluded)), ("exclusion", m(&|r| r.exclusion)),
        ("season", i(&|r| r.season)), ("episode_start", i(&|r| r.episode_start)), ("episode_end", i(&|r| r.episode_end)),
        ("part", i(&|r| r.part)), ("voice_group", m(&|r| r.voice_group.as_deref())),
        ("voice_label", s(&|r| &r.voice_label)), ("voice_name", s(&|r| &r.voice_name)),
        ("voice_unknown", i(&|r| r.voice_unknown)), ("player", s(&|r| r.player)), ("player_label", s(&|r| r.player_label)),
        ("group", s(&|r| r.group)), ("text", s(&|r| r.text)),
        ("heights", Column::IntLists(rows.iter().map(|r| r.heights.as_slice()).collect())), ("state", s(&|r| r.state)),
        ("published", m(&|r| r.published)), ("link", s(&|r| r.link)), ("video", s(&|r| r.video)),
        ("timeline", s(&|r| r.timeline)), ("track", s(&|r| r.track)), ("language", s(&|r| r.language)),
        ("subtitle_type", s(&|r| r.subtitle_type)), ("stale", b(&|r| r.stale)),
    ];
    cols::encode(rows.len(), &columns)
}

/// A layout field read: its rows, by the number of their columns.
pub struct Layout {
    pub store: Store,
}

impl Layout {
    pub fn of(bytes: Vec<u8>) -> Result<Layout, String> {
        let bytes = if bytes.is_empty() { encode(&[]) } else { bytes };
        Ok(Layout { store: Store::of(bytes, COLUMNS)? })
    }

    pub fn len(&self) -> usize {
        self.store.rows
    }

    pub fn row(&self, i: usize) -> Row<'_> {
        let s = &self.store;
        let mut row = Row {
            series_id: s.int(c::SERIES_ID, i).unwrap_or(0), source_id: s.int(c::SOURCE_ID, i).unwrap_or(0),
            row_key: s.str(c::ROW_KEY, i), excluded: s.flag(c::EXCLUDED, i), exclusion: s.text(c::EXCLUSION, i),
            season: s.int(c::SEASON, i), episode_start: s.int(c::START, i), episode_end: s.int(c::END, i),
            part: s.int(c::PART, i), voice_group: s.text(c::VOICE_GROUP, i).map(Into::into),
            voice_label: s.str(c::VOICE_LABEL, i).into(), voice_name: s.str(c::VOICE_NAME, i).into(),
            voice_unknown: s.int(c::VOICE_UNKNOWN, i), player: s.str(c::PLAYER, i), player_label: s.str(c::PLAYER_LABEL, i),
            group: s.str(c::GROUP, i), text: s.str(c::TEXT, i), heights: s.ints(c::HEIGHTS, i), state: s.str(c::STATE, i),
            published: s.text(c::PUBLISHED, i), link: s.str(c::LINK, i), video: s.str(c::VIDEO, i),
            timeline: s.str(c::TIMELINE, i), track: s.text(c::TRACK, i).unwrap_or("sound"), language: s.str(c::LANGUAGE, i),
            subtitle_type: s.str(c::SUBTITLE_TYPE, i), stale: s.flag(c::STALE, i), in_output: false,
        };
        row.in_output = row.output();
        row
    }

    pub fn rows(&self) -> impl Iterator<Item = Row<'_>> {
        (0..self.len()).map(|i| self.row(i))
    }

    /// The JSON of the rows as the model writes it (the rows the constructor left out only when asked).
    pub fn json(&self, include_excluded: bool) -> Vec<u8> {
        let mut out = Vec::with_capacity(self.store.bytes.len() * 2);
        out.push(b'[');
        let mut first = true;
        for row in self.rows() {
            if !include_excluded && row.excluded {
                continue;
            }
            if !first {
                out.push(b',');
            }
            first = false;
            serde_json::to_writer(&mut out, &row).unwrap();
        }
        out.push(b']');
        out
    }

    fn numbers(&self, i: usize) -> (bool, Option<i64>, Option<i64>, Option<i64>) {
        let s = &self.store;
        (s.flag(c::EXCLUDED, i), s.int(c::SEASON, i), s.int(c::START, i), s.int(c::END, i))
    }

    /// The row is one of the output (`Row::output`), read of its columns alone.
    fn output(&self, i: usize) -> bool {
        let (excluded, season, start, _) = self.numbers(i);
        !excluded && season.is_some() && start.is_some()
            && (self.store.text(c::VOICE_GROUP, i).is_some() || self.store.text(c::TRACK, i) == Some("subtitles"))
    }

    /// The (season, episode) pairs the layout covers; a compilation covers every episode of its range.
    pub fn episodes(&self) -> HashSet<(i64, i64)> {
        (0..self.len()).flat_map(|i| {
            let (excluded, season, start, end) = self.numbers(i);
            covered(excluded, season, start, end)
        }).collect()
    }

    fn by_key(&self) -> HashMap<(i64, &str), usize> {
        (0..self.len()).map(|i| ((self.store.int(c::SOURCE_ID, i).unwrap_or(0), self.store.str(c::ROW_KEY, i)), i)).collect()
    }

    /// The episodes whose rows are not the same in the two layouts; None when the difference cannot be told as episodes
    /// (a numbered row without a season changed). An excluded row covers no episode.
    pub fn touched(&self, before: &Layout) -> Option<HashSet<(i64, i64)>> {
        let (old, new) = (before.by_key(), self.by_key());
        let mut changed: Vec<Row> = Vec::new();
        for (key, i) in &old {
            if new.get(key).map_or(true, |j| !same(&self.store, *j, &before.store, *i)) {
                changed.push(before.row(*i));
            }
        }
        for (key, j) in &new {
            if old.get(key).map_or(true, |i| !same(&before.store, *i, &self.store, *j)) {
                changed.push(self.row(*j));
            }
        }
        let mut touched = HashSet::new();
        for row in changed {
            let covered = row.covered();
            if covered.is_empty() && !row.excluded {
                return None;
            }
            touched.extend(covered);
        }
        Some(touched)
    }

    /// {label: label} of the voice-overs that kept their names and changed their group (`project.group_renames`), in the
    /// order they are first met.
    pub fn renames(&self, before: &Layout) -> Vec<(String, String)> {
        let was = before.by_key();
        let (now, old) = (&self.store, &before.store);
        let standing: HashSet<&str> = (0..self.len()).map(|i| now.str(c::VOICE_LABEL, i)).collect();
        let left = |e: Option<&str>| e.map_or(false, |e| LEFT_BEFORE_RULES.contains(&e));
        let mut became: Vec<(&str, Vec<&str>)> = Vec::new();
        for i in 0..self.len() {
            let Some(&j) = was.get(&(now.int(c::SOURCE_ID, i).unwrap_or(0), now.str(c::ROW_KEY, i))) else { continue };
            if left(now.text(c::EXCLUSION, i)) || left(old.text(c::EXCLUSION, j)) {
                continue; // a row left out before the constructor names no group: a player switched off renames nothing
            }
            let (label, was_label) = (now.str(c::VOICE_LABEL, i), old.str(c::VOICE_LABEL, j));
            if was_label == label || old.str(c::VOICE_NAME, j) != now.str(c::VOICE_NAME, i) {
                continue;
            }
            let at = match became.iter().position(|(k, _)| *k == was_label) {
                Some(at) => at,
                None => {
                    became.push((was_label, Vec::new()));
                    became.len() - 1
                }
            };
            if !became[at].1.contains(&label) {
                became[at].1.push(label);
            }
        }
        became.into_iter().filter(|(old, new)| new.len() == 1 && !standing.contains(old))
            .map(|(old, new)| (old.to_string(), new[0].to_string())).collect()
    }

    /// season -> the labels of the rows of the output, in the order the layout gives them.
    pub fn season_labels(&self) -> Vec<(i64, Vec<String>)> {
        let mut out: Vec<(i64, Vec<String>)> = Vec::new();
        for i in (0..self.len()).filter(|i| self.output(*i)) {
            let (season, label) = (self.store.int(c::SEASON, i).unwrap(), self.store.str(c::VOICE_LABEL, i));
            let at = match out.iter().position(|(s, _)| *s == season) {
                Some(at) => at,
                None => {
                    out.push((season, Vec::new()));
                    out.len() - 1
                }
            };
            if !out[at].1.iter().any(|l| l == label) {
                out[at].1.push(label.to_string());
            }
        }
        out
    }

    /// (season, label) -> the episodes the rows of the output of that voice-over cover.
    pub fn season_episodes(&self) -> HashMap<(i64, String), HashSet<i64>> {
        let mut out: HashMap<(i64, String), HashSet<i64>> = HashMap::new();
        for i in (0..self.len()).filter(|i| self.output(*i)) {
            let (excluded, season, start, end) = self.numbers(i);
            out.entry((season.unwrap(), self.store.str(c::VOICE_LABEL, i).to_string())).or_default()
                .extend(covered(excluded, season, start, end).into_iter().map(|(_, n)| n));
        }
        out
    }

    /// One reference per key over the rows that play, are grouped and are compilations, its video the highest row (the
    /// earlier on a tie): (key, source id, row key, player, group, height, start, end), in the order keys are first met.
    pub fn compilations(&self) -> Vec<(String, i64, String, String, String, Option<i64>, i64, i64)> {
        let mut best: Vec<(String, usize, i64)> = Vec::new();
        let mut at: HashMap<String, usize> = HashMap::new();
        let s = &self.store;
        for i in 0..self.len() {
            let (excluded, season, start, end) = self.numbers(i);
            let (Some(season), Some(start), Some(end)) = (season, start, end) else { continue };
            if excluded || end <= start || s.str(c::STATE, i) != "playable" || s.text(c::VOICE_GROUP, i).is_none() {
                continue;
            }
            let key = compilation_key(s.str(c::VOICE_LABEL, i), s.int(c::SOURCE_ID, i).unwrap_or(0), s.str(c::PLAYER, i),
                                      season, start, end);
            let height = s.ints(c::HEIGHTS, i).into_iter().max().unwrap_or(0);
            match at.get(&key) {
                Some(&k) => {
                    if height > best[k].2 {
                        best[k] = (key, i, height);
                    }
                }
                None => {
                    at.insert(key.clone(), best.len());
                    best.push((key, i, height));
                }
            }
        }
        best.into_iter().map(|(key, i, _)| {
            let row = self.row(i);
            (key, row.source_id, row.row_key.to_string(), row.player.to_string(), row.voice_label.to_string(),
             row.heights.iter().copied().max(), row.episode_start.unwrap(), row.episode_end.unwrap())
        }).collect()
    }

    /// (season, episode) -> the best height of the single videos that play (at least 1).
    pub fn singles(&self) -> HashMap<(i64, i64), i64> {
        let mut out = HashMap::new();
        for i in 0..self.len() {
            let (excluded, season, start, end) = self.numbers(i);
            let (Some(season), Some(start)) = (season, start) else { continue };
            let compilation = end.map_or(false, |e| e > start);
            if excluded || compilation || self.store.str(c::STATE, i) != "playable" {
                continue;
            }
            let height = self.store.ints(c::HEIGHTS, i).into_iter().max().unwrap_or(0);
            let slot = out.entry((season, start)).or_insert(0);
            *slot = (*slot).max(height).max(1);
        }
        out
    }
}

/// Whether a row of one layout is the row of another, field by field (the fields of the model; `in_output` follows them).
fn same(a: &Store, i: usize, b: &Store, j: usize) -> bool {
    use c::*;
    [SERIES_ID, SOURCE_ID, SEASON, START, END, PART, VOICE_UNKNOWN].iter().all(|k| a.int(*k, i) == b.int(*k, j))
        && [EXCLUDED, STALE].iter().all(|k| a.flag(*k, i) == b.flag(*k, j))
        && [ROW_KEY, EXCLUSION, VOICE_GROUP, VOICE_LABEL, VOICE_NAME, PLAYER, PLAYER_LABEL, GROUP, TEXT, STATE, PUBLISHED, LINK,
            VIDEO, TIMELINE, LANGUAGE, SUBTITLE_TYPE].iter().all(|k| a.text(*k, i) == b.text(*k, j))
        && a.text(TRACK, i).unwrap_or("sound") == b.text(TRACK, j).unwrap_or("sound")
        && a.ints(HEIGHTS, i) == b.ints(HEIGHTS, j)
}

/// The key of a compilation (`core.contracts.compilation.compilation_key`): a JSON array behind its prefix.
pub fn compilation_key(group: &str, source_id: i64, player: &str, season: i64, start: i64, end: i64) -> String {
    let parts = serde_json::json!([group, source_id, player, season, start, end]);
    format!("compilation:{parts}")
}

/// The rows of a layout from the JSON of their models (the layouts stored before they were kept by columns).
pub fn encode_json(json: &[u8]) -> Result<Vec<u8>, String> {
    #[derive(serde::Deserialize)]
    struct J {
        series_id: i64,
        source_id: i64,
        row_key: String,
        #[serde(default)]
        excluded: bool,
        #[serde(default)]
        exclusion: Option<String>,
        #[serde(default)]
        season: Option<i64>,
        #[serde(default)]
        episode_start: Option<i64>,
        #[serde(default)]
        episode_end: Option<i64>,
        #[serde(default)]
        part: Option<i64>,
        #[serde(default)]
        voice_group: Option<String>,
        #[serde(default)]
        voice_label: String,
        #[serde(default)]
        voice_name: String,
        #[serde(default)]
        voice_unknown: Option<i64>,
        player: String,
        #[serde(default)]
        player_label: String,
        #[serde(default)]
        group: String,
        #[serde(default)]
        text: String,
        #[serde(default)]
        heights: Vec<i64>,
        state: String,
        #[serde(default)]
        published: Option<String>,
        #[serde(default)]
        link: String,
        #[serde(default)]
        video: String,
        #[serde(default)]
        timeline: String,
        #[serde(default = "sound")]
        track: String,
        #[serde(default)]
        language: String,
        #[serde(default)]
        subtitle_type: String,
        #[serde(default)]
        stale: bool,
    }
    fn sound() -> String {
        "sound".into()
    }
    let given: Vec<J> = serde_json::from_slice(json).map_err(|e| e.to_string())?;
    let rows: Vec<Row> = given.iter().map(|r| Row {
        series_id: r.series_id, source_id: r.source_id, row_key: &r.row_key, excluded: r.excluded,
        exclusion: r.exclusion.as_deref(), season: r.season, episode_start: r.episode_start, episode_end: r.episode_end,
        part: r.part, voice_group: r.voice_group.as_deref().map(Into::into), voice_label: r.voice_label.as_str().into(),
        voice_name: r.voice_name.as_str().into(), voice_unknown: r.voice_unknown, player: &r.player,
        player_label: &r.player_label, group: &r.group, text: &r.text, heights: r.heights.clone(), state: &r.state,
        published: r.published.as_deref(), link: &r.link, video: &r.video, timeline: &r.timeline, track: &r.track,
        language: &r.language, subtitle_type: &r.subtitle_type, stale: r.stale, in_output: false,
    }).collect();
    Ok(encode(&rows.iter().collect::<Vec<_>>()))
}
