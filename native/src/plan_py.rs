//! The goals of the planner (plan.rs) for the planner of Python: its inputs read of its own objects (core/plan/tables.py),
//! its goals given back as places in those inputs, out of which the planner makes its `Goal`s with the objects it holds.

use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::plan::{Container, Failed, Fails, Goal, Inputs, Key, RowId, Snap, Snapshot, Subtitle, Track, Video, VideoWay};

fn at<'py, T: FromPyObject<'py>>(object: &Bound<'py, PyAny>, name: &str) -> PyResult<T> {
    object.getattr(name)?.extract()
}

pub(crate) fn rid(object: &Bound<'_, PyAny>) -> PyResult<RowId> {
    Ok(RowId { source: at(object, "source_id")?, key: at(object, "row_key")?, video: at(object, "video")? })
}

fn opt_rid(object: &Bound<'_, PyAny>) -> PyResult<Option<RowId>> {
    if object.is_none() { Ok(None) } else { rid(object).map(Some) }
}

fn shot(object: &Bound<'_, PyAny>) -> PyResult<Snapshot> {
    Ok(Snapshot { source: at(object, "source")?, player: at(object, "player")?, group: at(object, "group")?,
                  height: at(object, "height")?, link: at(object, "link")?, label: at(object, "label")? })
}

fn subtitle(object: &Bound<'_, PyAny>) -> PyResult<Subtitle> {
    Ok(Subtitle { row: rid(&object.getattr("row")?)?, name: at(object, "name")?, player: at(object, "player")?,
                  timeline: rid(&object.getattr("timeline")?)?, language: at(object, "language")?,
                  subtitle_type: at(object, "subtitle_type")?, default: at(object, "default")?, forced: at(object, "forced")? })
}

pub(crate) fn container(object: &Bound<'_, PyAny>) -> PyResult<Container> {
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
    let rows = crate::plan_inputs::rows_of(&object.getattr("online")?)?;
    let subtitles = crate::plan_inputs::rows_of(&object.getattr("subtitles")?)?;
    let containers: Vec<Container> = object.getattr("containers")?.iter()?.map(|c| container(&c?)).collect::<PyResult<_>>()?;
    let choices = crate::plan_inputs::choices_ref(&object.getattr("choices")?)?;
    let failed = fails(&object.getattr("failed")?)?;
    Ok(Inputs { online: rows, subtitles, containers, choices, failed })
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

pub(crate) fn goal_py(py: Python<'_>, goal: &Goal, inputs: &Inputs) -> PyObject {
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

/// The goals of the planner's inputs (`Inputs`), counted: what `core.plan.planner` makes its goals of (`specs`), and
/// what the view of the window is counted from.
#[pyfunction]
pub fn plan_goals(py: Python<'_>, given: &Bound<'_, PyAny>) -> PyResult<crate::plan_view::Planned> {
    crate::plan_view::Planned::of(py, given)
}
