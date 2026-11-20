//! What the window shows of the plan of a series (core/contracts/planner.PlannerView), counted from the goals the core
//! counted and from the inputs they were counted of: the asset, the goal of every episode, the reference of every episode
//! and why it is that one (core/plan/marks), the failed attempts placed by the rows, the commands that wait.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
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

macro_rules! dict {
    ($py:expr, { $($key:literal : $value:expr),* $(,)? }) => {{
        let made = PyDict::new_bound($py);
        $( made.set_item(pyo3::intern!($py, $key), $value)?; )*
        made
    }};
}

fn row<'py>(py: Python<'py>, id: &RowId) -> PyResult<Bound<'py, PyDict>> {
    Ok(dict!(py, {"source_id": id.source, "row_key": id.key.as_str(), "video": id.video.as_str()}))
}

fn opt_row(py: Python<'_>, id: Option<&RowId>) -> PyResult<PyObject> {
    Ok(match id {
        Some(id) => row(py, id)?.into_any().unbind(),
        None => py.None(),
    })
}

fn shot_into(made: &Bound<'_, PyDict>, shot: &Snapshot) -> PyResult<()> {
    made.set_item(pyo3::intern!(made.py(), "source"), shot.source.as_str())?;
    made.set_item(pyo3::intern!(made.py(), "player"), shot.player.as_str())?;
    made.set_item(pyo3::intern!(made.py(), "group"), shot.group.as_deref())?;
    made.set_item(pyo3::intern!(made.py(), "height"), shot.height)?;
    made.set_item(pyo3::intern!(made.py(), "link"), shot.link.as_str())?;
    made.set_item(pyo3::intern!(made.py(), "label"), shot.label.as_str())
}

fn variant(py: Python<'_>, found: Option<&Variant>) -> PyResult<PyObject> {
    Ok(match found {
        Some((group, source, player)) => dict!(py, {"group": group.as_str(), "source_id": *source, "player": player.as_str()})
            .into_any().unbind(),
        None => py.None(),
    })
}

fn subtitle<'py>(py: Python<'py>, item: &Subtitle, kind: Option<&str>) -> PyResult<Bound<'py, PyDict>> {
    Ok(dict!(py, {"row": row(py, &item.row)?, "name": item.name.as_str(), "player": item.player.as_str(),
                  "language": item.language.as_str(), "subtitle_type": item.subtitle_type.as_str(),
                  "default": item.default, "forced": item.forced, "kind": kind}))
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
struct Placed {
    key: PyObject,
    kind: String,
    row: RowId,
    video: Option<RowId>,
    episodes: Vec<Slot>,
    voice: Option<String>,
    variant: Option<Variant>,
}

fn slots_of(row: &Online) -> Vec<Slot> {
    match row.part {
        Some(part) => vec![vec![row.season, row.start, part]],
        None => (row.start..=row.end).map(|episode| vec![row.season, episode]).collect(),
    }
}

/// The failed attempts (keys of Python, in the order of their `repr`) with the episodes their row gives and its group.
fn placed(failed: &Bound<'_, PyAny>, rows: &[Arc<Online>]) -> PyResult<Vec<Placed>> {
    let mut by_id: HashMap<&RowId, Vec<&Online>> = HashMap::new();
    for each in rows {
        by_id.entry(&each.id).or_default().push(each);
    }
    let mut keys: Vec<(String, Bound<'_, PyAny>)> = failed.iter()?
        .map(|key| { let key = key?; Ok((key.repr()?.extract::<String>()?, key)) }).collect::<PyResult<_>>()?;
    keys.sort_by(|a, b| a.0.cmp(&b.0));
    keys.into_iter().map(|(_, key)| {
        let id = plan_py::rid(&key.get_item(1)?)?;
        let video = if key.len()? > 2 { Some(plan_py::rid(&key.get_item(2)?)?) } else { None };
        let held: &[&Online] = by_id.get(&id).map(|each| each.as_slice()).unwrap_or(&[]);
        let episodes: BTreeSet<Slot> = held.iter().flat_map(|each| slots_of(each)).collect();
        Ok(Placed {
            kind: key.get_item(0)?.extract()?, video, episodes: episodes.into_iter().collect(),
            voice: held.iter().find(|each| !each.group.is_empty()).map(|each| each.group.clone()),
            variant: held.first().map(|each| each.variant().clone()), row: id, key: key.unbind(),
        })
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
fn reason(code: &str, params: &[(&str, serde_json::Value)]) -> String {
    let mut text = format!("{{\"code\":{}", serde_json::to_string(code).expect("a text"));
    for (name, value) in params {
        text.push_str(&format!(",\"{name}\":{}", serde_json::to_string(value).expect("a value")));
    }
    text.push('}');
    text
}

fn episode_of(slot: &Slot) -> Slot {
    slot[..2].to_vec()
}

/// The reference of every place the layout or the asset has, in the order of season, episode, part (core/plan/marks).
fn references<'py>(py: Python<'py>, planned: &Planned, members: &Bound<'py, PyDict>) -> PyResult<Bound<'py, PyList>> {
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
    let found = PyList::empty_bound(py);
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
            found.append(dict!(py, {"season": slot[0], "episode": slot[1], "part": part, "mark": mark,
                "source_id": py.None(), "row_key": py.None(), "player": py.None(), "voice_group": py.None(),
                "height": py.None(), "reason": why, "compilation": py.None(), "picture": py.None(), "markup": false}))?;
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
        let picture: PyObject = match cut_of {
            Some(cut) => {
                let shot = if video.kind == "cut" { video.snapshot.get() } else { &held.expect("a file").video.snapshot };
                let label = shot.variant_label();
                dict!(py, {"source_id": cut.source, "player": shot.player.as_str(), "height": shot.height,
                           "voice_group": if label.is_empty() { None } else { Some(label) }}).into_any().unbind()
            }
            None => py.None(),
        };
        let compilation: PyObject = match cut_of {
            Some(cut) => match members.get_item((cut.source, cut.key.as_str()))? {
                Some(found) => found.unbind(),
                None => py.None(),
            },
            None => py.None(),
        };
        let label = snapshot.variant_label();
        let (player, voice_group, height) = match row {
            Some(row) => (row.player.as_str(), Some(if row.label.is_empty() { row.group.as_str() } else { row.label.as_str() }),
                          row.height),
            None => (snapshot.player.as_str(), if label.is_empty() { None } else { Some(label) }, snapshot.height),
        };
        let markup = goal.markup.as_ref().map_or(false, |m| single.timeline == m.id);
        found.append(dict!(py, {"season": slot[0], "episode": slot[1], "part": part, "mark": mark,
            "source_id": single.timeline.source, "row_key": single.timeline.key.as_str(), "player": player,
            "voice_group": voice_group, "height": height, "reason": why, "compilation": compilation, "picture": picture,
            "markup": markup}))?;
    }
    Ok(found)
}

/// The reference of every episode (core/plan/marks `references_of`), as `Reference` dumps it.
#[pyfunction]
pub fn plan_references<'py>(py: Python<'py>, planned: &Planned, members: &Bound<'py, PyDict>) -> PyResult<Bound<'py, PyList>> {
    references(py, planned, members)
}

fn placed_py<'py>(py: Python<'py>, each: &Placed) -> PyResult<Bound<'py, PyDict>> {
    let episodes = PyList::empty_bound(py);
    for slot in &each.episodes {
        episodes.append(PyList::new_bound(py, slot))?;
    }
    Ok(dict!(py, {"kind": each.kind.as_str(), "row": row(py, &each.row)?, "video": opt_row(py, each.video.as_ref())?,
                  "episodes": episodes, "voice": each.voice.as_deref(), "variant": variant(py, each.variant.as_ref())?}))
}

/// The failed attempts with where each belongs (modules/planner/view `placed`): each with its `key`.
#[pyfunction]
pub fn plan_placed<'py>(py: Python<'py>, failed: &Bound<'py, PyAny>, rows: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let rows = crate::plan_inputs::rows_of(rows)?;
    let found = PyList::empty_bound(py);
    for each in placed(failed, &rows)? {
        let made = placed_py(py, &each)?;
        made.set_item(pyo3::intern!(made.py(), "key"), each.key.bind(py))?;
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

/// The view of the window (core/contracts/planner.PlannerView, as `model_dump(mode="json")` gives it): `containers` the
/// asset, `failed` the attempts recorded, `commands` the commands «Удалить», `problems` the steps of the last sync that
/// failed for the moment (dumped); `planned` the goals the plan counted and `names` the name of each, none when no plan was
/// counted; `members` the compilations of the layout by their row (source id, row key), dumped.
#[pyfunction]
#[pyo3(signature = (series_id, running, containers, failed, commands, problems, planned=None, names=None, members=None))]
#[allow(clippy::too_many_arguments)]
pub fn plan_view<'py>(py: Python<'py>, series_id: i64, running: bool, containers: &Bound<'py, PyAny>,
                      failed: &Bound<'py, PyAny>, commands: &Bound<'py, PyAny>, problems: &Bound<'py, PyAny>,
                      planned: Option<&Planned>, names: Option<Vec<String>>, members: Option<&Bound<'py, PyDict>>)
                      -> PyResult<Bound<'py, PyDict>> {
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
    let mut asset: Vec<&Container> = files.iter()
        .map(|(theirs, at)| if *theirs { &planned.expect("a plan").inputs.containers[*at] } else { &read[*at] }).collect();
    asset.sort_by(|a, b| a.name.cmp(&b.name));
    // Online is what the sources give now: a row that stands from an earlier read is not.
    let online: Option<HashSet<&RowId>> = planned.map(|p| p.inputs.online.iter().filter(|r| r.current).map(|r| &r.id).collect());
    let is_online = |id: &RowId| online.as_ref().map(|set| set.contains(id));

    let assets = PyList::empty_bound(py);
    for container in &asset {
        let video = &container.video;
        let shown = dict!(py, {});
        shot_into(&shown, &video.snapshot)?;
        shown.set_item(pyo3::intern!(shown.py(), "row"), row(py, &video.id)?)?;
        shown.set_item(pyo3::intern!(shown.py(), "cut_of"), opt_row(py, video.cut_of.as_ref())?)?;
        shown.set_item(pyo3::intern!(shown.py(), "online"), is_online(&video.id))?;
        let tracks = PyList::empty_bound(py);
        for track in &container.tracks {
            let made = dict!(py, {});
            shot_into(&made, &track.snapshot)?;
            made.set_item(pyo3::intern!(made.py(), "row"), row(py, &track.dub)?)?;
            made.set_item(pyo3::intern!(made.py(), "online"), is_online(&track.dub))?;
            tracks.append(made)?;
        }
        let subtitles = PyList::empty_bound(py);
        for item in &container.subtitles {
            subtitles.append(subtitle(py, item, None)?)?;
        }
        assets.append(dict!(py, {"name": container.name.as_str(), "off": container.off,
            "episode": PyList::new_bound(py, &video.episode), "video": shown, "tracks": tracks,
            "timeline": variant(py, timeline_variant(container).as_ref())?, "subtitles": subtitles}))?;
    }

    let episodes = PyList::empty_bound(py);
    if let (Some(planned), Some(names)) = (planned, names.as_ref()) {
        let inputs = &*planned.inputs;
        for (goal, name) in planned.goals.iter().zip(names) {
            let way = &goal.video;
            let held = way.container.map(|c| &inputs.containers[c]);
            let video = dict!(py, {});
            shot_into(&video, way.snapshot.get())?;
            video.set_item(pyo3::intern!(video.py(), "kind"), way.kind)?;
            video.set_item(pyo3::intern!(video.py(), "row"), row(py, &way.timeline)?)?;
            video.set_item(pyo3::intern!(video.py(), "file"), held.map(|c| c.name.as_str()))?;
            let cut_of = if way.kind == "cut" { way.row.map(|r| &inputs.online[r].id) }
                else { held.and_then(|c| c.video.cut_of.as_ref()) };
            video.set_item(pyo3::intern!(video.py(), "cut_of"), opt_row(py, cut_of)?)?;
            let tracks = PyList::empty_bound(py);
            for track in &goal.tracks {
                let made = dict!(py, {});
                shot_into(&made, track.snapshot.get())?;
                made.set_item(pyo3::intern!(made.py(), "voice"), track.voice.as_str())?;
                made.set_item(pyo3::intern!(made.py(), "kind"), track.kind)?;
                made.set_item(pyo3::intern!(made.py(), "row"), row(py, &track.dub)?)?;
                made.set_item(pyo3::intern!(made.py(), "own"), track.own)?;
                tracks.append(made)?;
            }
            let mut voices_off = goal.voices_off.clone();
            voices_off.sort();
            let subtitles = PyList::empty_bound(py);
            for way in &goal.subtitles {
                subtitles.append(subtitle(py, &way.subtitle, Some(way.kind))?)?;
            }
            let unaligned = PyList::empty_bound(py);
            for each in &goal.unaligned {
                unaligned.append(variant(py, Some(each))?)?;
            }
            episodes.append(dict!(py, {"slot": PyList::new_bound(py, &goal.slot), "name": name.as_str(), "off": goal.off,
                "video": video, "tracks": tracks, "voices_off": voices_off,
                "markup": goal.markup.as_ref().map(|m| voice_of(m.group.as_deref(), &m.id)),
                "markup_variant": variant(py, goal.markup.as_ref().and_then(|m| m.variant.as_ref()))?,
                "subtitles": subtitles, "unaligned": unaligned}))?;
        }
    }

    let holds: Vec<Holds> = asset.iter().map(|c| Holds {
        episode: &c.video.episode, off: c.off,
        groups: std::iter::once(c.video.snapshot.group.as_deref()).chain(c.tracks.iter().map(|t| t.snapshot.group.as_deref()))
            .collect(),
    }).collect();
    let rows: &[Arc<Online>] = planned.map(|p| p.inputs.online.as_slice()).unwrap_or(&[]);
    let failures = PyList::empty_bound(py);
    for each in placed(failed, rows)? {
        let made = placed_py(py, &each)?;
        made.set_item(pyo3::intern!(made.py(), "covered"), covered(&each.episodes, each.voice.as_deref(), &holds))?;
        failures.append(made)?;
    }

    let mut waiting: Vec<(String, Bound<'py, PyAny>)> = commands.iter()?
        .map(|each| { let each = each?; Ok((each.repr()?.extract::<String>()?, each)) }).collect::<PyResult<_>>()?;
    waiting.sort_by(|a, b| a.0.cmp(&b.0));
    let shown_commands = PyList::empty_bound(py);
    for (_, each) in waiting {
        let dub: PyObject = if each.len()? > 2 { row(py, &plan_py::rid(&each.get_item(2)?)?)?.into_any().unbind() } else { py.None() };
        shown_commands.append(dict!(py, {"kind": each.get_item(0)?, "name": each.get_item(1)?, "dub": dub}))?;
    }

    let references = match (planned, names.as_ref()) {
        (Some(planned), Some(_)) => references(py, planned, &members.cloned().unwrap_or_else(|| PyDict::new_bound(py)))?,
        _ => PyList::empty_bound(py),
    };
    Ok(dict!(py, {"series_id": series_id, "running": running, "references": references, "assets": assets,
                  "episodes": episodes, "failures": failures, "problems": problems, "commands": shown_commands}))
}
