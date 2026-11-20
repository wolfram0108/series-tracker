//! The row of the output the planner reads (core/plan/tables.OnlineRow), kept here: made of the columns of a layout with no
//! object of Python built field by field, read by the planner of the core as it is, read by Python as a row of the tables.

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};
use std::hash::{Hash, Hasher};
use std::sync::Arc;

use crate::plan::{Online, RowId};

/// A row of the output (`core/plan/inputs.online_of`): the rules gave it an episode (or a range of them: a compilation)
/// and `group`, the voice-over group, took it. `playable`: the player answered that it plays the video (a row that is not
/// is online all the same, but no work is made of it). `current`: the source gave the row in its last read.
/// `timeline_key`: the key of the row's video as a timeline (rows of one source with one timeline are one video); empty is
/// the row itself. `track`: `sound`, or `subtitles` — a subtitles track, never a voice-over or a video.
#[pyclass(frozen, module = "core.plan.tables", name = "OnlineRow")]
pub struct OnlineRow {
    pub row: Arc<Online>,
    // The row id and the timeline as the objects of Python they are read as (core/plan/tables.RowId).
    id: PyObject,
    timeline: PyObject,
}

fn row_id_class(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    py.import_bound("core.plan.tables")?.getattr("RowId")
}

fn rid(object: &Bound<'_, PyAny>) -> PyResult<RowId> {
    Ok(RowId { source: object.getattr("source_id")?.extract()?, key: object.getattr("row_key")?.extract()?,
               video: object.getattr("video")?.extract()? })
}

impl OnlineRow {
    /// The row of a row of the core, its ids made as the objects of Python they are read as.
    pub fn made(py: Python<'_>, class: &Bound<'_, PyAny>, row: Arc<Online>) -> PyResult<OnlineRow> {
        let id = class.call1((row.id.source, row.id.key.as_str(), row.id.video.as_str()))?.unbind();
        let timeline = if row.timeline_key.is_empty() { id.clone_ref(py) } else {
            let t = row.timeline();
            class.call1((t.source, t.key.as_str(), t.video.as_str()))?.unbind()
        };
        Ok(OnlineRow { row, id, timeline })
    }

    pub fn of(py: Python<'_>, row: Online) -> PyResult<OnlineRow> {
        let class = row_id_class(py)?;
        OnlineRow::made(py, &class, Arc::new(row))
    }

    fn fields(&self) -> impl Hash + PartialEq + '_ {
        let r = &self.row;
        // Tuples of twelve fields at most compare and hash.
        ((&r.id, r.season, r.start, r.end, &r.player, &r.group, r.height, &r.source, &r.link, &r.label),
         (r.position, r.part, r.playable, r.current, &r.player_label, &r.timeline_key, &r.track, &r.language, &r.subtitle_type))
    }
}

#[pymethods]
impl OnlineRow {
    /// The names of its fields, in their order: what a reader that writes a row out whole (core/dump) reads of it.
    #[classattr]
    fn __fields__() -> Vec<&'static str> {
        vec!["id", "season", "start", "end", "player", "group", "height", "source", "link", "label", "position", "part",
             "playable", "current", "player_label", "timeline_key", "track", "language", "subtitle_type"]
    }

    #[new]
    #[pyo3(signature = (id, season, start, end, player, group, height, source="".to_string(), link="".to_string(),
                        label="".to_string(), position=0, part=None, playable=true, current=true,
                        player_label="".to_string(), timeline_key="".to_string(), track="sound".to_string(),
                        language="".to_string(), subtitle_type="".to_string()))]
    #[allow(clippy::too_many_arguments)]
    fn new(py: Python<'_>, id: &Bound<'_, PyAny>, season: i64, start: i64, end: i64, player: String, group: String, height: i64,
           source: String, link: String, label: String, position: i64, part: Option<i64>, playable: bool, current: bool,
           player_label: String, timeline_key: String, track: String, language: String, subtitle_type: String)
        -> PyResult<OnlineRow> {
        let row = Online { id: rid(id)?, season, start, end, player, group, height, source, link, label, position, part,
                           playable, current, player_label, timeline_key, track, language, subtitle_type,
                           timeline_id: RowId { source: 0, key: String::new(), video: String::new() },
                           variant_of: (String::new(), 0, String::new()) }.made();
        OnlineRow::of(py, row)
    }

    /// The row with some of its fields changed (what `dataclasses.replace` gives a dataclass).
    #[pyo3(signature = (**changes))]
    fn replaced(&self, py: Python<'_>, changes: Option<&Bound<'_, PyDict>>) -> PyResult<OnlineRow> {
        let mut row = (*self.row).clone();
        for (name, value) in changes.map(|c| c.iter().collect::<Vec<_>>()).unwrap_or_default() {
            let name: String = name.extract()?;
            match name.as_str() {
                "id" => row.id = rid(&value)?,
                "season" => row.season = value.extract()?,
                "start" => row.start = value.extract()?,
                "end" => row.end = value.extract()?,
                "player" => row.player = value.extract()?,
                "group" => row.group = value.extract()?,
                "height" => row.height = value.extract()?,
                "source" => row.source = value.extract()?,
                "link" => row.link = value.extract()?,
                "label" => row.label = value.extract()?,
                "position" => row.position = value.extract()?,
                "part" => row.part = value.extract()?,
                "playable" => row.playable = value.extract()?,
                "current" => row.current = value.extract()?,
                "player_label" => row.player_label = value.extract()?,
                "timeline_key" => row.timeline_key = value.extract()?,
                "track" => row.track = value.extract()?,
                "language" => row.language = value.extract()?,
                "subtitle_type" => row.subtitle_type = value.extract()?,
                other => return Err(PyTypeError::new_err(format!("OnlineRow has no field {other}"))),
            }
        }
        OnlineRow::of(py, row.made())
    }

    #[getter]
    fn id(&self, py: Python<'_>) -> PyObject {
        self.id.clone_ref(py)
    }
    #[getter]
    fn season(&self) -> i64 {
        self.row.season
    }
    #[getter]
    fn start(&self) -> i64 {
        self.row.start
    }
    #[getter]
    fn end(&self) -> i64 {
        self.row.end
    }
    #[getter]
    fn player(&self) -> &str {
        &self.row.player
    }
    #[getter]
    fn group(&self) -> &str {
        &self.row.group
    }
    #[getter]
    fn height(&self) -> i64 {
        self.row.height
    }
    #[getter]
    fn source(&self) -> &str {
        &self.row.source
    }
    #[getter]
    fn link(&self) -> &str {
        &self.row.link
    }
    #[getter]
    fn label(&self) -> &str {
        &self.row.label
    }
    #[getter]
    fn position(&self) -> i64 {
        self.row.position
    }
    #[getter]
    fn part(&self) -> Option<i64> {
        self.row.part
    }
    #[getter]
    fn playable(&self) -> bool {
        self.row.playable
    }
    #[getter]
    fn current(&self) -> bool {
        self.row.current
    }
    #[getter]
    fn player_label(&self) -> &str {
        &self.row.player_label
    }
    #[getter]
    fn timeline_key(&self) -> &str {
        &self.row.timeline_key
    }
    #[getter]
    fn track(&self) -> &str {
        &self.row.track
    }
    #[getter]
    fn language(&self) -> &str {
        &self.row.language
    }
    #[getter]
    fn subtitle_type(&self) -> &str {
        &self.row.subtitle_type
    }
    /// The timeline of the row's video: the row itself, or the video its rows share.
    #[getter]
    fn timeline(&self, py: Python<'_>) -> PyObject {
        self.timeline.clone_ref(py)
    }
    #[getter]
    fn source_id(&self) -> i64 {
        self.row.id.source
    }
    /// (voice-over, source, player): what the user's levers name.
    #[getter]
    fn variant(&self) -> (String, i64, String) {
        self.row.variant().clone()
    }
    #[getter]
    fn compilation(&self) -> bool {
        self.row.compilation()
    }
    #[getter]
    fn slot<'py>(&self, py: Python<'py>) -> Bound<'py, PyTuple> {
        PyTuple::new_bound(py, self.row.slot())
    }
    fn snapshot(&self, py: Python<'_>) -> PyResult<PyObject> {
        let r = &self.row;
        let class = py.import_bound("core.plan.tables")?.getattr("Snapshot")?;
        Ok(class.call1((r.source.as_str(), r.player.as_str(), r.group.as_str(), r.height, r.link.as_str(), r.label.as_str()))?
            .unbind())
    }
    fn __eq__(&self, other: &Bound<'_, PyAny>) -> bool {
        other.downcast::<OnlineRow>().map_or(false, |o| o.get().fields() == self.fields())
    }
    fn __hash__(&self) -> u64 {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        self.fields().hash(&mut hasher);
        hasher.finish()
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let r = &self.row;
        Ok(format!("OnlineRow(id={}, season={}, start={}, end={}, player={:?}, group={:?}, height={}, position={}, part={:?})",
                   self.id.bind(py).repr()?, r.season, r.start, r.end, r.player, r.group, r.height, r.position, r.part))
    }
}
