//! The rows of the table of a page (core/contracts/release_table.py, TableRow) kept by columns: written once after a read
//! of the page, read in place by the count; and the line the constructor gets for a row (`line_of`).

use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;

use crate::cols::{self, Column, Store};
use crate::text::{py_strip, site_of};

// The columns of a row of a table, in the order of the fields of TableRow.
pub const COLUMNS: &[&str] = &[
    "key", "player", "player_label", "group", "season", "episode", "text", "voice.name", "voice.kind", "heights", "state",
    "reason", "duration_s", "published", "link", "video", "timeline", "track", "language", "subtitle_type", "site_fields",
    "part",
];

pub mod c {
    pub const KEY: usize = 0;
    pub const PLAYER: usize = 1;
    pub const PLAYER_LABEL: usize = 2;
    pub const GROUP: usize = 3;
    pub const SEASON: usize = 4;
    pub const EPISODE: usize = 5;
    pub const TEXT: usize = 6;
    pub const VOICE_NAME: usize = 7;
    pub const VOICE_KIND: usize = 8;
    pub const HEIGHTS: usize = 9;
    pub const STATE: usize = 10;
    pub const REASON: usize = 11;
    pub const DURATION: usize = 12;
    pub const PUBLISHED: usize = 13;
    pub const LINK: usize = 14;
    pub const VIDEO: usize = 15;
    pub const TIMELINE: usize = 16;
    pub const TRACK: usize = 17;
    pub const LANGUAGE: usize = 18;
    pub const SUBTITLE_TYPE: usize = 19;
    pub const SITE_FIELDS: usize = 20;
    pub const PART: usize = 21;
}

#[derive(Deserialize, Serialize, Default)]
#[serde(default)]
struct Voice<'a> {
    #[serde(borrow)]
    name: std::borrow::Cow<'a, str>,
    #[serde(borrow)]
    kind: std::borrow::Cow<'a, str>,
}

/// A row as the JSON of its model writes it.
#[derive(Deserialize, Serialize)]
struct Json<'a> {
    #[serde(borrow)]
    key: std::borrow::Cow<'a, str>,
    #[serde(borrow)]
    player: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    player_label: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    group: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    season: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    episode: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    text: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    voice: Voice<'a>,
    #[serde(default)]
    heights: Vec<i64>,
    #[serde(borrow)]
    state: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    reason: Option<std::borrow::Cow<'a, str>>,
    #[serde(default)]
    duration_s: Option<f64>,
    #[serde(default, borrow)]
    published: Option<std::borrow::Cow<'a, str>>,
    #[serde(default, borrow)]
    link: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    video: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    timeline: std::borrow::Cow<'a, str>,
    #[serde(default = "sound", borrow)]
    track: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    language: std::borrow::Cow<'a, str>,
    #[serde(default, borrow)]
    subtitle_type: std::borrow::Cow<'a, str>,
    #[serde(default = "no_fields", borrow)]
    site_fields: &'a RawValue,
    #[serde(default, borrow)]
    part: std::borrow::Cow<'a, str>,
}

fn sound() -> std::borrow::Cow<'static, str> {
    "sound".into()
}

fn no_fields() -> &'static RawValue {
    static EMPTY: std::sync::OnceLock<Box<RawValue>> = std::sync::OnceLock::new();
    EMPTY.get_or_init(|| RawValue::from_string("{}".into()).unwrap())
}

/// The rows of a table, from the JSON of their models, as columns.
pub fn encode_json(json: &[u8]) -> Result<Vec<u8>, String> {
    let rows: Vec<Json> = serde_json::from_slice(json).map_err(|e| e.to_string())?;
    let text = |f: &dyn Fn(&Json) -> String| Column::Text(rows.iter().map(f).collect());
    let maybe = |f: &dyn Fn(&Json) -> Option<String>| Column::MaybeText(rows.iter().map(f).collect());
    let columns = vec![
        ("key", text(&|r| r.key.to_string())), ("player", text(&|r| r.player.to_string())),
        ("player_label", text(&|r| r.player_label.to_string())), ("group", text(&|r| r.group.to_string())),
        ("season", text(&|r| r.season.to_string())), ("episode", text(&|r| r.episode.to_string())),
        ("text", text(&|r| r.text.to_string())), ("voice.name", text(&|r| r.voice.name.to_string())),
        ("voice.kind", text(&|r| r.voice.kind.to_string())),
        ("heights", Column::Ints(rows.iter().map(|r| r.heights.clone()).collect())),
        ("state", text(&|r| r.state.to_string())), ("reason", maybe(&|r| r.reason.as_ref().map(|s| s.to_string()))),
        ("duration_s", Column::Float(rows.iter().map(|r| r.duration_s).collect())),
        ("published", maybe(&|r| r.published.as_ref().map(|s| s.to_string()))), ("link", text(&|r| r.link.to_string())),
        ("video", text(&|r| r.video.to_string())), ("timeline", text(&|r| r.timeline.to_string())),
        ("track", text(&|r| r.track.to_string())), ("language", text(&|r| r.language.to_string())),
        ("subtitle_type", text(&|r| r.subtitle_type.to_string())),
        ("site_fields", text(&|r| r.site_fields.get().to_string())), ("part", text(&|r| r.part.to_string())),
    ];
    Ok(cols::encode(rows.len(), &columns))
}

/// The rows of a table as the JSON of their models (TableRow), for a reader that makes models of them.
pub fn to_json(store: &Store) -> Vec<u8> {
    let mut out = Vec::with_capacity(store.bytes.len() * 2);
    out.push(b'[');
    for i in 0..store.rows {
        if i > 0 {
            out.push(b',');
        }
        let t = |column| std::borrow::Cow::Borrowed(store.str(column, i));
        let maybe = |column| store.text(column, i).map(std::borrow::Cow::Borrowed);
        let fields = store.str(c::SITE_FIELDS, i);
        let row = Json {
            key: t(c::KEY), player: t(c::PLAYER), player_label: t(c::PLAYER_LABEL), group: t(c::GROUP), season: t(c::SEASON),
            episode: t(c::EPISODE), text: t(c::TEXT), voice: Voice { name: t(c::VOICE_NAME), kind: t(c::VOICE_KIND) },
            heights: store.ints(c::HEIGHTS, i), state: t(c::STATE), reason: maybe(c::REASON),
            duration_s: float(store, c::DURATION, i), published: maybe(c::PUBLISHED), link: t(c::LINK), video: t(c::VIDEO),
            timeline: t(c::TIMELINE), track: store.text(c::TRACK, i).map_or("sound".into(), std::borrow::Cow::Borrowed),
            language: t(c::LANGUAGE), subtitle_type: t(c::SUBTITLE_TYPE),
            site_fields: serde_json::from_str(if fields.is_empty() { "{}" } else { fields }).unwrap_or_else(|_| no_fields()),
            part: t(c::PART),
        };
        serde_json::to_writer(&mut out, &row).unwrap();
    }
    out.push(b']');
    out
}

fn float(store: &Store, column: usize, row: usize) -> Option<f64> {
    store.float(column, row)
}

/// The head of a table: what the line and the summary of a source read besides the rows.
pub struct Head {
    pub url: String,
    pub names: Vec<String>,
    pub title: String,
    pub channel: String,
    pub read_at: Option<String>,
    // The parts of the page its last read did not get.
    pub unread: Vec<String>,
}

/// A table of a page: its head and its rows read in place.
pub struct Table<'a> {
    pub head: &'a Head,
    pub rows: cols::View<'a>,
    // `PID` and the name of the release as every line gives them, made once for the table.
    pub pid: String,
    pub name: String,
}

/// One row of a table, its fields slices of the field of the table.
pub struct Row<'a> {
    pub key: &'a str,
    pub player: &'a str,
    pub player_label: &'a str,
    pub group: &'a str,
    pub season: &'a str,
    pub episode: &'a str,
    pub text: &'a str,
    pub voice_name: &'a str,
    pub voice_kind: &'a str,
    pub heights: Vec<i64>,
    pub state: &'a str,
    pub published: Option<&'a str>,
    pub link: &'a str,
    pub video: &'a str,
    pub timeline: &'a str,
    pub track: &'a str,
    pub language: &'a str,
    pub subtitle_type: &'a str,
    pub part: &'a str,
}

impl<'a> Table<'a> {
    pub fn of(head: &'a Head, bytes: &'a [u8]) -> Result<Table<'a>, String> {
        Ok(Table {
            head, rows: cols::View::of(bytes)?, pid: site_of(&head.url),
            name: head.names.first().map(|n| py_strip(n).to_string()).unwrap_or_default(),
        })
    }

    /// The rows, in the order of the table.
    pub fn rows(&self) -> Vec<Row<'a>> {
        let v = &self.rows;
        let t = |name: &str| v.text(name);
        let (key, player, label, group, season, episode, text) = (t("key"), t("player"), t("player_label"), t("group"),
            t("season"), t("episode"), t("text"));
        let (vname, vkind, state, published, link, video) = (t("voice.name"), t("voice.kind"), t("state"), t("published"),
            t("link"), t("video"));
        let (timeline, track, language, subtitle_type, part) = (t("timeline"), t("track"), t("language"),
            t("subtitle_type"), t("part"));
        let heights = v.ints("heights");
        let s = |x: Option<&'a str>| x.unwrap_or("");
        (0..v.rows).map(|i| Row {
            key: s(key.get(i)), player: s(player.get(i)), player_label: s(label.get(i)), group: s(group.get(i)),
            season: s(season.get(i)), episode: s(episode.get(i)), text: s(text.get(i)), voice_name: s(vname.get(i)),
            voice_kind: s(vkind.get(i)), heights: heights.get(i).collect(), state: s(state.get(i)),
            published: published.get(i), link: s(link.get(i)), video: s(video.get(i)), timeline: s(timeline.get(i)),
            track: track.get(i).unwrap_or("sound"), language: s(language.get(i)), subtitle_type: s(subtitle_type.get(i)),
            part: s(part.get(i)),
        }).collect()
    }

    /// The text the constructor gets for a row (`core.contracts.release_table.line_of`): the fields joined by « | », a
    /// field with a code as `CODE:value`, an empty field left out with its separator.
    pub fn line_of(&self, row: &Row) -> String {
        let player = if row.player_label.is_empty() { row.player } else { row.player_label };
        let height = if row.state == "playable" { row.heights.iter().copied().max().filter(|h| *h != 0) } else { None };
        let fields: [(&str, &str); 10] = [
            ("PID", &self.pid), ("", &self.head.channel), ("P", player), ("", &self.name), ("S", row.season),
            ("E", row.episode), ("", row.text), ("G", row.group), ("V", row.voice_name), ("VT", row.voice_kind),
        ];
        let mut out = String::with_capacity(192);
        for (code, text) in fields {
            if text.is_empty() {
                continue;
            }
            if !out.is_empty() {
                out.push_str(" | ");
            }
            if !code.is_empty() {
                out.push_str(code);
                out.push(':');
            }
            out.push_str(text);
        }
        if let Some(h) = height {
            use std::fmt::Write;
            if !out.is_empty() {
                out.push_str(" | ");
            }
            let _ = write!(out, "R:{h}p");
        }
        out
    }
}
