//! The goals of the planner (plan.rs) for the planner of Python: its inputs read of its own objects (core/plan/tables.py),
//! its goals given back as places in those inputs, out of which the planner makes its `Goal`s with the objects it holds.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use std::collections::{HashMap, HashSet};

use crate::plan::{Choices, Container, Failed, Fails, Goal, Inputs, Key, Online, Planner, RowId, Same, Slot, Snap, Snapshot,
                  Subtitle, Track, Video, VideoWay};

fn at<'py, T: FromPyObject<'py>>(object: &Bound<'py, PyAny>, name: &str) -> PyResult<T> {
    object.getattr(name)?.extract()
}

fn rid(object: &Bound<'_, PyAny>) -> PyResult<RowId> {
    Ok(RowId { source: at(object, "source_id")?, key: at(object, "row_key")?, video: at(object, "video")? })
}

fn opt_rid(object: &Bound<'_, PyAny>) -> PyResult<Option<RowId>> {
    if object.is_none() { Ok(None) } else { rid(object).map(Some) }
}

fn shot(object: &Bound<'_, PyAny>) -> PyResult<Snapshot> {
    Ok(Snapshot { source: at(object, "source")?, player: at(object, "player")?, group: at(object, "group")?,
                  height: at(object, "height")?, link: at(object, "link")?, label: at(object, "label")? })
}

fn online(object: &Bound<'_, PyAny>) -> PyResult<Online> {
    Ok(Online {
        id: rid(&object.getattr("id")?)?, season: at(object, "season")?, start: at(object, "start")?, end: at(object, "end")?,
        player: at(object, "player")?, group: at(object, "group")?, height: at(object, "height")?,
        source: at(object, "source")?, link: at(object, "link")?, label: at(object, "label")?,
        position: at(object, "position")?, part: at(object, "part")?, playable: at(object, "playable")?,
        current: at(object, "current")?, player_label: at(object, "player_label")?, timeline_key: at(object, "timeline_key")?,
        track: at(object, "track")?, language: at(object, "language")?, subtitle_type: at(object, "subtitle_type")?,
        timeline_id: RowId { source: 0, key: String::new(), video: String::new() },
        variant_of: (String::new(), 0, String::new()),
    }.made())
}

fn subtitle(object: &Bound<'_, PyAny>) -> PyResult<Subtitle> {
    Ok(Subtitle { row: rid(&object.getattr("row")?)?, name: at(object, "name")?, player: at(object, "player")?,
                  timeline: rid(&object.getattr("timeline")?)?, language: at(object, "language")?,
                  subtitle_type: at(object, "subtitle_type")?, default: at(object, "default")?, forced: at(object, "forced")? })
}

fn container(object: &Bound<'_, PyAny>) -> PyResult<Container> {
    let video = object.getattr("video")?;
    let timeline_snapshot = video.getattr("timeline_snapshot")?;
    let tracks = object.getattr("tracks")?.iter()?.map(|t| {
        let t = t?;
        Ok(Track { dub: rid(&t.getattr("dub")?)?, snapshot: shot(&t.getattr("snapshot")?)?, episode: at(&t, "episode")?,
                   aligned_to: rid(&t.getattr("aligned_to")?)? })
    }).collect::<PyResult<Vec<_>>>()?;
    Ok(Container {
        name: at(object, "name")?,
        video: Video { id: rid(&video.getattr("id")?)?, snapshot: shot(&video.getattr("snapshot")?)?,
                       episode: at(&video, "episode")?, timeline: rid(&video.getattr("timeline")?)?,
                       cut_of: opt_rid(&video.getattr("cut_of")?)?,
                       timeline_snapshot: if timeline_snapshot.is_none() { None } else { Some(shot(&timeline_snapshot)?) } },
        tracks, off: at(object, "off")?, titles: at(object, "titles")?, languages: at(object, "languages")?,
        subtitles: object.getattr("subtitles")?.iter()?.map(|s| subtitle(&s?)).collect::<PyResult<Vec<_>>>()?,
    })
}

type Variant = (String, i64, String);

fn by_slot<'py, T>(object: &Bound<'py, PyAny>, name: &str, f: impl Fn(&Bound<'py, PyAny>) -> PyResult<T>)
    -> PyResult<HashMap<Slot, T>> {
    let found = object.getattr(name)?;
    let dict = found.downcast::<PyDict>()?;
    dict.iter().map(|(k, v)| Ok((k.extract::<Slot>()?, f(&v)?))).collect()
}

fn same(object: &Bound<'_, PyAny>) -> PyResult<Same> {
    Ok(Same { groups: object.getattr("groups")?.extract::<Vec<HashSet<Variant>>>()? })
}

fn choices(object: &Bound<'_, PyAny>) -> PyResult<Choices> {
    let groups = object.getattr("player_groups")?.getattr("index")?;
    let voice_order = object.getattr("voice_order")?;
    let mut made = Choices {
        players: at(object, "players")?, sources: at(object, "sources")?,
        groups: object.getattr("groups")?.downcast::<PyDict>()?.iter()
            .map(|(k, v)| Ok((k.extract::<i64>()?, v.extract::<Vec<String>>()?))).collect::<PyResult<_>>()?,
        player_groups: groups.downcast::<PyDict>()?.iter().map(|(k, v)| Ok((k.extract()?, v.extract()?)))
            .collect::<PyResult<_>>()?,
        off_episodes: at(object, "off_episodes")?, off_groups: at(object, "off_groups")?,
        off_compilations: object.getattr("off_compilations")?.iter()?.map(|r| rid(&r?)).collect::<PyResult<_>>()?,
        reference: by_slot(object, "reference", |v| v.extract())?,
        reference_deny: by_slot(object, "reference_deny", |v| v.extract())?,
        voice_order: voice_order.downcast::<PyDict>()?.iter().map(|(k, v)| {
            let k = k.downcast_into::<PyTuple>()?;
            Ok(((k.get_item(0)?.extract::<Slot>()?, k.get_item(1)?.extract::<String>()?), v.extract()?))
        }).collect::<PyResult<_>>()?,
        voice_deny: by_slot(object, "voice_deny", |v| v.extract())?,
        voice_off: by_slot(object, "voice_off", |v| v.extract())?,
        member: by_slot(object, "member", opt_rid)?,
        titles: object.getattr("titles")?.downcast::<PyDict>()?.iter().map(|(k, v)| Ok((k.extract()?, v.extract()?)))
            .collect::<PyResult<_>>()?,
        languages: object.getattr("languages")?.downcast::<PyDict>()?.iter().map(|(k, v)| Ok((k.extract()?, v.extract()?)))
            .collect::<PyResult<_>>()?,
        markup: by_slot(object, "markup", |v| v.extract())?,
        markup_deny: by_slot(object, "markup_deny", |v| v.extract())?,
        same: by_slot(object, "same", same)?,
        markup_same: by_slot(object, "markup_same", same)?,
        subtitle_order: by_slot(object, "subtitle_order", |v| v.extract())?,
        subtitle_deny: by_slot(object, "subtitle_deny", |v| v.extract())?,
        ..Default::default()
    };
    for each in object.getattr("delete")?.iter()? {
        let each = each?.downcast_into::<PyTuple>()?;
        match each.get_item(0)?.extract::<String>()?.as_str() {
            "video" => {
                made.delete_videos.insert(each.get_item(1)?.extract()?);
            }
            "track" => {
                made.delete_tracks.insert((each.get_item(1)?.extract()?, rid(&each.get_item(2)?)?));
            }
            _ => {}
        }
    }
    Ok(made)
}

fn fails(object: &Bound<'_, PyAny>) -> PyResult<Fails> {
    let mut made = Fails::default();
    for each in object.iter()? {
        let each = each?;
        let Ok(each) = each.downcast::<PyTuple>() else { continue };
        let kind: String = match each.get_item(0)?.extract() {
            Ok(kind) => kind,
            Err(_) => continue,
        };
        let row = |i: usize| -> PyResult<RowId> { rid(&each.get_item(i)?) };
        match kind.as_str() {
            "download" if each.len() == 2 => made.add(Failed::Download(row(1)?)),
            "align" if each.len() == 3 => made.add(Failed::Align(row(1)?, row(2)?)),
            "cut" if each.len() == 3 => made.add(Failed::Cut(row(1)?, row(2)?)),
            _ => {}
        }
    }
    Ok(made)
}

/// The inputs of the planner of Python (`Inputs`) read into the planner here.
pub fn inputs(object: &Bound<'_, PyAny>) -> PyResult<Inputs> {
    Ok(Inputs {
        online: object.getattr("online")?.iter()?.map(|r| online(&r?)).collect::<PyResult<_>>()?,
        subtitles: object.getattr("subtitles")?.iter()?.map(|r| online(&r?)).collect::<PyResult<_>>()?,
        containers: object.getattr("containers")?.iter()?.map(|c| container(&c?)).collect::<PyResult<_>>()?,
        choices: choices(&object.getattr("choices")?)?,
        failed: fails(&object.getattr("failed")?)?,
    })
}

fn rid_py(py: Python<'_>, row: &RowId) -> PyObject {
    (row.source, row.key.as_str(), row.video.as_str()).into_py(py)
}

fn key_py(py: Python<'_>, key: &Key) -> PyObject {
    match key {
        Key::Row(r) => {
            let made: (&str, PyObject) = ("row", rid_py(py, r));
            made.into_py(py)
        }
        Key::Cut(a, b) => {
            let made: (&str, PyObject, PyObject) = ("cut", rid_py(py, a), rid_py(py, b));
            made.into_py(py)
        }
    }
}

/// Where the snapshot of a way is: ("row", row) — of the row —, ("video", file), ("track", file, track).
fn snap_py(py: Python<'_>, snap: &Snap, inputs: &Inputs, row: Option<usize>, container: Option<usize>) -> PyObject {
    match snap {
        Snap::Row(_) => ("row", row).into_py(py),
        Snap::Of(s) => {
            let c = container.expect("a snapshot of a file has its file");
            let held = &inputs.containers[c];
            if std::ptr::eq(*s, &held.video.snapshot) {
                ("video", c).into_py(py)
            } else {
                let t = held.tracks.iter().position(|t| std::ptr::eq(&t.snapshot, *s)).expect("a track of the file");
                ("track", c, t).into_py(py)
            }
        }
    }
}

fn way_py(py: Python<'_>, way: &VideoWay, inputs: &Inputs) -> PyObject {
    let container = way.container;
    let snapshot = snap_py(py, &way.snapshot, inputs, way.row, container);
    let ref2 = way.ref2.as_ref().map(|r| way_py(py, r, inputs));
    (way.kind, key_py(py, &way.key), rid_py(py, &way.timeline), snapshot, way.row, container, ref2, way.picture).into_py(py)
}

fn goal_py(py: Python<'_>, goal: &Goal, inputs: &Inputs) -> PyObject {
    let tracks: Vec<PyObject> = goal.tracks.iter().map(|t| {
        (t.voice.as_str(), rid_py(py, &t.dub), snap_py(py, &t.snapshot, inputs, t.row, t.container), t.kind, t.row,
         t.container, t.own).into_py(py)
    }).collect();
    let markup: Option<PyObject> = goal.markup.as_ref().map(|m| (rid_py(py, &m.id), m.group.as_deref(), m.height, m.row,
                                                                 m.variant.clone()).into_py(py));
    let subtitles: Vec<PyObject> = goal.subtitles.iter().map(|w| {
        let s = &w.subtitle;
        match (w.kind, w.container) {
            ("reuse", Some(c)) => {
                let at = inputs.containers[c].subtitles.iter().position(|each| each.row == s.row && each.name == s.name
                    && each.player == s.player && each.timeline == s.timeline && each.language == s.language
                    && each.subtitle_type == s.subtitle_type).expect("a subtitle of the file");
                ("reuse", c, at, s.default, s.forced).into_py(py)
            }
            _ => ("take", w.row, s.default, s.forced, 0usize).into_py(py),
        }
    }).collect();
    let head = (goal.slot.clone(), way_py(py, &goal.video, inputs), tracks, goal.off, goal.voices_off.clone(), goal.own_at);
    let tail = (goal.titles.clone(), markup, goal.languages.clone(), subtitles, goal.unaligned.clone());
    let parts: (PyObject, PyObject) = (head.into_py(py), tail.into_py(py));
    parts.into_py(py)
}

/// The goals of the planner's inputs (`Inputs`), each as places in them: what `core.plan.planner` makes its goals of.
#[pyfunction]
pub fn plan_goals<'py>(py: Python<'py>, given: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let inputs = self::inputs(given)?;
    let goals = py.allow_threads(|| {
        let planner = Planner::new(&inputs);
        planner.goals()
    });
    let made = PyList::empty_bound(py);
    for goal in &goals {
        made.append(goal_py(py, goal, &inputs))?;
    }
    Ok(made)
}
