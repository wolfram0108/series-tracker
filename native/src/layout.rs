//! The layout of a series as a pure function of what its sources gave and what the constructor answered
//! (modules/composition/project.py): the rows of all sources in the order of the sources, the summary of every source and
//! the numbering of unknown voice-overs. Every number of a row is the constructor's answer to the line of that row.

use serde::Serialize;
use std::collections::{HashMap, HashSet};

use crate::cols::{self, Column, View};
use crate::rows::{self, Row as LayoutRow};
use crate::rules::Answer;
use crate::table::{Row, Table};
use crate::text::{fold_name, py_strip};

const UNKNOWN_VOICE_LABEL: &str = "Неизвестная озвучка ";
// The only player whose list holds several seasons of a catalog, one of which the user may pick for the source.
const CATALOG_PLAYER: &str = "vibix";

pub struct Source {
    pub id: i64,
    pub series_id: i64,
    pub position: i64,
    pub url: String,
    pub profile: Option<i64>,
    pub vibix_season: Option<i64>,
    pub players_off: Vec<String>,
}

/// A source of the series with what the other modules told about it: `answers` is None while it has no profile (or no
/// table); `failure` is a reason when the page could not be read, and then the table is the last complete one.
pub struct Input<'a> {
    pub source: Source,
    pub table: Option<Table<'a>>,
    pub answers: Option<Answers<'a>>,
    pub failure: Option<String>,
    pub table_url: Option<String>,
    // The players switched off in the settings for every source.
    pub everywhere: HashSet<String>,
}

/// The name a player takes in the order of the settings: every release of a tracker's page is one place.
fn order_name(player: &str) -> &str {
    if player.starts_with("torrent:") { "torrent" } else { player }
}

/// The rows the choices of the user leave out of play, by key, with whose choice it is (`project.left_out`).
pub fn left_out<'a>(source: &Source, rows: &[Row<'a>], everywhere: &HashSet<String>) -> HashMap<&'a str, &'static str> {
    let off: HashSet<&str> = source.players_off.iter().map(String::as_str).collect();
    let mut groups: Vec<&str> = Vec::new();
    for row in rows {
        if row.player == CATALOG_PLAYER && !groups.contains(&row.group) {
            groups.push(row.group);
        }
    }
    let position = source.vibix_season.filter(|s| *s > 0).map(|s| s - 1);
    let chosen = position.and_then(|p| groups.get(p as usize).copied());
    let mut out = HashMap::new();
    for row in rows {
        if everywhere.contains(order_name(row.player)) {
            out.insert(row.key, "settings");
        } else if off.contains(row.player)
            || (row.player == CATALOG_PLAYER && position.is_some() && chosen.map_or(true, |c| row.group != c)) {
            out.insert(row.key, "source");
        }
    }
    out
}

/// The lines the constructor is asked about a source: one for every row in play, with its key, the player and the
/// voice-over of the row — as columns (key, text, player, voice).
pub fn lines(source: &Source, table: &Table, everywhere: &HashSet<String>) -> Vec<u8> {
    let rows = table.rows();
    let out = left_out(source, &rows, everywhere);
    let kept: Vec<&Row> = rows.iter().filter(|r| !out.contains_key(r.key)).collect();
    let columns = vec![
        ("key", Column::Strs(kept.iter().map(|r| r.key).collect())),
        ("text", Column::Text(kept.iter().map(|r| table.line_of(r)).collect())),
        ("player", Column::Strs(kept.iter().map(|r| r.player).collect())),
        ("voice", Column::Strs(kept.iter().map(|r| r.voice_name).collect())),
    ];
    cols::encode(kept.len(), &columns)
}

/// The answers of the constructor to the lines of a source, read in place, by key: the columns are found once.
pub struct Answers<'a> {
    at: HashMap<&'a str, usize>,
    excluded: cols::Flags<'a>,
    season: cols::Int<'a>,
    episode: cols::Int<'a>,
    start: cols::Int<'a>,
    end: cols::Int<'a>,
    part: cols::Int<'a>,
    voiceover: cols::Texts<'a>,
}

impl<'a> Answers<'a> {
    pub fn of(bytes: &'a [u8]) -> Result<Answers<'a>, String> {
        let bytes: &'a [u8] = if bytes.is_empty() { EMPTY.get_or_init(|| cols::encode(0, &[])) } else { bytes };
        let view = View::of(bytes)?;
        let keys = view.text("key");
        let at = (0..view.rows).filter_map(|i| keys.get(i).map(|k| (k, i))).collect();
        Ok(Answers {
            at, excluded: view.flag("excluded"), season: view.int("season"), episode: view.int("episode"),
            start: view.int("start"), end: view.int("end"), part: view.int("part"), voiceover: view.text("voiceover"),
        })
    }

    /// The voice-over the constructor gave the line of a key (none for no answer).
    pub fn voiceover(&self, key: &str) -> Option<&'a str> {
        self.at.get(key).and_then(|&i| self.voiceover.get(i))
    }

    /// The numbers of the answer for a key (its voice-over is `voiceover`); none for a line the constructor gave none.
    pub fn get(&self, key: &str) -> Answer {
        let Some(&i) = self.at.get(key) else { return Answer::default() };
        Answer {
            excluded: self.excluded.get(i), season: self.season.get(i), episode: self.episode.get(i),
            start: self.start.get(i), end: self.end.get(i), part: self.part.get(i), ..Default::default()
        }
    }
}

static EMPTY: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();

fn ordered<'a, 'b>(inputs: &'b [Input<'a>]) -> Vec<&'b Input<'a>> {
    let mut out: Vec<&Input> = inputs.iter().collect();
    out.sort_by_key(|i| (i.source.position, i.source.id));
    out
}

fn answered(item: &Input) -> bool {
    item.table.is_some() && item.answers.is_some() && item.source.profile.is_some()
}

/// The distinct voice-over names of the rows the constructors answered: the names the groups are asked about.
pub fn voice_names(inputs: &[Input]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    let mut seen: HashSet<&str> = HashSet::new();
    for item in ordered(inputs) {
        if !answered(item) {
            continue;
        }
        let (table, answers) = (item.table.as_ref().unwrap(), item.answers.as_ref().unwrap());
        let rows = table.rows();
        let out = left_out(&item.source, &rows, &item.everywhere);
        for row in rows.iter().filter(|r| !out.contains_key(r.key) && r.track == "sound") {
            let name = answers.voiceover(row.key).map(py_strip).unwrap_or("");
            if !name.is_empty() && seen.insert(name) {
                names.push(name.to_string());
            }
        }
    }
    names
}

#[derive(Serialize)]
struct PlayerSummary {
    player: String,
    player_label: String,
    rows: usize,
    groups: usize,
    group_names: Vec<String>,
}

#[derive(Serialize)]
struct Summary<'a> {
    source_id: i64,
    url: &'a str,
    canonical_url: Option<&'a str>,
    status: &'static str,
    reason: Option<&'a str>,
    read_at: Option<&'a str>,
    players: Vec<PlayerSummary>,
    place: Vec<&'a str>,
    title: &'a str,
}

fn summary(item: &Input, rows: &[Row]) -> String {
    let table = item.table.as_ref();
    let failed = item.failure.as_deref().map_or(false, |f| !f.is_empty());
    let status = if failed { "failed" } else if table.is_none() { "waiting" }
        else if item.source.profile.is_none() { "no_profile" }
        else if item.answers.is_some() { "ready" } else { "waiting" };
    let mut players: Vec<PlayerSummary> = Vec::new();
    let mut place = Vec::new();
    if let Some(table) = table {
        let mut at: HashMap<&str, usize> = HashMap::new();
        let mut groups: Vec<HashSet<&str>> = Vec::new();
        for row in rows {
            let index = *at.entry(row.player).or_insert_with(|| {
                players.push(PlayerSummary { player: row.player.to_string(), player_label: String::new(), rows: 0,
                                             groups: 0, group_names: vec![] });
                groups.push(HashSet::new());
                players.len() - 1
            });
            let p = &mut players[index];
            if p.player_label.is_empty() && !row.player_label.is_empty() {
                p.player_label = row.player_label.to_string();
            }
            p.rows += 1;
            if !row.group.is_empty() && groups[index].insert(row.group) {
                p.group_names.push(row.group.to_string());
            }
        }
        for (p, g) in players.iter_mut().zip(&groups) {
            p.groups = g.len();
        }
        for part in [table.pid.as_str(), table.head.channel.as_str()] {
            if !part.is_empty() {
                place.push(part);
            }
        }
    }
    serde_json::to_string(&Summary {
        source_id: item.source.id, url: &item.source.url,
        canonical_url: table.map(|t| t.head.url.as_str()).or(item.table_url.as_deref()), status,
        reason: item.failure.as_deref().filter(|f| !f.is_empty()), read_at: table.and_then(|t| t.head.read_at.as_deref()),
        players, place, title: table.map_or("", |t| t.head.title.as_str()),
    }).unwrap()
}

pub struct Made {
    pub rows: Vec<u8>,
    pub subtitles: Vec<u8>,
    pub summaries: Vec<String>,
    pub numbering: Vec<(String, i64)>,
}

/// The rows of one source, made apart from the others: an unknown voice-over is named by its folded name, numbered
/// after all sources are made (the numbers go in the order of the sources and of their rows).
struct Part<'a> {
    summary: String,
    rows: Vec<LayoutRow<'a>>,
    // The folded name of the unknown voice-over of each row (by its place in `rows`) that awaits its number.
    unknown: Vec<(usize, String)>,
}

fn part<'a>(item: &Input<'a>, groups: &'a HashMap<String, Option<String>>) -> Part<'a> {
    // The rows of the table are read once: for the summary and for the layout.
    let rows = item.table.as_ref().map(Table::rows).unwrap_or_default();
    let summary = summary(item, &rows);
    let mut made: Vec<LayoutRow<'a>> = Vec::new();
    let mut unknown = Vec::new();
    if !answered(item) {
        return Part { summary, rows: made, unknown };
    }
    let (table, answers) = (item.table.as_ref().unwrap(), item.answers.as_ref().unwrap());
    let unread: HashSet<&str> = table.head.unread.iter().map(String::as_str).collect();
    let stale = |row: &Row| !(item.failure.is_none() && !unread.contains(row.part));
    let out = left_out(&item.source, &rows, &item.everywhere);
    let mut folded: HashMap<&str, String> = HashMap::new();
    for row in rows.iter().filter(|r| !out.contains_key(r.key)) {
        let answer = answers.get(row.key);
        // The voice-over of a row is the tag the constructor gave it, and nothing else.
        let name: &'a str = answers.voiceover(row.key).map(py_strip).unwrap_or("");
        let (group, label): (Option<std::borrow::Cow<'a, str>>, std::borrow::Cow<'a, str>) = if row.track == "subtitles" {
            // Subtitles are no voice-over: no group takes them and no unknown number counts them.
            (None, if name.is_empty() { row.voice_name.into() } else { name.into() })
        } else {
            let group = if name.is_empty() { None }
                else { groups.get(name).and_then(|g| g.as_deref()).filter(|g| !g.is_empty()) };
            match group {
                Some(g) => (Some(g.into()), g.into()),
                None => {
                    // A row with no tag is numbered by what its table told, so that two unknown voice-overs stay apart.
                    let told = if name.is_empty() { row.voice_name } else { name };
                    unknown.push((made.len(), folded.entry(told).or_insert_with(|| fold_name(told)).clone()));
                    (None, "".into())
                }
            }
        };
        let (excluded, exclusion, season, start, end, part) = if answer.excluded {
            (true, Some("rule"), None, None, None, None)
        } else if let Some(e) = answer.episode {
            (false, None, answer.season, Some(e), Some(e), answer.part)
        } else if let Some(s) = answer.start {
            (false, None, answer.season, Some(s), Some(answer.end.unwrap_or(s)), answer.part)
        } else {
            (true, Some("no_number"), answer.season, None, None, answer.part)
        };
        let mut layout_row = LayoutRow {
            series_id: item.source.series_id, source_id: item.source.id, row_key: row.key, excluded, exclusion, season,
            episode_start: start, episode_end: end, part, voice_group: group, voice_label: label, voice_name: name.into(),
            voice_unknown: None, player: row.player, player_label: row.player_label, group: row.group, text: row.text,
            heights: row.heights.clone(), state: row.state, published: row.published, link: row.link, video: row.video,
            timeline: row.timeline, track: row.track, language: row.language, subtitle_type: row.subtitle_type,
            stale: stale(row), in_output: false,
        };
        layout_row.in_output = layout_row.output();
        made.push(layout_row);
    }
    // The rows the choices of the user leave out: the layout says the video is there and is not wanted.
    for row in rows.iter().filter(|r| out.contains_key(r.key)) {
        made.push(LayoutRow {
            series_id: item.source.series_id, source_id: item.source.id, row_key: row.key, excluded: true,
            exclusion: Some(out[row.key]), season: None, episode_start: None, episode_end: None, part: None,
            voice_group: None, voice_label: "".into(), voice_name: "".into(), voice_unknown: None, player: row.player,
            player_label: row.player_label, group: row.group, text: row.text, heights: row.heights.clone(),
            state: row.state, published: row.published, link: row.link, video: row.video, timeline: row.timeline,
            track: row.track, language: row.language, subtitle_type: row.subtitle_type, stale: stale(row), in_output: false,
        });
    }
    Part { summary, rows: made, unknown }
}

/// Rows of all sources in source order, a summary of every source, and the numbering of unknown voice-overs (it only
/// grows). `groups` maps a voice-over name to the user group it belongs to, None for a name in no group. The sources
/// are made side by side; the unknown voice-overs are numbered after, in the order of the sources and of their rows.
pub fn project<'a>(inputs: &[Input<'a>], groups: &'a HashMap<String, Option<String>>, numbering: Vec<(String, i64)>)
    -> Made {
    let ordered = ordered(inputs);
    let mut parts: Vec<Part<'a>> = std::thread::scope(|scope| {
        let made: Vec<_> = ordered.iter().map(|item| scope.spawn(move || part(item, groups))).collect();
        made.into_iter().map(|each| each.join().expect("a part of the layout")).collect()
    });
    let mut numbering = numbering;
    let mut number_of: HashMap<String, i64> = numbering.iter().cloned().collect();
    let mut highest = numbering.iter().map(|(_, n)| *n).max().unwrap_or(0);
    // The label of an unknown voice-over, made once for its number.
    let mut labels: HashMap<i64, String> = HashMap::new();
    for each in &mut parts {
        for (at, key) in std::mem::take(&mut each.unknown) {
            let number = match number_of.get(&key) {
                Some(n) => *n,
                None => {
                    highest += 1;
                    numbering.push((key.clone(), highest));
                    number_of.insert(key, highest);
                    highest
                }
            };
            let label = labels.entry(number).or_insert_with(|| format!("{UNKNOWN_VOICE_LABEL}{number}"));
            let row = &mut each.rows[at];
            row.voice_label = label.clone().into();
            row.voice_unknown = Some(number);
        }
    }
    let summaries = parts.iter().map(|each| each.summary.clone()).collect();
    let all: Vec<&LayoutRow> = parts.iter().flat_map(|each| each.rows.iter()).collect();
    let sound: Vec<&LayoutRow> = all.iter().copied().filter(|r| r.track != "subtitles").collect();
    let subtitles: Vec<&LayoutRow> = all.iter().copied().filter(|r| r.track == "subtitles").collect();
    let (rows, subtitles) = std::thread::scope(|scope| {
        let made = scope.spawn(|| rows::encode(&subtitles));
        (rows::encode(&sound), made.join().expect("the subtitles of the layout"))
    });
    Made { rows, subtitles, summaries, numbering }
}
