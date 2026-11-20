//! What the window shows of the plan of a series (core/contracts/planner.PlannerView), counted from the goals the core
//! counted and from the inputs they were counted of: the asset, the goal of every episode, the reference of every episode
//! and why it is that one (core/plan/marks), the failed attempts placed by the rows, the commands that wait.

use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBytes, PyList, PyTuple};
use serde::Serialize;
use serde_json::value::RawValue;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;

use crate::plan::{voice_of, Container, Goal, Inputs, Online, Planner, RowId, Slot, Snapshot, Subtitle, Variant};
use crate::plan_py;

/// The goals of a series as the core counted them, with the inputs they were counted of: what the planner of Python makes
/// its goals of (`specs`), what the rule of names is asked (`items`), what the view is counted from (`plan_view`).
#[pyclass(frozen, module = "core.native", name = "Planned")]
pub struct Planned {
    // The goals hold places of the inputs boxed below, which nothing changes: declared first, they are dropped first.
    goals: Vec<Goal<'static>>,
    inputs: Box<Inputs>,
    // The files of the inputs as Python gave them: a file of the view that is one of them is not read again.
    files: Vec<PyObject>,
}

impl Planned {
    pub fn of(py: Python<'_>, given: &Bound<'_, PyAny>) -> PyResult<Planned> {
        let inputs = Box::new(plan_py::inputs(given)?);
        let files: Vec<PyObject> = given.getattr("containers")?.iter()?.map(|c| Ok(c?.unbind())).collect::<PyResult<_>>()?;
        let counted: Vec<Goal<'_>> = py.allow_threads(|| Planner::new(&inputs).goals());
        // SAFETY: the goals borrow `*inputs` (its rows and files), a box no method changes or moves out of, and they are
        // dropped before it (the order of the fields).
        let goals: Vec<Goal<'static>> = unsafe { std::mem::transmute(counted) };
        Ok(Planned { goals, inputs, files })
    }
}

#[pymethods]
impl Planned {
    /// The goals as places in the inputs (core/plan/planner `_goals`).
    fn specs<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let made = PyList::empty_bound(py);
        for goal in &self.goals {
            made.append(plan_py::goal_py(py, goal, &self.inputs))?;
        }
        Ok(made)
    }

    /// What the rule of names is asked of every goal: (slot, height, voice-over group, link of the video, whether the
    /// video is cut out of a compilation, whether the episode is switched off).
    fn items<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let made = PyList::empty_bound(py);
        for goal in &self.goals {
            let shot = goal.video.snapshot.get();
            let cut = goal.video.kind == "cut"
                || goal.video.container.map_or(false, |c| self.inputs.containers[c].video.cut_of.is_some());
            let item: (PyObject, i64, Option<&str>, &str, bool, bool) = (PyTuple::new_bound(py, &goal.slot).into_any().unbind(),
                shot.height, shot.group.as_deref(), shot.link.as_str(), cut, goal.off);
            made.append(item)?;
        }
        Ok(made)
    }

    fn __len__(&self) -> usize {
        self.goals.len()
    }
}

// --- a text of JSON ----------------------------------------------------------------------------------------------------

/// A value as a text of JSON (`__json__`): what the gateway writes out as it is; read by Python as the value it is (a dict,
/// a list), made of the text when first read.
#[pyclass(frozen, module = "core.native", name = "JsonText")]
pub struct JsonText {
    text: Py<PyBytes>,
    value: GILOnceCell<PyObject>,
}

impl JsonText {
    pub fn of(py: Python<'_>, text: &[u8]) -> JsonText {
        JsonText { text: PyBytes::new_bound(py, text).unbind(), value: GILOnceCell::new() }
    }

    fn value<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let made = self.value.get_or_try_init(py, || -> PyResult<PyObject> {
            Ok(py.import_bound("orjson")?.call_method1("loads", (self.text.bind(py),))?.unbind())
        })?;
        Ok(made.bind(py).clone())
    }
}

#[pymethods]
impl JsonText {
    fn __json__<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        self.text.bind(py).clone()
    }
    fn __getitem__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        self.value(py)?.get_item(key)
    }
    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyIterator>> {
        self.value(py)?.iter()
    }
    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        self.value(py)?.len()
    }
    fn __contains__(&self, py: Python<'_>, key: &Bound<'_, PyAny>) -> PyResult<bool> {
        self.value(py)?.contains(key)
    }
    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        match other.downcast::<JsonText>() {
            Ok(other) => Ok(other.get().text.bind(py).as_bytes() == self.text.bind(py).as_bytes()
                || other.get().value(py)?.eq(self.value(py)?)?),
            Err(_) => self.value(py)?.eq(other),
        }
    }
    /// What the value is read as besides its items: `keys`, `items`, `get` of a dict.
    fn __getattr__<'py>(&self, py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        self.value(py)?.getattr(name)
    }
    fn __repr__(&self, py: Python<'_>) -> String {
        format!("JsonText({} bytes)", self.text.bind(py).as_bytes().len())
    }
}

// --- the view ------------------------------------------------------------------------------------------------------------
// The view is written as the models of core/contracts/planner.py are dumped, field for field in their order, of the data
// of the core as it is: nothing is copied into a tree of values first.

#[derive(Serialize)]
struct RowOut<'a> {
    source_id: i64,
    row_key: &'a str,
    video: &'a str,
}

fn row(id: &RowId) -> RowOut<'_> {
    RowOut { source_id: id.source, row_key: &id.key, video: &id.video }
}

#[derive(Serialize)]
struct VariantOut<'a> {
    group: &'a str,
    source_id: i64,
    player: &'a str,
}

fn variant(found: &Variant) -> VariantOut<'_> {
    VariantOut { group: &found.0, source_id: found.1, player: &found.2 }
}

#[derive(Serialize)]
struct SubtitleOut<'a> {
    row: RowOut<'a>,
    name: &'a str,
    player: &'a str,
    language: &'a str,
    subtitle_type: &'a str,
    default: bool,
    forced: bool,
    kind: Option<&'a str>,
}

fn subtitle<'a>(item: &'a Subtitle, kind: Option<&'a str>) -> SubtitleOut<'a> {
    SubtitleOut { row: row(&item.row), name: &item.name, player: &item.player, language: &item.language,
                  subtitle_type: &item.subtitle_type, default: item.default, forced: item.forced, kind }
}

#[derive(Serialize)]
struct AssetVideoOut<'a> {
    source: &'a str,
    player: &'a str,
    group: Option<&'a str>,
    height: i64,
    link: &'a str,
    label: &'a str,
    row: RowOut<'a>,
    cut_of: Option<RowOut<'a>>,
    online: Option<bool>,
}

#[derive(Serialize)]
struct AssetTrackOut<'a> {
    source: &'a str,
    player: &'a str,
    group: Option<&'a str>,
    height: i64,
    link: &'a str,
    label: &'a str,
    row: RowOut<'a>,
    online: Option<bool>,
}

#[derive(Serialize)]
struct AssetOut<'a> {
    name: &'a str,
    off: bool,
    episode: &'a [i64],
    video: AssetVideoOut<'a>,
    tracks: Vec<AssetTrackOut<'a>>,
    timeline: Option<TimelineOut>,
    subtitles: Vec<SubtitleOut<'a>>,
}

#[derive(Serialize)]
struct TimelineOut {
    group: String,
    source_id: i64,
    player: String,
}

#[derive(Serialize)]
struct GoalVideoOut<'a> {
    source: &'a str,
    player: &'a str,
    group: Option<&'a str>,
    height: i64,
    link: &'a str,
    label: &'a str,
    kind: &'a str,
    row: RowOut<'a>,
    file: Option<&'a str>,
    cut_of: Option<RowOut<'a>>,
}

#[derive(Serialize)]
struct GoalTrackOut<'a> {
    source: &'a str,
    player: &'a str,
    group: Option<&'a str>,
    height: i64,
    link: &'a str,
    label: &'a str,
    voice: &'a str,
    kind: &'a str,
    row: RowOut<'a>,
    own: bool,
}

#[derive(Serialize)]
struct EpisodeOut<'a> {
    slot: &'a [i64],
    name: &'a str,
    off: bool,
    video: GoalVideoOut<'a>,
    tracks: Vec<GoalTrackOut<'a>>,
    voices_off: Vec<&'a str>,
    markup: Option<String>,
    markup_variant: Option<VariantOut<'a>>,
    subtitles: Vec<SubtitleOut<'a>>,
    unaligned: Vec<VariantOut<'a>>,
}

#[derive(Serialize)]
struct FailureOut<'a> {
    kind: &'a str,
    row: RowOut<'a>,
    video: Option<RowOut<'a>>,
    episodes: &'a [Slot],
    voice: Option<&'a str>,
    variant: Option<VariantOut<'a>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    covered: Option<bool>,
}

#[derive(Serialize)]
struct CommandOut<'a> {
    kind: &'a str,
    name: &'a str,
    dub: Option<RowOut<'a>>,
}

#[derive(Serialize)]
struct PictureOut<'a> {
    source_id: i64,
    player: &'a str,
    height: i64,
    voice_group: Option<&'a str>,
}

#[derive(Serialize)]
struct ReferenceOut<'a> {
    season: i64,
    episode: i64,
    part: Option<i64>,
    mark: &'a str,
    source_id: Option<i64>,
    row_key: Option<&'a str>,
    player: Option<&'a str>,
    voice_group: Option<&'a str>,
    height: Option<i64>,
    reason: Option<String>,
    compilation: Option<&'a Value>,
    picture: Option<PictureOut<'a>>,
    markup: bool,
}

#[derive(Serialize)]
struct ViewOut<'a> {
    series_id: i64,
    running: bool,
    references: &'a RawValue,
    assets: Vec<AssetOut<'a>>,
    episodes: Vec<EpisodeOut<'a>>,
    failures: Vec<FailureOut<'a>>,
    problems: &'a RawValue,
    commands: Vec<CommandOut<'a>>,
}

/// What the timeline a file lies on is (core/plan/planner `timeline_shot`).
fn timeline_shot(container: &Container) -> Option<&Snapshot> {
    let video = &container.video;
    if video.cut_of.is_none() {
        return Some(&video.snapshot);
    }
    video.timeline_snapshot.as_ref()
        .or_else(|| container.tracks.iter().find(|t| t.dub == video.timeline).map(|t| &t.snapshot))
}

/// The variant of the queue of the markup reference whose timeline a file lies on (`timeline_variant`).
fn timeline_variant(container: &Container) -> Option<Variant> {
    let shot = timeline_shot(container)?;
    shot.group.as_ref()?;
    Some((shot.variant_label().to_string(), container.video.timeline.source, shot.player.clone()))
}

/// A failed attempt with where it belongs, as the layout the rows are of has it (modules/planner/view `placed`).
struct Placed<'a> {
    failure: &'a Failure,
    episodes: Vec<Slot>,
    voice: Option<&'a str>,
    variant: Option<&'a Variant>,
}

impl<'a> Placed<'a> {
    fn out(&'a self, covered: Option<bool>) -> FailureOut<'a> {
        let failure = self.failure;
        FailureOut { kind: &failure.kind, row: row(&failure.row), video: failure.video.as_ref().map(row),
                     episodes: &self.episodes, voice: self.voice, variant: self.variant.map(variant), covered }
    }
}

/// A failed attempt as Python gave it: its key, its kind and rows, and the `repr` of the key it is ordered by.
struct Failure {
    key: PyObject,
    repr: String,
    kind: String,
    row: RowId,
    video: Option<RowId>,
}

fn failures_of(failed: &Bound<'_, PyAny>) -> PyResult<Vec<Failure>> {
    let mut found: Vec<Failure> = failed.iter()?.map(|key| {
        let key = key?;
        Ok(Failure { repr: key.repr()?.extract()?, kind: key.get_item(0)?.extract()?, row: plan_py::rid(&key.get_item(1)?)?,
                     video: if key.len()? > 2 { Some(plan_py::rid(&key.get_item(2)?)?) } else { None }, key: key.unbind() })
    }).collect::<PyResult<_>>()?;
    found.sort_by(|a, b| a.repr.cmp(&b.repr));
    Ok(found)
}

fn slots_of(row: &Online) -> Vec<Slot> {
    match row.part {
        Some(part) => vec![vec![row.season, row.start, part]],
        None => (row.start..=row.end).map(|episode| vec![row.season, episode]).collect(),
    }
}

/// The failed attempts (in the order of the `repr` of their keys) with the episodes their row gives and its group.
fn placed<'a>(failed: &'a [Failure], rows: &'a [Arc<Online>]) -> Vec<Placed<'a>> {
    let mut by_id: HashMap<&RowId, Vec<&Online>> = HashMap::new();
    for each in rows {
        by_id.entry(&each.id).or_default().push(each);
    }
    failed.iter().map(|failure| {
        let held: &[&Online] = by_id.get(&failure.row).map(|found| found.as_slice()).unwrap_or(&[]);
        let episodes: BTreeSet<Slot> = held.iter().flat_map(|row| slots_of(row)).collect();
        Placed { failure, episodes: episodes.into_iter().collect(),
                 voice: held.iter().find(|row| !row.group.is_empty()).map(|row| row.group.as_str()),
                 variant: held.first().map(|row| row.variant()) }
    }).collect()
}

/// What a file of the asset holds, for the failures it covers: its episode, whether it is hidden, its voice-overs.
struct Holds<'a> {
    episode: &'a [i64],
    off: bool,
    groups: Vec<Option<&'a str>>,
}

/// A failed attempt the series does not lack (modules/planner/view `covered`): every episode of its row has a visible file
/// that holds its voice-over, as a track or as the own sound of the video, or the video when the row names none.
fn covered(episodes: &[Slot], voice: Option<&str>, assets: &[Holds<'_>]) -> bool {
    episodes.iter().all(|slot| {
        let held: Vec<&Holds> = assets.iter().filter(|each| !each.off && each.episode == slot.as_slice()).collect();
        !held.is_empty() && voice.map_or(true, |voice| held.iter().any(|each| each.groups.iter().any(|g| *g == Some(voice))))
    })
}

/// `core.reasons.reason`: the code and its parameters as one JSON text, in their order.
fn reason(code: &str, params: &[(&str, Value)]) -> String {
    let mut text = format!("{{\"code\":{}", Value::from(code));
    for (name, value) in params {
        text.push_str(&format!(",\"{name}\":{value}"));
    }
    text.push('}');
    text
}

fn episode_of(slot: &Slot) -> Slot {
    slot[..2].to_vec()
}

fn some(text: &str) -> Option<&str> {
    if text.is_empty() { None } else { Some(text) }
}

/// The compilations of the layout by their row (source id, row key), as `CompilationRef` dumps them.
type Members = HashMap<(i64, String), Value>;

fn members_of(text: Option<&[u8]>) -> PyResult<Members> {
    let Some(text) = text else { return Ok(Members::new()) };
    let found: Vec<(i64, String, Value)> = serde_json::from_slice(text)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok(found.into_iter().map(|(source, key, value)| ((source, key), value)).collect())
}

/// The reference of every place the layout or the asset has, in the order of season, episode, part (core/plan/marks), as
/// the text of a list of `Reference`.
fn references(planned: &Planned, members: &Members) -> Vec<u8> {
    let inputs = &*planned.inputs;
    let choices = &inputs.choices;
    let mut online: HashMap<&RowId, &Online> = HashMap::new();
    let mut singles: HashMap<Slot, Vec<&Online>> = HashMap::new();
    let mut covering: HashSet<(i64, i64)> = HashSet::new();
    for each in &inputs.online {
        online.insert(&each.id, each);
        if each.compilation() {
            if each.playable {
                covering.extend((each.start..=each.end).map(|number| (each.season, number)));
            }
        } else {
            singles.entry(each.slot()).or_default().push(each);
        }
    }
    let goals: HashMap<&Slot, &Goal> = planned.goals.iter().map(|goal| (&goal.slot, goal)).collect();
    let mut slots: BTreeSet<Slot> = singles.keys().cloned().chain(goals.keys().map(|slot| (*slot).clone())).collect();
    for (season, episode) in &covering {
        if !singles.keys().any(|key| key[0] == *season && key[1] == *episode) {
            slots.insert(vec![*season, *episode]);
        }
    }
    let mut found: Vec<ReferenceOut> = Vec::with_capacity(slots.len());
    for slot in &slots {
        let episode = episode_of(slot);
        let part = slot.get(2).copied();
        let Some(goal) = goals.get(slot) else {
            let mark = if choices.off_episodes.contains(&episode) { "off" }
                else if covering.contains(&(slot[0], slot[1])) { "unfit" } else { "none" };
            let why = match mark {
                "off" => None,
                "unfit" => Some(reason("reference_unfit", &[])),
                _ => Some(reason("reference_none", &[])),
            };
            found.push(ReferenceOut { season: slot[0], episode: slot[1], part, mark, source_id: None, row_key: None,
                player: None, voice_group: None, height: None, reason: why, compilation: None, picture: None, markup: false });
            continue;
        };
        let video = &goal.video;
        let single = if video.kind == "cut" { video.ref2.as_deref().expect("a cut has its single video") } else { video };
        let row = online.get(&single.timeline).copied();
        let snapshot = single.snapshot.get();
        let taken: Variant = (snapshot.variant_label().to_string(), single.timeline.source, snapshot.player.clone());
        let mut mark = if choices.own_reference.contains(&episode) { "own" } else { "by_level" };
        let mut why: Option<String> = None;
        let denied = choices.reference_deny.get(&episode);
        let wanted = choices.reference.get(&episode)
            .and_then(|queue| queue.iter().find(|each| denied.map_or(true, |d| !d.contains(*each))));
        let same = choices.same.get(&episode);
        let members_of = |v: &Variant| -> Vec<Variant> { same.map_or_else(|| vec![v.clone()], |s| s.members(v)) };
        let equals = members_of(&taken);
        let lost: Vec<&Online> = singles.get(slot).map(|rows| rows.iter().copied()
            .filter(|each| inputs.failed.download(&each.id) && !equals.contains(each.variant())).collect()).unwrap_or_default();
        if goal.off {
            mark = "off";
        } else if !lost.is_empty() && lost.iter().all(|each| each.id != single.timeline) {
            let head = lost[0];
            mark = "other_player";
            why = Some(reason("reference_download_failed", &[
                ("taken", snapshot.player.clone().into()), ("failed", head.player.clone().into()),
                ("taken_source", single.timeline.source.into()), ("failed_source", head.id.source.into())]));
        } else if let Some(wanted) = wanted.filter(|wanted| !members_of(wanted).contains(&taken)) {
            mark = "other_player";
            why = Some(reason("reference_other_player", &[
                ("taken", snapshot.player.clone().into()), ("missing", wanted.2.clone().into()),
                ("taken_source", single.timeline.source.into()), ("missing_source", wanted.1.into())]));
        }
        let held = video.container.map(|c| &inputs.containers[c]);
        let cut_of: Option<&RowId> = if video.kind == "cut" { video.row.map(|r| &inputs.online[r].id) }
            else { held.and_then(|c| c.video.cut_of.as_ref()) };
        // The picture of a cut video: the row it is cut out of, as the plan has it now or as its file was made.
        let picture = cut_of.map(|cut| {
            let shot = if video.kind == "cut" { video.snapshot.get() } else { &held.expect("a file").video.snapshot };
            PictureOut { source_id: cut.source, player: &shot.player, height: shot.height, voice_group: some(shot.variant_label()) }
        });
        let compilation = cut_of.and_then(|cut| members.get(&(cut.source, cut.key.clone())));
        let (player, voice_group, height) = match row {
            Some(row) => (row.player.as_str(), Some(if row.label.is_empty() { row.group.as_str() } else { row.label.as_str() }),
                          row.height),
            None => (snapshot.player.as_str(), some(snapshot.variant_label()), snapshot.height),
        };
        let markup = goal.markup.as_ref().map_or(false, |m| single.timeline == m.id);
        found.push(ReferenceOut { season: slot[0], episode: slot[1], part, mark, source_id: Some(single.timeline.source),
            row_key: Some(&single.timeline.key), player: Some(player), voice_group, height: Some(height), reason: why,
            compilation, picture, markup });
    }
    serde_json::to_vec(&found).expect("a list of references")
}

/// The reference of every episode (core/plan/marks `references_of`), as `Reference` dumps them: `members` the JSON of
/// [[source id, row key, compilation]].
#[pyfunction]
#[pyo3(signature = (planned, members=None))]
pub fn plan_references(py: Python<'_>, planned: &Planned, members: Option<&[u8]>) -> PyResult<JsonText> {
    let members = members_of(members)?;
    let made = py.allow_threads(|| references(planned, &members));
    Ok(JsonText::of(py, &made))
}

/// The failed attempts with where each belongs (modules/planner/view `placed`): each with its `key`.
#[pyfunction]
pub fn plan_placed<'py>(py: Python<'py>, failed: &Bound<'py, PyAny>, rows: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let rows = crate::plan_inputs::rows_of(rows)?;
    let failures = failures_of(failed)?;
    let found = PyList::empty_bound(py);
    let loads = py.import_bound("orjson")?.getattr("loads")?;
    for each in placed(&failures, &rows) {
        let text = serde_json::to_vec(&each.out(None)).expect("a failure");
        let made = loads.call1((PyBytes::new_bound(py, &text),))?;
        made.set_item("key", each.failure.key.bind(py))?;
        found.append(made)?;
    }
    Ok(found)
}

/// Whether a failure (`episodes`, `voice`, as the view places it) is covered by the asset (the files as the view shows
/// them: `episode`, `off`, the `group` of the video and of each track).
#[pyfunction]
pub fn plan_covered(failure: &Bound<'_, PyAny>, assets: &Bound<'_, PyAny>) -> PyResult<bool> {
    let episodes: Vec<Slot> = failure.get_item("episodes")?.extract()?;
    let voice: Option<String> = failure.get_item("voice")?.extract()?;
    let mut read: Vec<(Vec<i64>, bool, Vec<Option<String>>)> = Vec::new();
    for each in assets.iter()? {
        let each = each?;
        let mut groups = vec![each.get_item("video")?.call_method1("get", ("group",))?.extract::<Option<String>>()?];
        for track in each.get_item("tracks")?.iter()? {
            groups.push(track?.call_method1("get", ("group",))?.extract()?);
        }
        read.push((each.get_item("episode")?.extract()?, each.get_item("off")?.extract()?, groups));
    }
    let holds: Vec<Holds> = read.iter().map(|(episode, off, groups)| Holds { episode, off: *off,
        groups: groups.iter().map(|g| g.as_deref()).collect() }).collect();
    Ok(covered(&episodes, voice.as_deref(), &holds))
}

/// A command «Удалить» as Python gave it, with the `repr` it is ordered by.
struct Command {
    repr: String,
    kind: String,
    name: String,
    dub: Option<RowId>,
}

/// The view of the window (core/contracts/planner.PlannerView, as `model_dump(mode="json")` gives it), as a text of JSON,
/// with the references alone and what the card of the series is told of the view: (view, references, (ready, work,
/// error)). `containers` the asset, `failed` the attempts recorded, `commands` the commands «Удалить», `problems` the JSON
/// of the steps of the last sync that failed for the moment (dumped); `planned` the goals the plan counted and `names` the
/// name of each, none when no plan was counted; `members` the JSON of [[source id, row key, compilation]].
#[pyfunction]
#[pyo3(signature = (series_id, running, containers, failed, commands, problems, planned=None, names=None, members=None))]
#[allow(clippy::too_many_arguments)]
pub fn plan_view(py: Python<'_>, series_id: i64, running: bool, containers: &Bound<'_, PyAny>, failed: &Bound<'_, PyAny>,
                 commands: &Bound<'_, PyAny>, problems: &[u8], planned: Option<&Planned>, names: Option<Vec<String>>,
                 members: Option<&[u8]>) -> PyResult<(JsonText, JsonText, (bool, bool, bool))> {
    // A file the plan was counted with is the one read then; another one (a sync under way changed it) is read now.
    let known: HashMap<usize, usize> = planned.map(|p| p.files.iter().enumerate().map(|(at, f)| (f.as_ptr() as usize, at))
        .collect()).unwrap_or_default();
    let mut read: Vec<Container> = Vec::new();
    let mut files: Vec<(bool, usize)> = Vec::new();
    for each in containers.iter()? {
        let each = each?;
        match known.get(&(each.as_ptr() as usize)) {
            Some(at) => files.push((true, *at)),
            None => {
                read.push(plan_py::container(&each)?);
                files.push((false, read.len() - 1));
            }
        }
    }
    let failures = failures_of(failed)?;
    let mut waiting: Vec<Command> = commands.iter()?.map(|each| {
        let each = each?;
        Ok(Command { repr: each.repr()?.extract()?, kind: each.get_item(0)?.extract()?, name: each.get_item(1)?.extract()?,
                     dub: if each.len()? > 2 { Some(plan_py::rid(&each.get_item(2)?)?) } else { None } })
    }).collect::<PyResult<_>>()?;
    waiting.sort_by(|a, b| a.repr.cmp(&b.repr));
    let problems: Box<RawValue> = serde_json::from_slice(problems)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let members = members_of(members)?;
    let (view, references, flags) = py.allow_threads(|| {
        view_of(series_id, running, &files, &read, &failures, &waiting, &problems, planned, names.as_deref(), &members)
    });
    Ok((JsonText::of(py, &view), JsonText::of(py, &references), flags))
}

#[allow(clippy::too_many_arguments)]
fn view_of(series_id: i64, running: bool, files: &[(bool, usize)], read: &[Container], failures: &[Failure],
           waiting: &[Command], problems: &RawValue, planned: Option<&Planned>, names: Option<&[String]>, members: &Members)
           -> (Vec<u8>, Vec<u8>, (bool, bool, bool)) {
    let mut asset: Vec<&Container> = files.iter()
        .map(|(theirs, at)| if *theirs { &planned.expect("a plan").inputs.containers[*at] } else { &read[*at] }).collect();
    asset.sort_by(|a, b| a.name.cmp(&b.name));
    // Online is what the sources give now: a row that stands from an earlier read is not.
    let online: Option<HashSet<&RowId>> = planned.map(|p| p.inputs.online.iter().filter(|r| r.current).map(|r| &r.id).collect());
    let is_online = |id: &RowId| online.as_ref().map(|set| set.contains(id));

    let assets: Vec<AssetOut> = asset.iter().map(|container| {
        let video = &container.video;
        let shot = &video.snapshot;
        AssetOut {
            name: &container.name, off: container.off, episode: &video.episode,
            video: AssetVideoOut { source: &shot.source, player: &shot.player, group: shot.group.as_deref(), height: shot.height,
                link: &shot.link, label: &shot.label, row: row(&video.id), cut_of: video.cut_of.as_ref().map(row),
                online: is_online(&video.id) },
            tracks: container.tracks.iter().map(|track| {
                let shot = &track.snapshot;
                AssetTrackOut { source: &shot.source, player: &shot.player, group: shot.group.as_deref(), height: shot.height,
                    link: &shot.link, label: &shot.label, row: row(&track.dub), online: is_online(&track.dub) }
            }).collect(),
            timeline: timeline_variant(container).map(|(group, source_id, player)| TimelineOut { group, source_id, player }),
            subtitles: container.subtitles.iter().map(|item| subtitle(item, None)).collect(),
        }
    }).collect();

    let mut episodes = Vec::new();
    let mut work = !waiting.is_empty();
    if let (Some(planned), Some(names)) = (planned, names) {
        let inputs = &*planned.inputs;
        for (goal, name) in planned.goals.iter().zip(names) {
            let way = &goal.video;
            let held = way.container.map(|c| &inputs.containers[c]);
            let shot = way.snapshot.get();
            let cut_of = if way.kind == "cut" { way.row.map(|r| &inputs.online[r].id) }
                else { held.and_then(|c| c.video.cut_of.as_ref()) };
            // What the card is told: the plan has work for the next sync.
            work |= way.kind != "asset" || goal.tracks.iter().any(|track| track.kind != "reuse");
            let mut voices_off: Vec<&str> = goal.voices_off.iter().map(String::as_str).collect();
            voices_off.sort();
            episodes.push(EpisodeOut {
                slot: &goal.slot, name, off: goal.off,
                video: GoalVideoOut { source: &shot.source, player: &shot.player, group: shot.group.as_deref(),
                    height: shot.height, link: &shot.link, label: &shot.label, kind: way.kind, row: row(&way.timeline),
                    file: held.map(|c| c.name.as_str()), cut_of: cut_of.map(row) },
                tracks: goal.tracks.iter().map(|track| {
                    let shot = track.snapshot.get();
                    GoalTrackOut { source: &shot.source, player: &shot.player, group: shot.group.as_deref(), height: shot.height,
                        link: &shot.link, label: &shot.label, voice: &track.voice, kind: track.kind, row: row(&track.dub),
                        own: track.own }
                }).collect(),
                voices_off,
                markup: goal.markup.as_ref().map(|m| voice_of(m.group.as_deref(), &m.id)),
                markup_variant: goal.markup.as_ref().and_then(|m| m.variant.as_ref()).map(variant),
                subtitles: goal.subtitles.iter().map(|way| subtitle(&way.subtitle, Some(way.kind))).collect(),
                unaligned: goal.unaligned.iter().map(variant).collect(),
            });
        }
    }

    let holds: Vec<Holds> = asset.iter().map(|c| Holds {
        episode: &c.video.episode, off: c.off,
        groups: std::iter::once(c.video.snapshot.group.as_deref()).chain(c.tracks.iter().map(|t| t.snapshot.group.as_deref()))
            .collect(),
    }).collect();
    let rows: &[Arc<Online>] = planned.map(|p| p.inputs.online.as_slice()).unwrap_or(&[]);
    let placed = placed(failures, rows);
    let mut lacking = false;
    let shown_failures: Vec<FailureOut> = placed.iter().map(|each| {
        let held = covered(&each.episodes, each.voice, &holds);
        lacking |= !held;
        each.out(Some(held))
    }).collect();
    let commands: Vec<CommandOut> = waiting.iter()
        .map(|each| CommandOut { kind: &each.kind, name: &each.name, dub: each.dub.as_ref().map(row) }).collect();
    let references = match (planned, names) {
        (Some(planned), Some(_)) => references(planned, members),
        _ => b"[]".to_vec(),
    };
    let referenced: &RawValue = serde_json::from_slice(&references).expect("the text of the references");
    let failing = serde_json::from_str::<Vec<&RawValue>>(problems.get()).map_or(false, |found| !found.is_empty());
    let flags = (!assets.is_empty(), work, lacking || failing);
    let view = ViewOut { series_id, running, references: referenced, assets, episodes, failures: shown_failures,
                         problems, commands };
    (serde_json::to_vec(&view).expect("a view"), references, flags)
}
