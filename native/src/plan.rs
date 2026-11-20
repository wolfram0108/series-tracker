//! The goals of the planner of a series (core/plan/planner.py, `_goals`): what the container of every episode must be, from
//! the rows of the output, the files of the library, the choices of the user and the attempts that failed. The same rule
//! as the planner of Python, step for step: every order, tie and first of equals is the one it has.

use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};

use crate::text::fold_name;

// --- the tables (core/plan/tables.py) ------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct RowId {
    pub source: i64,
    pub key: String,
    pub video: String,
}

impl RowId {
    pub fn json(&self) -> Value {
        json!([self.source, self.key, self.video])
    }

    /// `repr(RowId(...))` of Python: the last tie-break of the rule.
    pub fn repr(&self) -> String {
        format!("RowId(source_id={}, row_key={}, video={})", self.source, py_repr(&self.key), py_repr(&self.video))
    }
}

/// `repr(str)` of Python: single quotes unless the text holds one and no double quote; the escapes of `str.__repr__`.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if !printable(c) => {
                let code = c as u32;
                if code < 0x100 {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code < 0x10000 {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable()` of one character, as far as the texts of rows go: no control, no separator but the space, no
/// mark of format, no character of private use.
fn printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    if c.is_control() || c.is_whitespace() {
        return false;
    }
    let code = c as u32;
    !matches!(code, 0xAD | 0x600..=0x605 | 0x61C | 0x6DD | 0x70F | 0x180E | 0x200B..=0x200F | 0x202A..=0x202E
        | 0x2060..=0x2064 | 0x2066..=0x206F | 0xFEFF | 0xFFF9..=0xFFFB | 0xE000..=0xF8FF | 0xF0000..=0x10FFFF
        | 0xD800..=0xDFFF | 0x110BD | 0x1D173..=0x1D17A | 0xE0001 | 0xE0020..=0xE007F)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    pub source: String,
    pub player: String,
    pub group: Option<String>,
    pub height: i64,
    pub link: String,
    pub label: String,
}

impl Snapshot {
    pub fn variant_label(&self) -> &str {
        if !self.label.is_empty() { &self.label } else { self.group.as_deref().unwrap_or("") }
    }

    pub fn json(&self) -> Value {
        json!([self.source, self.player, self.group, self.height, self.link, self.label])
    }
}

pub type Slot = Vec<i64>;
pub type Variant = (String, i64, String);

#[derive(Clone, Debug)]
pub struct Online {
    pub id: RowId,
    pub season: i64,
    pub start: i64,
    pub end: i64,
    pub player: String,
    pub group: String,
    pub height: i64,
    pub source: String,
    pub link: String,
    pub label: String,
    pub position: i64,
    pub part: Option<i64>,
    pub playable: bool,
    pub current: bool,
    pub player_label: String,
    pub timeline_key: String,
    pub track: String,
    pub language: String,
    pub subtitle_type: String,
    // The timeline of the row's video and its variant, made once.
    pub timeline_id: RowId,
    pub variant_of: Variant,
}

impl Online {
    /// The row with what it stands for made once: its timeline and its variant.
    pub fn made(mut self) -> Online {
        self.timeline_id = if self.timeline_key.is_empty() { self.id.clone() }
            else { RowId { source: self.id.source, key: self.timeline_key.clone(), video: self.id.video.clone() } };
        let label = if !self.label.is_empty() { self.label.clone() } else { self.group.clone() };
        self.variant_of = (label, self.id.source, self.player.clone());
        self
    }

    pub fn timeline(&self) -> &RowId {
        &self.timeline_id
    }

    pub fn variant(&self) -> &Variant {
        &self.variant_of
    }

    pub fn compilation(&self) -> bool {
        self.end > self.start
    }

    pub fn slot(&self) -> Slot {
        match self.part {
            None => vec![self.season, self.start],
            Some(part) => vec![self.season, self.start, part],
        }
    }

    pub fn snapshot(&self) -> Snapshot {
        Snapshot { source: self.source.clone(), player: self.player.clone(), group: Some(self.group.clone()),
                   height: self.height, link: self.link.clone(), label: self.label.clone() }
    }
}

#[derive(Clone, Debug)]
pub struct Video {
    pub id: RowId,
    pub snapshot: Snapshot,
    pub episode: Slot,
    pub timeline: RowId,
    pub cut_of: Option<RowId>,
    pub timeline_snapshot: Option<Snapshot>,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Key {
    Row(RowId),
    Cut(RowId, RowId),
}

impl Key {
    fn json(&self) -> Value {
        match self {
            Key::Row(r) => json!(["row", {"row": r.json()}]),
            Key::Cut(a, b) => json!(["cut", {"row": a.json()}, {"row": b.json()}]),
        }
    }
}

impl Video {
    pub fn key(&self) -> Key {
        match &self.cut_of {
            Some(cut) => Key::Cut(cut.clone(), self.timeline.clone()),
            None => Key::Row(self.timeline.clone()),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Track {
    pub dub: RowId,
    pub snapshot: Snapshot,
    pub episode: Slot,
    pub aligned_to: RowId,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Subtitle {
    pub row: RowId,
    pub name: String,
    pub player: String,
    pub timeline: RowId,
    pub language: String,
    pub subtitle_type: String,
    pub default: bool,
    pub forced: bool,
}

impl Subtitle {
    fn variant(&self) -> Variant {
        (self.name.clone(), self.row.source, self.player.clone())
    }

    fn json(&self) -> Value {
        json!([self.row.json(), self.name, self.player, self.timeline.json(), self.language, self.subtitle_type,
               self.default, self.forced])
    }
}

#[derive(Clone, Debug)]
pub struct Container {
    pub name: String,
    pub video: Video,
    pub tracks: Vec<Track>,
    pub off: bool,
    pub titles: Vec<(String, String)>,
    pub languages: Vec<(String, String)>,
    pub subtitles: Vec<Subtitle>,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Failed {
    Download(RowId),
    Align(RowId, RowId),
    Cut(RowId, RowId),
}

/// The groups of variants the user declared equal on a level.
#[derive(Clone, Debug, Default)]
pub struct Same {
    pub groups: Vec<HashSet<Variant>>,
}

impl Same {
    fn members(&self, variant: &Variant) -> Vec<Variant> {
        match self.groups.iter().find(|g| g.contains(variant)) {
            Some(group) => group.iter().cloned().collect(),
            None => vec![variant.clone()],
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct Choices {
    pub players: Vec<String>,
    pub sources: Vec<i64>,
    pub groups: HashMap<i64, Vec<String>>,
    // The folded name or trigger of a group of players -> the group.
    pub player_groups: HashMap<String, String>,
    pub off_episodes: HashSet<Slot>,
    pub off_groups: HashSet<String>,
    pub off_compilations: HashSet<RowId>,
    pub reference: HashMap<Slot, Vec<Variant>>,
    pub reference_deny: HashMap<Slot, HashSet<Variant>>,
    pub voice_order: HashMap<(Slot, String), Vec<(i64, String)>>,
    pub voice_deny: HashMap<Slot, HashSet<Variant>>,
    pub voice_off: HashMap<Slot, HashSet<String>>,
    // The user's word on the compilation of an episode: a row, or none (the key is absent for no word: the rule).
    pub member: HashMap<Slot, Option<RowId>>,
    pub titles: Vec<(String, String)>,
    pub languages: Vec<(String, String)>,
    pub delete_videos: HashSet<String>,
    pub delete_tracks: HashSet<(String, RowId)>,
    pub markup: HashMap<Slot, Vec<Variant>>,
    pub markup_deny: HashMap<Slot, HashSet<Variant>>,
    pub same: HashMap<Slot, Same>,
    pub markup_same: HashMap<Slot, Same>,
    pub subtitle_order: HashMap<Slot, Vec<Variant>>,
    pub subtitle_deny: HashMap<Slot, HashSet<Variant>>,
}

pub struct Inputs {
    pub online: Vec<Online>,
    pub subtitles: Vec<Online>,
    pub containers: Vec<Container>,
    pub choices: Choices,
    pub failed: Fails,
}

/// The attempts that failed, by what they are of: a download of a row, an alignment or a cut onto a timeline.
#[derive(Default)]
pub struct Fails {
    download: HashSet<RowId>,
    align: HashMap<RowId, HashSet<RowId>>,
    cut: HashMap<RowId, HashSet<RowId>>,
}

impl Fails {
    pub fn add(&mut self, failed: Failed) {
        match failed {
            Failed::Download(row) => {
                self.download.insert(row);
            }
            Failed::Align(row, at) => {
                self.align.entry(row).or_default().insert(at);
            }
            Failed::Cut(row, at) => {
                self.cut.entry(row).or_default().insert(at);
            }
        }
    }

    fn download(&self, row: &RowId) -> bool {
        self.download.contains(row)
    }

    fn align(&self, row: &RowId, at: &RowId) -> bool {
        self.align.get(row).map_or(false, |s| s.contains(at))
    }

    fn cut(&self, row: &RowId, at: &RowId) -> bool {
        self.cut.get(row).map_or(false, |s| s.contains(at))
    }
}

// --- the goals ------------------------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub enum Snap<'a> {
    Of(&'a Snapshot),
    Row(Snapshot),
}

impl<'a> Snap<'a> {
    fn get(&self) -> &Snapshot {
        match self {
            Snap::Of(s) => s,
            Snap::Row(s) => s,
        }
    }
}

/// How the video of a goal is obtained (VideoWay): `rows` and `files` are places in the inputs.
#[derive(Clone, Debug)]
pub struct VideoWay<'a> {
    pub kind: &'static str,
    pub key: Key,
    pub timeline: RowId,
    pub snapshot: Snap<'a>,
    pub row: Option<usize>,
    pub container: Option<usize>,
    pub ref2: Option<Box<VideoWay<'a>>>,
    pub picture: Option<usize>,
}

#[derive(Clone, Debug)]
pub struct TrackWay<'a> {
    pub voice: String,
    pub dub: RowId,
    pub snapshot: Snap<'a>,
    pub kind: &'static str,
    pub row: Option<usize>,
    pub container: Option<usize>,
    pub own: bool,
}

#[derive(Clone, Debug)]
pub struct SubtitleWay {
    pub subtitle: Subtitle,
    pub kind: &'static str,
    // A row of `Inputs.subtitles`, or a file.
    pub row: Option<usize>,
    pub container: Option<usize>,
}

#[derive(Clone, Debug)]
pub struct Markup {
    pub id: RowId,
    pub group: Option<String>,
    pub height: i64,
    pub row: Option<usize>,
    pub variant: Option<Variant>,
}

#[derive(Clone, Debug)]
pub struct Goal<'a> {
    pub slot: Slot,
    pub video: VideoWay<'a>,
    pub tracks: Vec<TrackWay<'a>>,
    pub off: bool,
    pub voices_off: Vec<String>,
    pub own_at: i64,
    pub titles: Vec<(String, String)>,
    pub markup: Option<Markup>,
    pub languages: Vec<(String, String)>,
    pub subtitles: Vec<SubtitleWay>,
    pub unaligned: Vec<Variant>,
}

fn variant_json(v: &Variant) -> Value {
    json!([v.0, v.1, v.2])
}

impl<'a> Goal<'a> {
    /// The goal as the corpus writes it (the name the plain rule of names gives).
    pub fn json(&self, inputs: &Inputs) -> Value {
        let way = |w: &VideoWay| way_json(w, inputs);
        let part = if self.slot.len() > 2 { format!(" pt{}", self.slot[2]) } else { String::new() };
        let shot = self.video.snapshot.get();
        let name = format!("s{:02}e{:02}{part} [{}] {}p.mkv{}", self.slot[0], self.slot[1],
                           shot.group.as_deref().filter(|g| !g.is_empty()).unwrap_or("—"), shot.height,
                           if self.off { ".off" } else { "" });
        json!({
            "slot": self.slot, "video": way(&self.video),
            "tracks": self.tracks.iter().map(|t| json!([t.voice, t.dub.json(), t.snapshot.get().json(), t.kind,
                t.row.map(|r| inputs.online[r].id.json()), t.container.map(|c| inputs.containers[c].name.clone()), t.own]))
                .collect::<Vec<_>>(),
            "name": name, "off": self.off, "voices_off": self.voices_off, "own_at": self.own_at,
            "titles": self.titles.iter().map(|(a, b)| json!([a, b])).collect::<Vec<_>>(),
            "languages": self.languages.iter().map(|(a, b)| json!([a, b])).collect::<Vec<_>>(),
            "markup": self.markup.as_ref().map(|m| json!([m.id.json(), m.group, m.height,
                m.row.map(|r| inputs.online[r].id.json()), m.variant.as_ref().map(variant_json)])),
            "subtitles": self.subtitles.iter().map(|w| json!([w.subtitle.json(), w.kind,
                w.row.map(|r| inputs.subtitles[r].id.json()), w.container.map(|c| inputs.containers[c].name.clone())]))
                .collect::<Vec<_>>(),
            "unaligned": self.unaligned.iter().map(variant_json).collect::<Vec<_>>(),
        })
    }
}

fn way_json(w: &VideoWay, inputs: &Inputs) -> Value {
    json!({"kind": w.kind, "key": w.key.json(), "timeline": w.timeline.json(), "snapshot": w.snapshot.get().json(),
           "row": w.row.map(|r| inputs.online[r].id.json()), "container": w.container.map(|c| inputs.containers[c].name.clone()),
           "ref2": w.ref2.as_ref().map(|r| way_json(r, inputs)), "picture": w.picture.map(|c| inputs.containers[c].name.clone())})
}

/// The key of a voice-over in a container: its group; an unknown voice-over is a voice of its own row.
fn voice_of(group: Option<&str>, dub: &RowId) -> String {
    match group {
        Some(g) => g.to_string(),
        None => format!("?{}:{}:{}", dub.source, dub.key, dub.video),
    }
}

const HIDDEN: &str = ".hidden-";

/// The order of the players and of the sources the rule compares against (core/variant_queue.Order).
struct Order<'c> {
    places: HashMap<String, i64>,
    size: i64,
    groups: &'c HashMap<String, String>,
    sources: HashMap<i64, i64>,
    known: std::cell::RefCell<HashMap<String, i64>>,
}

impl<'c> Order<'c> {
    fn of(choices: &'c Choices) -> Order<'c> {
        let mut order = Order { places: HashMap::new(), size: choices.players.len() as i64, groups: &choices.player_groups,
                                sources: HashMap::new(), known: Default::default() };
        for (place, kind) in choices.players.iter().enumerate() {
            let key = order.canonical(kind);
            order.places.entry(key).or_insert(place as i64);
        }
        for (place, source) in choices.sources.iter().enumerate() {
            order.sources.insert(*source, place as i64);
        }
        order
    }

    fn canonical(&self, name: &str) -> String {
        let key = fold_name(name);
        let group = if key.is_empty() { None } else { self.groups.get(&key) };
        fold_name(group.map_or(name, String::as_str))
    }

    fn player(&self, player: &str) -> i64 {
        if let Some(found) = self.known.borrow().get(player) {
            return *found;
        }
        let name = if player.starts_with("torrent:") { "torrent" } else { player };
        let found = *self.places.get(&self.canonical(name)).unwrap_or(&self.size);
        self.known.borrow_mut().insert(player.to_string(), found);
        found
    }

    fn source(&self, source: i64) -> i64 {
        *self.sources.get(&source).unwrap_or(&(self.sources.len() as i64))
    }
}

/// `repr` of a row, compared only when everything before it in a rank is equal (the last tie-break).
#[derive(Clone, Debug)]
pub struct Repr<'r>(&'r RowId);

impl PartialEq for Repr<'_> {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0 || self.0.repr() == other.0.repr()
    }
}

impl Eq for Repr<'_> {}

impl PartialOrd for Repr<'_> {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Repr<'_> {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        if self.0 == other.0 { std::cmp::Ordering::Equal } else { self.0.repr().cmp(&other.0.repr()) }
    }
}

/// The rank of a video: (deleted, named, -height, player, source, held 0/1, position, repr) — the tuple of Python.
type Rank<'r> = (bool, i64, i64, i64, i64, i64, i64, Repr<'r>);

/// A sound a file holds, with what a goal asks of it made once: its voice-over and its variant.
struct Held {
    container: usize,
    // The track of the file; none for the own sound of its video.
    track: Option<usize>,
    own: bool,
    deleted: bool,
    voice: String,
    variant: Variant,
}

struct Index<'a> {
    rows: HashMap<Slot, Vec<usize>>,
    files: HashMap<Slot, Vec<usize>>,
    tracks: HashMap<Slot, Vec<Held>>,
    compilations: Vec<usize>,
    subtitles: HashMap<Slot, Vec<usize>>,
    // The own sound of the video of every file, by the file.
    own: Vec<Track>,
    _p: std::marker::PhantomData<&'a ()>,
}

pub struct Planner<'a> {
    inputs: &'a Inputs,
    online: HashMap<&'a RowId, usize>,
    order: Order<'a>,
    voices_order: HashMap<i64, HashMap<&'a str, i64>>,
    by_slot: Index<'a>,
    deleted: &'a HashSet<String>,
}

fn sort_tuples<T: Ord>(items: &mut [T]) {
    items.sort();
}

impl<'a> Planner<'a> {
    pub fn new(inputs: &'a Inputs) -> Planner<'a> {
        let choices = &inputs.choices;
        let mut online = HashMap::new();
        for (i, row) in inputs.online.iter().enumerate() {
            online.insert(&row.id, i);
        }
        let mut on_timeline: HashMap<RowId, usize> = HashMap::new();
        for (i, row) in inputs.online.iter().enumerate() {
            on_timeline.insert(row.timeline().clone(), i);
        }
        for (i, row) in inputs.online.iter().enumerate() {
            on_timeline.insert(row.id.clone(), i);
        }
        let video_slot = |video: &Video| -> Slot {
            let row = if video.cut_of.is_some() { on_timeline.get(&video.timeline) } else { online.get(&video.id) };
            match row.map(|r| &inputs.online[*r]) {
                Some(row) if !row.compilation() => row.slot(),
                _ => video.episode.clone(),
            }
        };
        let track_slot = |track: &Track| -> Slot {
            match online.get(&track.dub).map(|r| &inputs.online[*r]) {
                Some(row) if !row.compilation() => row.slot(),
                _ => track.episode.clone(),
            }
        };
        let mut voices_order = HashMap::new();
        for (season, order) in &choices.groups {
            let places: HashMap<&str, i64> = order.iter().enumerate().map(|(p, v)| (v.as_str(), p as i64)).collect();
            voices_order.insert(*season, places);
        }
        let mut by_slot = Index { rows: HashMap::new(), files: HashMap::new(), tracks: HashMap::new(), compilations: vec![],
                                  subtitles: HashMap::new(), own: vec![], _p: Default::default() };
        for (i, row) in inputs.online.iter().enumerate() {
            if row.compilation() {
                by_slot.compilations.push(i);
            } else {
                by_slot.rows.entry(row.slot()).or_default().push(i);
            }
        }
        for (i, row) in inputs.subtitles.iter().enumerate() {
            if !row.compilation() {
                by_slot.subtitles.entry(row.slot()).or_default().push(i);
            }
        }
        let deleted = &choices.delete_videos;
        // The files in the order of their names (the containers of a plan).
        let mut named: Vec<usize> = (0..inputs.containers.len()).collect();
        named.sort_by(|a, b| inputs.containers[*a].name.cmp(&inputs.containers[*b].name));
        let mut best = named.clone();
        best.sort_by(|a, b| {
            let key = |i: &usize| {
                let c = &inputs.containers[*i];
                (deleted.contains(&c.name), -c.video.snapshot.height, c.name.contains(HIDDEN), c.off, c.name.clone())
            };
            key(a).cmp(&key(b))
        });
        for i in &best {
            let here = video_slot(&inputs.containers[*i].video);
            by_slot.files.entry(here.clone()).or_default().push(*i);
            by_slot.tracks.entry(here).or_default();
        }
        by_slot.own = inputs.containers.iter().map(|c| {
            let video = &c.video;
            Track { dub: video.cut_of.clone().unwrap_or_else(|| video.id.clone()), snapshot: video.snapshot.clone(),
                    episode: video.episode.clone(), aligned_to: video.timeline.clone() }
        }).collect();
        let held = |container: usize, track: Option<usize>, own: bool, deleted: bool, of: &Track| Held {
            container, track, own, deleted, voice: voice_of(of.snapshot.group.as_deref(), &of.dub),
            variant: (of.snapshot.variant_label().to_string(), of.dub.source, of.snapshot.player.clone()),
        };
        for i in &named {
            let c = &inputs.containers[*i];
            let made = held(*i, None, true, deleted.contains(&c.name), &by_slot.own[*i]);
            by_slot.tracks.entry(video_slot(&c.video)).or_default().push(made);
            for (t, track) in c.tracks.iter().enumerate() {
                let gone = choices.delete_tracks.contains(&(c.name.clone(), track.dub.clone()));
                let made = held(*i, Some(t), false, gone, track);
                by_slot.tracks.entry(track_slot(track)).or_default().push(made);
            }
        }
        Planner { inputs, online, order: Order::of(choices), voices_order, by_slot, deleted }
    }

    /// The track a held sound is: a track of its file, or the own sound of its video.
    fn track(&self, held: &Held) -> &Track {
        match held.track {
            Some(t) => &self.inputs.containers[held.container].tracks[t],
            None => &self.by_slot.own[held.container],
        }
    }

    fn position(&self, id: &RowId) -> i64 {
        self.online.get(id).map_or(self.inputs.online.len() as i64, |r| self.inputs.online[*r].position)
    }

    fn group_rank<'v>(&self, season: i64, voice: &'v str) -> (i64, &'v str) {
        let places = self.voices_order.get(&season);
        let size = places.map_or(0, |p| p.len() as i64);
        (places.and_then(|p| p.get(voice).copied()).unwrap_or(size), voice)
    }

    fn own_place<'v>(&self, season: i64, own: &str, voices: impl Iterator<Item = &'v str>) -> i64 {
        if !self.voices_order.get(&season).map_or(false, |p| p.contains_key(own)) {
            return 0;
        }
        let mine = self.group_rank(season, own);
        voices.filter(|v| self.group_rank(season, v) < mine).count() as i64
    }

    /// (the place the user's order gives the variant, its player, its source): of its group of equals, the best.
    fn standing(&self, variant: &Variant, same: &Same, wanted: &[Variant]) -> (i64, i64, i64) {
        same.members(variant).iter().map(|each| {
            let named = wanted.iter().position(|w| w == each).unwrap_or(wanted.len()) as i64;
            (named, self.order.player(&each.2), self.order.source(each.1))
        }).min().unwrap()
    }

    fn video_rank(&self, variant: &Variant, height: i64, held: bool, row: &'a RowId, deleted: bool, same: &Same,
                  wanted: &[Variant]) -> Rank<'a> {
        let (named, player, source) = self.standing(variant, same, wanted);
        (deleted, named, -height, player, source, if held { 0 } else { 1 }, self.position(row), Repr(row))
    }

    fn switched_off(&self, held: &Held, episode: &Slot) -> bool {
        let choices = &self.inputs.choices;
        choices.off_groups.contains(&held.voice) || choices.voice_off.get(episode).map_or(false, |v| v.contains(&held.voice))
            || choices.voice_deny.get(episode).map_or(false, |d| d.contains(&held.variant))
    }

    pub fn goals(&self) -> Vec<Goal<'a>> {
        let mut slots: Vec<&Slot> = self.by_slot.rows.keys().chain(self.by_slot.files.keys()).collect();
        slots.sort();
        slots.dedup();
        slots.into_iter().filter_map(|slot| self.goal(slot)).collect()
    }

    fn markup_of(&self, episode: &Slot, singles: &[usize], files: &[usize]) -> Option<Markup> {
        let choices = &self.inputs.choices;
        let order = choices.markup.get(episode)?;
        let none = HashSet::new();
        let denied = choices.markup_deny.get(episode).unwrap_or(&none);
        let no_same = Same::default();
        let same = choices.markup_same.get(episode).unwrap_or(&no_same);
        let rank = |variant: &Variant, height: i64, row: &'a RowId, held: bool| {
            let (named, player, source) = self.standing(variant, same, order);
            (named, -height, player, source, if held { 0 } else { 1 }, self.position(row), Repr(row))
        };
        let mut found: Vec<((i64, i64, i64, i64, i64, i64, Repr<'a>), Markup)> = Vec::new();
        for r in singles {
            let row = &self.inputs.online[*r];
            if row.playable && !denied.contains(row.variant())
                && !self.inputs.failed.download(&row.id) {
                found.push((rank(row.variant(), row.height, &row.id, false),
                            Markup { id: row.timeline().clone(), group: Some(row.group.clone()), height: row.height, row: Some(*r),
                                     variant: Some(row.variant().clone()) }));
            }
        }
        let mut online: HashMap<RowId, usize> = HashMap::new();
        for r in singles {
            online.insert(self.inputs.online[*r].timeline().clone(), *r);
        }
        for r in singles {
            online.insert(self.inputs.online[*r].id.clone(), *r);
        }
        for c in files {
            let container = &self.inputs.containers[*c];
            let timeline = &container.video.timeline;
            let Some(shot) = timeline_shot(container) else { continue };
            if shot.group.is_none() {
                continue;
            }
            let variant = (shot.variant_label().to_string(), timeline.source, shot.player.clone());
            if !denied.contains(&variant) {
                found.push((rank(&variant, shot.height, timeline, true),
                            Markup { id: timeline.clone(), group: shot.group.clone(), height: shot.height,
                                     row: online.get(timeline).copied(), variant: Some(variant) }));
            }
        }
        first_min(found)
    }

    fn goal(&self, slot: &Slot) -> Option<Goal<'a>> {
        let inputs = self.inputs;
        let choices = &inputs.choices;
        let episode: Slot = slot[..2].to_vec();
        let off = choices.off_episodes.contains(&episode);
        let nothing: Vec<Variant> = Vec::new();
        let wanted = choices.reference.get(&episode).unwrap_or(&nothing);
        let none = HashSet::new();
        let denied = choices.reference_deny.get(&episode).unwrap_or(&none);
        let no_same = Same::default();
        let same = choices.same.get(&episode).unwrap_or(&no_same);
        let empty = Vec::new();
        let singles = self.by_slot.rows.get(slot).unwrap_or(&empty);
        let files_here = self.by_slot.files.get(slot).unwrap_or(&empty);
        let gone = |w: &VideoWay| w.container.map_or(false, |c| self.deleted.contains(&inputs.containers[c].name));
        let live: Vec<usize> = files_here.iter().copied().filter(|c| !self.deleted.contains(&inputs.containers[*c].name))
            .collect();
        let markup = self.markup_of(&episode, singles, &live);
        let timeline = markup.as_ref().map(|m| Key::Row(m.id.clone()));
        let mut candidates: Vec<(Rank<'a>, VideoWay<'a>)> = Vec::new();
        let mut seen: HashSet<Key> = HashSet::new();
        for c in files_here {
            let video = &inputs.containers[*c].video;
            let source = video.cut_of.as_ref().unwrap_or(&video.id);
            let variant = (video.snapshot.variant_label().to_string(), source.source, video.snapshot.player.clone());
            let key = video.key();
            if seen.contains(&key) || (denied.contains(&variant) && Some(&key) != timeline.as_ref()) {
                continue;
            }
            seen.insert(key.clone());
            let way = VideoWay { kind: "asset", key, timeline: video.timeline.clone(), snapshot: Snap::Of(&video.snapshot),
                                 row: None, container: Some(*c), ref2: None, picture: None };
            let deleted = gone(&way);
            candidates.push((self.video_rank(&variant, video.snapshot.height, true, source, deleted, same, wanted), way));
        }
        if !off {
            for r in singles {
                let row = &inputs.online[*r];
                let row_timeline = Key::Row(row.timeline().clone());
                if !row.playable || inputs.failed.download(&row.id)
                    || (denied.contains(row.variant()) && Some(&row_timeline) != timeline.as_ref()) {
                    continue;
                }
                let held = candidates.iter().map(|(_, w)| w).find(|w| w.key == row_timeline && !gone(w));
                if held.map_or(false, |h| h.snapshot.get().height >= row.height) {
                    continue; // the asset holds this video at least as high: no download of it
                }
                let way = VideoWay { kind: "download", key: row_timeline, timeline: row.timeline().clone(),
                                     snapshot: Snap::Row(row.snapshot()), row: Some(*r), container: None, ref2: None,
                                     picture: None };
                candidates.push((self.video_rank(row.variant(), row.height, false, &row.id, false, same, wanted), way));
            }
            let open: Vec<(Rank<'a>, VideoWay<'a>)> = candidates.iter().filter(|(_, w)| !gone(w)).cloned().collect();
            let cuts = self.cuts(slot, singles, &open, denied, markup.as_ref(), same, wanted);
            candidates.extend(cuts);
        }
        // The user's word on the compilation is stronger than what the asset holds.
        let pictures: HashSet<&RowId> = singles.iter().map(|r| &inputs.online[*r].id).collect();
        let compiled = |w: &VideoWay| matches!(&w.key, Key::Cut(row, _) if !pictures.contains(row));
        match choices.member.get(&episode) {
            Some(None) => candidates.retain(|(_, w)| !compiled(w)),
            Some(Some(word)) => {
                if candidates.iter().any(|(_, w)| compiled(w) && matches!(&w.key, Key::Cut(row, _) if row == word)) {
                    candidates.retain(|(_, w)| !compiled(w) || matches!(&w.key, Key::Cut(row, _) if row == word));
                }
            }
            None => {}
        }
        if candidates.is_empty() {
            return None;
        }
        // With a markup reference every video on its timeline comes before every other one.
        let mut best = 0;
        let key = |i: usize| {
            let (rank, way) = &candidates[i];
            (rank.0, markup.as_ref().map_or(false, |m| way.timeline != m.id), rank.1, rank.2, rank.3, rank.4, rank.5, rank.6,
             rank.7.clone())
        };
        for i in 1..candidates.len() {
            if key(i) < key(best) {
                best = i;
            }
        }
        let video = candidates.swap_remove(best).1;
        let own = voice_of(video.snapshot.get().group.as_deref(), &video.timeline);
        let idle = off || gone(&video);
        let mut taken_off: HashSet<String> = choices.off_groups.clone();
        if let Some(more) = choices.voice_off.get(&episode) {
            taken_off.extend(more.iter().cloned());
        }
        let left_out = choices.voice_deny.get(&episode).unwrap_or(&none);
        let cut_row = if video.kind == "cut" { video.row.map(|r| &inputs.online[r].id) } else { None };
        let mut dubbing: Vec<usize> = singles.iter().copied().filter(|r| inputs.online[*r].playable).collect();
        if slot.len() == 2 {
            for r in &self.by_slot.compilations {
                let row = &inputs.online[*r];
                if row.season == slot[0] && row.playable && row.start <= slot[1] && slot[1] <= row.end
                    && Some(&row.id) != cut_row {
                    dubbing.push(*r);
                }
            }
        }
        let mut voices: Vec<String> = Vec::new();
        let mut add = |voice: String, voices: &mut Vec<String>| {
            if !voices.contains(&voice) {
                voices.push(voice);
            }
        };
        if !idle {
            for r in &dubbing {
                let row = &inputs.online[*r];
                if !taken_off.contains(&row.group) && row.group != own {
                    add(row.group.clone(), &mut voices);
                }
            }
        }
        let held_all = self.by_slot.tracks.get(slot).map(Vec::as_slice).unwrap_or(&[]);
        let held_tracks: Vec<&Held> = held_all.iter()
            .filter(|h| !(h.own && Some(h.container) == video.container) && !self.switched_off(h, &episode))
            .collect();
        for h in &held_tracks {
            if h.voice != own {
                add(h.voice.clone(), &mut voices);
            }
        }
        let last = markup.as_ref().map(|m| voice_of(m.group.as_deref(), &m.id));
        let mut order: Vec<(bool, (i64, &str), usize)> = voices.iter().enumerate()
            .map(|(i, v)| (Some(v) == last.as_ref(), self.group_rank(slot[0], v), i)).collect();
        order.sort();
        let name = |c: usize| inputs.containers[c].name.as_str();
        let mut tracks: Vec<TrackWay<'a>> = Vec::new();
        for (_, _, at) in order {
            let voice = &voices[at];
            let mine: Vec<&Held> = held_tracks.iter().copied().filter(|h| &h.voice == voice).collect();
            let take = |h: &Held, kind: &'static str| -> TrackWay<'a> {
                let snapshot = match h.track {
                    Some(t) => Snap::Of(&inputs.containers[h.container].tracks[t].snapshot),
                    None => Snap::Of(&inputs.containers[h.container].video.snapshot),
                };
                TrackWay { voice: voice.clone(), dub: self.track(h).dub.clone(), snapshot, kind, row: None,
                           container: Some(h.container), own: h.own }
            };
            let file_order = |h: &&Held| (Some(h.container) != video.container, h.own, name(h.container));
            let mut reuse: Vec<&Held> = mine.iter().copied()
                .filter(|h| self.track(h).aligned_to == video.timeline && !h.deleted).collect();
            reuse.sort_by(|a, b| file_order(a).cmp(&file_order(b)));
            if let Some(first) = reuse.first() {
                tracks.push(take(first, "reuse"));
                continue;
            }
            let no_order = Vec::new();
            let preferred = choices.voice_order.get(&(episode.clone(), voice.clone())).unwrap_or(&no_order);
            let mut rows: Vec<usize> = dubbing.iter().copied().filter(|r| {
                let row = &inputs.online[*r];
                &row.group == voice && !idle && !taken_off.contains(voice) && !left_out.contains(row.variant())
                    && !inputs.failed.download(&row.id)
                    && !inputs.failed.align(&row.id, &video.timeline)
            }).collect();
            let rank = |r: &usize| {
                let row = &inputs.online[*r];
                let named = preferred.iter().position(|(s, p)| *s == row.id.source && *p == row.player)
                    .unwrap_or(preferred.len()) as i64;
                (named, -row.height, self.order.player(&row.player), row.position, Repr(&row.id))
            };
            rows.sort_by_key(rank);
            if let Some(first) = rows.first() {
                let row = &inputs.online[*first];
                let kind = if *row.timeline() == video.timeline { "timeline" } else { "download" };
                tracks.push(TrackWay { voice: voice.clone(), dub: row.id.clone(), snapshot: Snap::Row(row.snapshot()), kind,
                                       row: Some(*first), container: None, own: false });
                continue;
            }
            // A track the user deleted is no source of itself.
            let usable: Vec<&Held> = mine.iter().copied().filter(|h| !(h.deleted && !h.own)).collect();
            let mut sources: Vec<&Held> = usable.iter().copied()
                .filter(|h| !inputs.failed.align(&self.track(h).dub, &video.timeline)).collect();
            if !sources.is_empty() && !idle {
                sources.sort_by(|a, b| (a.own, name(a.container), &self.track(a).dub)
                    .cmp(&(b.own, name(b.container), &self.track(b).dub)));
                tracks.push(take(sources[0], "realign"));
                continue;
            }
            let pool: &Vec<&Held> = if idle { &mine } else { &usable };
            let mut waiting: Vec<&Held> = pool.iter().copied().filter(|h| self.track(h).aligned_to == video.timeline)
                .collect();
            waiting.sort_by(|a, b| file_order(a).cmp(&file_order(b)));
            if let Some(first) = waiting.first() {
                tracks.push(take(first, "reuse"));
            }
        }
        // The names of the sounds the user gave; an idle file keeps the names it was written under.
        let mut sounds: HashSet<String> = HashSet::new();
        sounds.insert(video.snapshot.get().group.clone().filter(|g| !g.is_empty()).unwrap_or_else(|| "—".into()));
        for way in &tracks {
            sounds.insert(way.snapshot.get().group.clone().filter(|g| !g.is_empty()).unwrap_or_else(|| way.voice.clone()));
        }
        let mut titles: Vec<(String, String)> = choices.titles.iter().filter(|(l, _)| sounds.contains(l)).cloned().collect();
        sort_tuples(&mut titles);
        let mut languages: Vec<(String, String)> = choices.languages.iter().filter(|(l, _)| sounds.contains(l)).cloned()
            .collect();
        sort_tuples(&mut languages);
        if idle {
            if let Some(c) = video.container {
                titles = inputs.containers[c].titles.clone();
                languages = inputs.containers[c].languages.clone();
            }
        }
        let own_at = if Some(&own) == last.as_ref() { tracks.len() as i64 } else {
            self.own_place(slot[0], &own, tracks.iter().map(|w| w.voice.as_str()).filter(|v| Some(*v) != last.as_deref()))
        };
        let (subtitles, unaligned) = self.subtitles(slot, &video, idle);
        let mut voices_off: Vec<String> = taken_off.into_iter().collect();
        voices_off.sort();
        Some(Goal { slot: slot.clone(), video, tracks, off, voices_off, own_at, titles, markup, languages, subtitles,
                    unaligned })
    }

    #[allow(clippy::too_many_arguments)]
    fn cuts(&self, slot: &Slot, singles: &[usize], candidates: &[(Rank<'a>, VideoWay<'a>)], denied: &HashSet<Variant>,
            markup: Option<&Markup>, same: &Same, wanted: &[Variant]) -> Vec<(Rank<'a>, VideoWay<'a>)> {
        if slot.len() > 2 {
            return vec![];
        }
        let inputs = self.inputs;
        let episode: Slot = slot[..2].to_vec();
        let word = inputs.choices.member.get(&episode);
        let held: Vec<&(Rank<'a>, VideoWay<'a>)> = candidates.iter().filter(|(_, w)| matches!(w.key, Key::Row(_))).collect();
        let (ref2, floor) = if let Some(markup) = markup {
            let mine = Key::Row(markup.id.clone());
            let mut found: Option<&VideoWay<'a>> = None;
            for (_, way) in &held {
                if way.key == mine && found.map_or(true, |f| (way.kind != "asset") < (f.kind != "asset")) {
                    found = Some(way);
                }
            }
            let Some(ref2) = found else { return vec![] };
            let row = markup.row.map(|r| &inputs.online[r]);
            let row_height = row.filter(|r| r.playable && !inputs.failed.download(&r.id))
                .map_or(0, |r| r.height);
            (ref2.clone(), markup.height.max(row_height))
        } else if !held.is_empty() {
            let mut best = 0;
            for i in 1..held.len() {
                if held[i].0 < held[best].0 {
                    best = i;
                }
            }
            (held[best].1.clone(), held.iter().map(|(_, w)| w.snapshot.get().height).max().unwrap())
        } else {
            return vec![];
        };
        let timeline = ref2.timeline.clone();
        let fits = |row: &Online| {
            row.playable && row.height > floor && !denied.contains(row.variant())
                && !inputs.failed.download(&row.id)
                && !inputs.failed.cut(&row.id, &timeline)
        };
        let mut pictures: Vec<usize> = singles.iter().copied().filter(|r| {
            let row = &inputs.online[*r];
            markup.is_some() && *row.timeline() != timeline && fits(row)
        }).collect();
        let mut covering: Vec<usize> = self.by_slot.compilations.iter().copied().filter(|r| {
            let row = &inputs.online[*r];
            !matches!(word, Some(None)) && fits(row) && row.season == slot[0] && row.start <= slot[1] && slot[1] <= row.end
                && !inputs.choices.off_compilations.contains(&row.id)
        }).collect();
        let named: Vec<usize> = covering.iter().copied()
            .filter(|r| matches!(word, Some(Some(w)) if *w == inputs.online[*r].id)).collect();
        if !named.is_empty() {
            covering = named;
        }
        if !covering.is_empty() {
            let rank = |r: usize| {
                let row = &inputs.online[r];
                (row.end - row.start, -row.height, row.position, Repr(&row.id))
            };
            let mut best = covering[0];
            for r in &covering[1..] {
                if rank(*r) < rank(best) {
                    best = *r;
                }
            }
            pictures.push(best);
        }
        let mut found = Vec::new();
        for r in pictures {
            let row = &inputs.online[r];
            let key = Key::Cut(row.id.clone(), timeline.clone());
            if candidates.iter().any(|(_, w)| w.key == key && w.snapshot.get().height >= row.height) {
                continue; // the asset holds this cut already, as high as the picture is now
            }
            let picture = held.iter().find(|(_, w)| w.kind == "asset" && w.key == Key::Row(row.timeline().clone())
                && w.snapshot.get().height >= row.height).and_then(|(_, w)| w.container);
            let way = VideoWay { kind: "cut", key, timeline: timeline.clone(), snapshot: Snap::Row(row.snapshot()),
                                 row: Some(r), container: None, ref2: Some(Box::new(ref2.clone())), picture };
            found.push((self.video_rank(row.variant(), row.height, false, &row.id, false, same, wanted), way));
        }
        found
    }

    fn subtitles(&self, slot: &Slot, video: &VideoWay, idle: bool) -> (Vec<SubtitleWay>, Vec<Variant>) {
        let inputs = self.inputs;
        if idle {
            let held = video.container.map(|c| inputs.containers[c].subtitles.clone()).unwrap_or_default();
            return (held.into_iter().map(|s| SubtitleWay { subtitle: s, kind: "reuse", row: None, container: video.container })
                        .collect(), vec![]);
        }
        let choices = &inputs.choices;
        let episode: Slot = slot[..2].to_vec();
        let empty = Vec::new();
        let rows = self.by_slot.subtitles.get(slot).unwrap_or(&empty);
        let places: HashMap<i64, i64> = choices.sources.iter().enumerate().map(|(p, s)| (*s, p as i64)).collect();
        let place = |s: i64| *places.get(&s).unwrap_or(&(places.len() as i64));
        let mut files: Vec<usize> = self.by_slot.files.get(slot).unwrap_or(&empty).iter().copied()
            .filter(|c| inputs.containers[*c].video.timeline == video.timeline).collect();
        files.sort_by(|a, b| (Some(*a) != video.container, &inputs.containers[*a].name)
            .cmp(&(Some(*b) != video.container, &inputs.containers[*b].name)));
        let mut held: Vec<(Variant, usize, usize)> = Vec::new();
        for c in &files {
            for (s, each) in inputs.containers[*c].subtitles.iter().enumerate() {
                if each.timeline == video.timeline && !held.iter().any(|(v, _, _)| *v == each.variant()) {
                    held.push((each.variant(), *c, s));
                }
            }
        }
        let last = rows.len() as i64 + 1;
        let mut found: Vec<(Variant, i64, i64)> = rows.iter().map(|r| {
            let row = &inputs.subtitles[*r];
            (row.variant().clone(), place(row.id.source), row.position)
        }).collect();
        found.extend(held.iter().map(|(v, _, _)| (v.clone(), place(v.1), last)));
        let none = HashSet::new();
        let denied = choices.subtitle_deny.get(&episode).unwrap_or(&none);
        let no_order = Vec::new();
        let mut ways = Vec::new();
        let mut unaligned = Vec::new();
        for variant in ordered_subtitles(&found, choices.subtitle_order.get(&episode).unwrap_or(&no_order)) {
            if denied.contains(&variant) {
                continue;
            }
            let mut in_step: Vec<usize> = rows.iter().copied().filter(|r| {
                let row = &inputs.subtitles[*r];
                *row.variant() == variant && *row.timeline() == video.timeline && row.playable
                    && !inputs.failed.download(&row.id)
            }).collect();
            in_step.sort_by_key(|r| (inputs.subtitles[*r].position, Repr(&inputs.subtitles[*r].id)));
            let kept = held.iter().find(|(v, _, _)| *v == variant);
            match (kept, in_step.first()) {
                (Some((_, c, s)), first) if first.map_or(true, |r| inputs.containers[*c].subtitles[*s].row
                    == inputs.subtitles[*r].id) => {
                    ways.push(SubtitleWay { subtitle: inputs.containers[*c].subtitles[*s].clone(), kind: "reuse", row: None,
                                            container: Some(*c) });
                }
                (_, Some(r)) => {
                    let row = &inputs.subtitles[*r];
                    ways.push(SubtitleWay { subtitle: Subtitle { row: row.id.clone(), name: row.label.clone(),
                                                                 player: row.player.clone(), timeline: row.timeline().clone(),
                                                                 language: row.language.clone(),
                                                                 subtitle_type: row.subtitle_type.clone(), default: false,
                                                                 forced: false },
                                            kind: "take", row: Some(*r), container: None });
                }
                _ => {
                    if rows.iter().any(|r| *inputs.subtitles[*r].variant() == variant) {
                        unaligned.push(variant);
                    }
                }
            }
        }
        // The first full track is the default one, the first track of signs the forced one.
        let default = ways.iter().position(|w| w.subtitle.subtitle_type == "full");
        let forced = ways.iter().position(|w| w.subtitle.subtitle_type == "signs");
        for (at, way) in ways.iter_mut().enumerate() {
            way.subtitle.default = Some(at) == default;
            way.subtitle.forced = Some(at) == forced;
        }
        (ways, unaligned)
    }
}

/// The first of the least in the order given (Python's `min` with a key).
fn first_min<K: Ord, T>(items: Vec<(K, T)>) -> Option<T> {
    let mut best: Option<(K, T)> = None;
    for (k, t) in items {
        if best.as_ref().map_or(true, |(b, _)| k < *b) {
            best = Some((k, t));
        }
    }
    best.map(|(_, t)| t)
}

/// What the timeline a file lies on is: the video itself, else what the file kept of it, else the sound of it it holds.
fn timeline_shot(container: &Container) -> Option<&Snapshot> {
    let video = &container.video;
    if video.cut_of.is_none() {
        return Some(&video.snapshot);
    }
    video.timeline_snapshot.as_ref()
        .or_else(|| container.tracks.iter().find(|t| t.dub == video.timeline).map(|t| &t.snapshot))
}

/// The variants of subtitles in the order of the container (core/subtitle_queue.ordered).
fn ordered_subtitles(found: &[(Variant, i64, i64)], named: &[Variant]) -> Vec<Variant> {
    let mut places: Vec<(Variant, (i64, i64))> = Vec::new();
    for (variant, source, position) in found {
        match places.iter_mut().find(|(v, _)| v == variant) {
            Some(slot) => slot.1 = slot.1.min((*source, *position)),
            None => places.push((variant.clone(), (*source, *position))),
        }
    }
    let mut first: Vec<Variant> = Vec::new();
    for variant in named {
        if !first.contains(variant) && places.iter().any(|(v, _)| v == variant) {
            first.push(variant.clone());
        }
    }
    let mut rest: Vec<&(Variant, (i64, i64))> = places.iter().filter(|(v, _)| !first.contains(v)).collect();
    rest.sort_by_key(|(_, p)| *p);
    first.extend(rest.into_iter().map(|(v, _)| v.clone()));
    first
}

// --- the inputs of the corpus (JSON) --------------------------------------------------------------------------------

fn s(v: &Value) -> String {
    v.as_str().unwrap_or("").to_string()
}

fn rid(v: &Value) -> RowId {
    RowId { source: v[0].as_i64().unwrap_or(0), key: s(&v[1]), video: s(&v[2]) }
}

fn opt_rid(v: &Value) -> Option<RowId> {
    if v.is_null() { None } else { Some(rid(v)) }
}

fn shot(v: &Value) -> Snapshot {
    Snapshot { source: s(&v[0]), player: s(&v[1]), group: v[2].as_str().map(str::to_string), height: v[3].as_i64().unwrap_or(0),
               link: s(&v[4]), label: s(&v[5]) }
}

fn slot_of(v: &Value) -> Slot {
    v.as_array().map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default()
}

fn variant(v: &Value) -> Variant {
    (s(&v[0]), v[1].as_i64().unwrap_or(0), s(&v[2]))
}

fn online_of(v: &Value) -> Online {
    Online { id: rid(&v[0]), season: v[1].as_i64().unwrap_or(0), start: v[2].as_i64().unwrap_or(0),
             end: v[3].as_i64().unwrap_or(0), player: s(&v[4]), group: s(&v[5]), height: v[6].as_i64().unwrap_or(0),
             source: s(&v[7]), link: s(&v[8]), label: s(&v[9]), position: v[10].as_i64().unwrap_or(0), part: v[11].as_i64(),
             playable: v[12].as_bool().unwrap_or(false), current: v[13].as_bool().unwrap_or(false), player_label: s(&v[14]),
             timeline_key: s(&v[15]), track: s(&v[16]), language: s(&v[17]), subtitle_type: s(&v[18]),
             timeline_id: RowId { source: 0, key: String::new(), video: String::new() },
             variant_of: (String::new(), 0, String::new()) }.made()
}

fn subtitle_of(v: &Value) -> Subtitle {
    Subtitle { row: rid(&v[0]), name: s(&v[1]), player: s(&v[2]), timeline: rid(&v[3]), language: s(&v[4]),
               subtitle_type: s(&v[5]), default: v[6].as_bool().unwrap_or(false), forced: v[7].as_bool().unwrap_or(false) }
}

fn pairs(v: &Value) -> Vec<(String, String)> {
    v.as_array().map(|a| a.iter().map(|p| (s(&p[0]), s(&p[1]))).collect()).unwrap_or_default()
}

fn by_slot<T>(v: &Value, f: impl Fn(&Value) -> T) -> HashMap<Slot, T> {
    v.as_array().map(|a| a.iter().map(|p| (slot_of(&p[0]), f(&p[1]))).collect()).unwrap_or_default()
}

fn variants(v: &Value) -> Vec<Variant> {
    v.as_array().map(|a| a.iter().map(variant).collect()).unwrap_or_default()
}

fn same_of(v: &Value) -> Same {
    Same { groups: v.as_array().map(|a| a.iter().map(|g| variants(g).into_iter().collect()).collect()).unwrap_or_default() }
}

fn keyed_row(v: &Value) -> RowId {
    rid(&v["row"])
}

impl Inputs {
    pub fn from_json(v: &Value) -> Inputs {
        let c = &v["choices"];
        let mut choices = Choices {
            players: c["players"].as_array().map(|a| a.iter().map(s).collect()).unwrap_or_default(),
            sources: c["sources"].as_array().map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default(),
            groups: c["groups"].as_array().map(|a| a.iter().map(|p| (p[0].as_i64().unwrap_or(0),
                p[1].as_array().map(|x| x.iter().map(s).collect()).unwrap_or_default())).collect()).unwrap_or_default(),
            player_groups: c["player_groups"].as_object().map(|o| o.iter().map(|(k, v)| (k.clone(), s(v))).collect())
                .unwrap_or_default(),
            off_episodes: c["off_episodes"].as_array().map(|a| a.iter().map(slot_of).collect()).unwrap_or_default(),
            off_groups: c["off_groups"].as_array().map(|a| a.iter().map(s).collect()).unwrap_or_default(),
            off_compilations: c["off_compilations"].as_array().map(|a| a.iter().map(rid).collect()).unwrap_or_default(),
            reference: by_slot(&c["reference"], variants),
            reference_deny: by_slot(&c["reference_deny"], |x| variants(x).into_iter().collect()),
            voice_order: c["voice_order"].as_array().map(|a| a.iter().map(|p| ((slot_of(&p[0]), s(&p[1])),
                p[2].as_array().map(|x| x.iter().map(|e| (e[0].as_i64().unwrap_or(0), s(&e[1]))).collect())
                    .unwrap_or_default())).collect()).unwrap_or_default(),
            voice_deny: by_slot(&c["voice_deny"], |x| variants(x).into_iter().collect()),
            voice_off: by_slot(&c["voice_off"], |x| x.as_array().map(|a| a.iter().map(s).collect()).unwrap_or_default()),
            member: by_slot(&c["member"], opt_rid),
            titles: c["titles"].as_object().map(|o| o.iter().map(|(k, v)| (k.clone(), s(v))).collect()).unwrap_or_default(),
            languages: c["languages"].as_object().map(|o| o.iter().map(|(k, v)| (k.clone(), s(v))).collect())
                .unwrap_or_default(),
            markup: by_slot(&c["markup"], variants),
            markup_deny: by_slot(&c["markup_deny"], |x| variants(x).into_iter().collect()),
            same: by_slot(&c["same"], same_of),
            markup_same: by_slot(&c["markup_same"], same_of),
            subtitle_order: by_slot(&c["subtitle_order"], variants),
            subtitle_deny: by_slot(&c["subtitle_deny"], |x| variants(x).into_iter().collect()),
            ..Default::default()
        };
        for each in c["delete"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            match each[0].as_str() {
                Some("video") => {
                    choices.delete_videos.insert(s(&each[1]));
                }
                Some("track") => {
                    choices.delete_tracks.insert((s(&each[1]), keyed_row(&each[2])));
                }
                _ => {}
            }
        }
        let mut failed = Fails::default();
        for f in v["failed"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            match f[0].as_str() {
                Some("download") => failed.add(Failed::Download(keyed_row(&f[1]))),
                Some("align") => failed.add(Failed::Align(keyed_row(&f[1]), keyed_row(&f[2]))),
                Some("cut") => failed.add(Failed::Cut(keyed_row(&f[1]), keyed_row(&f[2]))),
                _ => {}
            }
        }
        let containers = v["containers"].as_array().map(|a| a.iter().map(|c| {
            let video = &c["video"];
            Container {
                name: s(&c["name"]),
                video: Video { id: rid(&video[0]), snapshot: shot(&video[1]), episode: slot_of(&video[2]),
                               timeline: rid(&video[3]), cut_of: opt_rid(&video[4]),
                               timeline_snapshot: if video[5].is_null() { None } else { Some(shot(&video[5])) } },
                tracks: c["tracks"].as_array().map(|t| t.iter().map(|t| Track { dub: rid(&t[0]), snapshot: shot(&t[1]),
                    episode: slot_of(&t[2]), aligned_to: rid(&t[3]) }).collect()).unwrap_or_default(),
                off: c["off"].as_bool().unwrap_or(false), titles: pairs(&c["titles"]), languages: pairs(&c["languages"]),
                subtitles: c["subtitles"].as_array().map(|x| x.iter().map(subtitle_of).collect()).unwrap_or_default(),
            }
        }).collect()).unwrap_or_default();
        Inputs {
            online: v["online"].as_array().map(|a| a.iter().map(online_of).collect()).unwrap_or_default(),
            subtitles: v["subtitles"].as_array().map(|a| a.iter().map(online_of).collect()).unwrap_or_default(),
            containers, choices, failed,
        }
    }
}

#[cfg(test)]
mod corpus {
    use super::*;

    /// The goals of every case of a corpus (`ST_PLAN_CORPUS`: files of plandump) against the goals of Python. Run by
    /// hand: `cargo test --release --no-default-features -- --ignored --nocapture corpus`.
    #[test]
    #[ignore]
    fn the_goals_of_the_corpus_are_those_of_python() {
        let Ok(dir) = std::env::var("ST_PLAN_CORPUS") else { return };
        let (mut same, mut differ, mut shown) = (0, 0, 0);
        let mut times: Vec<(String, f64, f64)> = Vec::new();
        let mut paths: Vec<_> = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).collect();
        paths.sort();
        for path in paths {
            let text = std::fs::read_to_string(&path).unwrap();
            let case: Value = serde_json::from_str(&text).unwrap();
            let inputs = Inputs::from_json(&case["inputs"]);
            let mut best = f64::MAX;
            let mut made = Vec::new();
            for _ in 0..3 {
                let began = std::time::Instant::now();
                let planner = Planner::new(&inputs);
                let goals = planner.goals();
                best = best.min(began.elapsed().as_secs_f64() * 1000.0);
                made = goals.iter().map(|g| g.json(&inputs)).collect::<Vec<_>>();
            }
            let expected = case["goals"].as_array().cloned().unwrap_or_default();
            let name = path.file_name().unwrap().to_string_lossy().to_string();
            if name.starts_with("real-") {
                times.push((name.clone(), best, case["python_ms"].as_f64().unwrap_or(0.0)));
            }
            if made == expected {
                same += 1;
            } else {
                differ += 1;
                if shown < 6 {
                    shown += 1;
                    println!("DIFFER {name}: goals {} vs {}", made.len(), expected.len());
                    for (m, e) in made.iter().zip(expected.iter()) {
                        if m != e {
                            println!("  rust:   {m}\n  python: {e}");
                            break;
                        }
                    }
                }
            }
        }
        for (name, rust, python) in &times {
            println!("TIME {name}: rust {rust:.2} ms, python {python:.0} ms");
        }
        println!("CORPUS same {same} differ {differ}");
    }
}
