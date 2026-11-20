//! The inputs of the planner kept by the core (core/plan/tables, core/plan/inputs): the rows of the output as the core
//! holds them (`OnlineRows`: made into objects of Python only when Python reads them), and the choices of the user
//! (`Choices`), made of the levers of the layout by the core (`plan_choices`) or of the values Python gives.

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBool, PyDict, PyFrozenSet, PyIterator, PyList, PyString, PyTuple};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::Arc;

use crate::plan::{Choices, Online, RowId, Same, Slot, Variant};
use crate::plan_py::rid;
use crate::plan_rows::OnlineRow;

// --- the rows -------------------------------------------------------------------------------------------------------

/// Rows of the output (core/plan/tables.OnlineRow), a sequence of Python: what the planner of the core reads as it is.
#[pyclass(frozen, sequence, module = "core.native", name = "OnlineRows")]
pub struct OnlineRows {
    pub rows: Vec<Arc<Online>>,
    // The rows as objects of Python, made when Python first reads one.
    objects: GILOnceCell<Py<PyTuple>>,
    // The video of the row of every (source, row key): the last row with that key.
    videos: GILOnceCell<HashMap<(i64, String), String>>,
}

impl OnlineRows {
    pub fn of(rows: Vec<Arc<Online>>) -> OnlineRows {
        OnlineRows { rows, objects: GILOnceCell::new(), videos: GILOnceCell::new() }
    }

    fn objects<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyTuple>> {
        let made = self.objects.get_or_try_init(py, || -> PyResult<Py<PyTuple>> {
            let class = py.import_bound("core.plan.tables")?.getattr("RowId")?;
            let rows: Vec<Py<OnlineRow>> = self.rows.iter()
                .map(|row| Py::new(py, OnlineRow::made(py, &class, row.clone())?)).collect::<PyResult<_>>()?;
            Ok(PyTuple::new_bound(py, rows).unbind())
        })?;
        Ok(made.bind(py).clone())
    }
}

/// The rows of a sequence of rows: those of the core as they are, every other one a row of the core.
pub fn rows_of(object: &Bound<'_, PyAny>) -> PyResult<Vec<Arc<Online>>> {
    if let Ok(rows) = object.downcast::<OnlineRows>() {
        return Ok(rows.get().rows.clone());
    }
    object.iter()?.map(|row| Ok(row?.downcast::<OnlineRow>()?.get().row.clone())).collect()
}

#[pymethods]
impl OnlineRows {
    #[new]
    #[pyo3(signature = (rows=None))]
    fn new(rows: Option<&Bound<'_, PyAny>>) -> PyResult<OnlineRows> {
        Ok(OnlineRows::of(match rows {
            Some(rows) => rows_of(rows)?,
            None => Vec::new(),
        }))
    }

    fn __len__(&self) -> usize {
        self.rows.len()
    }

    fn __getitem__<'py>(&self, py: Python<'py>, key: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        self.objects(py)?.as_any().get_item(key)
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        self.objects(py)?.as_any().iter()
    }

    fn __add__(&self, other: &Bound<'_, PyAny>) -> PyResult<OnlineRows> {
        let mut rows = self.rows.clone();
        rows.extend(rows_of(other)?);
        Ok(OnlineRows::of(rows))
    }

    fn __radd__(&self, other: &Bound<'_, PyAny>) -> PyResult<OnlineRows> {
        let mut rows = rows_of(other)?;
        rows.extend(self.rows.iter().cloned());
        Ok(OnlineRows::of(rows))
    }

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        let Ok(theirs) = other.iter() else { return Ok(false) };
        let theirs: Vec<Bound<'_, PyAny>> = theirs.collect::<PyResult<_>>()?;
        if theirs.len() != self.rows.len() {
            return Ok(false);
        }
        let mine = self.objects(py)?;
        for (a, b) in mine.iter().zip(theirs.iter()) {
            if !a.eq(b)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn __repr__(&self) -> String {
        format!("OnlineRows({} rows)", self.rows.len())
    }

    /// (the rows of sound, the rows of subtitles): those of `subtitles` and every row of these that is one.
    fn split(&self, subtitles: &Bound<'_, PyAny>) -> PyResult<(OnlineRows, OnlineRows)> {
        let mut sound = Vec::with_capacity(self.rows.len());
        let mut titles = rows_of(subtitles)?;
        for row in &self.rows {
            if row.track == "subtitles" { titles.push(row.clone()) } else { sound.push(row.clone()) }
        }
        Ok((OnlineRows::of(sound), OnlineRows::of(titles)))
    }

    /// The (season, episode) of every row of a single episode (no compilation).
    fn episodes(&self) -> HashSet<(i64, i64)> {
        self.rows.iter().filter(|row| !row.compilation()).map(|row| (row.season, row.start)).collect()
    }

    /// The video of the row of a source with that key (of the last such row), none when no row has the key.
    fn video_of(&self, py: Python<'_>, source_id: i64, row_key: &str) -> Option<String> {
        let videos = self.videos.get_or_init(py, || {
            self.rows.iter().map(|row| ((row.id.source, row.id.key.clone()), row.id.video.clone())).collect()
        });
        videos.get(&(source_id, row_key.to_string())).cloned()
    }
}

// --- the choices ----------------------------------------------------------------------------------------------------

const FIELDS: [&str; 23] = ["players", "sources", "groups", "player_groups", "off_episodes", "off_groups", "off_compilations",
    "reference", "reference_deny", "voice_order", "voice_deny", "voice_off", "member", "titles", "languages", "delete",
    "own_reference", "markup", "markup_deny", "same", "markup_same", "subtitle_order", "subtitle_deny"];

/// What the user chose (core/plan/tables.Choices, its fields as the docstring there tells them), kept by the core.
#[pyclass(frozen, module = "core.plan.tables", name = "Choices")]
pub struct ChoicesPy {
    pub choices: Arc<Choices>,
    // The fields as Python reads them: given, or made of the core's when first read.
    read: Py<PyDict>,
}

/// A map by episode whose values are shared objects of Python (the value of a level, one for its hundreds of episodes):
/// each object is read once.
fn by_slot<'py, T: Clone>(value: &Bound<'py, PyAny>, f: impl Fn(&Bound<'py, PyAny>) -> PyResult<T>) -> PyResult<HashMap<Slot, T>> {
    let mut read: HashMap<usize, T> = HashMap::new();
    value.downcast::<PyDict>()?.iter().map(|(k, v)| {
        let at = v.as_ptr() as usize;
        let made = match read.get(&at) {
            Some(made) => made.clone(),
            None => {
                let made = f(&v)?;
                read.insert(at, made.clone());
                made
            }
        };
        Ok((k.extract::<Slot>()?, made))
    }).collect()
}

fn same_of(value: &Bound<'_, PyAny>) -> PyResult<Same> {
    Ok(Same { groups: value.getattr("groups")?.extract::<Vec<HashSet<Variant>>>()? })
}

fn pairs(value: &Bound<'_, PyAny>) -> PyResult<Vec<(String, String)>> {
    value.downcast::<PyDict>()?.iter().map(|(k, v)| Ok((k.extract()?, v.extract()?))).collect()
}

/// A field of the choices given its value as Python has it.
fn set_choice(choices: &mut Choices, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
    match name {
        "players" => choices.players = value.extract()?,
        "sources" => choices.sources = value.extract()?,
        "groups" => choices.groups = value.downcast::<PyDict>()?.iter()
            .map(|(k, v)| Ok((k.extract::<i64>()?, v.extract::<Vec<String>>()?))).collect::<PyResult<_>>()?,
        "player_groups" => choices.player_groups = value.getattr("index")?.downcast::<PyDict>()?.iter()
            .map(|(k, v)| Ok((k.extract()?, v.extract()?))).collect::<PyResult<_>>()?,
        "off_episodes" => choices.off_episodes = value.extract()?,
        "off_groups" => choices.off_groups = value.extract()?,
        "off_compilations" => choices.off_compilations = value.iter()?.map(|r| rid(&r?)).collect::<PyResult<_>>()?,
        "reference" => choices.reference = by_slot(value, |v| v.extract())?,
        "reference_deny" => choices.reference_deny = by_slot(value, |v| v.extract())?,
        "voice_order" => choices.voice_order = value.downcast::<PyDict>()?.iter().map(|(k, v)| {
            let k = k.downcast_into::<PyTuple>()?;
            Ok(((k.get_item(0)?.extract::<Slot>()?, k.get_item(1)?.extract::<String>()?), v.extract()?))
        }).collect::<PyResult<_>>()?,
        "voice_deny" => choices.voice_deny = by_slot(value, |v| v.extract())?,
        "voice_off" => choices.voice_off = by_slot(value, |v| v.extract())?,
        "member" => choices.member = by_slot(value, |v| if v.is_none() { Ok(None) } else { rid(v).map(Some) })?,
        "titles" => choices.titles = pairs(value)?,
        "languages" => choices.languages = pairs(value)?,
        "delete" => {
            choices.delete_videos.clear();
            choices.delete_tracks.clear();
            for each in value.iter()? {
                let each = each?.downcast_into::<PyTuple>()?;
                match each.get_item(0)?.extract::<String>()?.as_str() {
                    "video" => {
                        choices.delete_videos.insert(each.get_item(1)?.extract()?);
                    }
                    "track" => {
                        choices.delete_tracks.insert((each.get_item(1)?.extract()?, rid(&each.get_item(2)?)?));
                    }
                    _ => {}
                }
            }
        }
        "own_reference" => choices.own_reference = value.extract()?,
        "markup" => choices.markup = by_slot(value, |v| v.extract())?,
        "markup_deny" => choices.markup_deny = by_slot(value, |v| v.extract())?,
        "same" => choices.same = by_slot(value, same_of)?,
        "markup_same" => choices.markup_same = by_slot(value, same_of)?,
        "subtitle_order" => choices.subtitle_order = by_slot(value, |v| v.extract())?,
        "subtitle_deny" => choices.subtitle_deny = by_slot(value, |v| v.extract())?,
        other => return Err(PyTypeError::new_err(format!("Choices has no field {other}"))),
    }
    Ok(())
}

fn row_py(class: &Bound<'_, PyAny>, id: &RowId) -> PyResult<PyObject> {
    Ok(class.call1((id.source, id.key.as_str(), id.video.as_str()))?.unbind())
}

fn variants_py<'py>(py: Python<'py>, found: &[Variant]) -> Bound<'py, PyTuple> {
    PyTuple::new_bound(py, found.iter().map(|v| -> PyObject { v.clone().into_py(py) }))
}

fn set_py<'py, T: ToPyObject>(py: Python<'py>, found: impl IntoIterator<Item = T>) -> PyResult<Bound<'py, PyFrozenSet>> {
    let items: Vec<PyObject> = found.into_iter().map(|each| each.to_object(py)).collect();
    PyFrozenSet::new_bound(py, &items)
}

fn slot_py<'py>(py: Python<'py>, slot: &Slot) -> Bound<'py, PyTuple> {
    PyTuple::new_bound(py, slot)
}

fn same_py(py: Python<'_>, same: &Same) -> PyResult<PyObject> {
    let class = py.import_bound("core.variant_queue")?.getattr("Same")?;
    let groups: Vec<PyObject> = same.groups.iter().map(|g| set_py(py, g.iter().cloned()).map(|s| s.into_any().unbind()))
        .collect::<PyResult<_>>()?;
    Ok(class.call1((PyTuple::new_bound(py, groups),))?.unbind())
}

/// A field of the choices made of the core's, as Python reads it (the types of core/plan/tables.Choices).
fn choice_py(py: Python<'_>, choices: &Choices, name: &str) -> PyResult<PyObject> {
    let row_class = || py.import_bound("core.plan.tables").and_then(|m| m.getattr("RowId"));
    let dict = PyDict::new_bound(py);
    let by_slot_of = |map: &HashMap<Slot, Vec<Variant>>| -> PyResult<PyObject> {
        let made = PyDict::new_bound(py);
        for (slot, found) in map {
            made.set_item(slot_py(py, slot), variants_py(py, found))?;
        }
        Ok(made.into_any().unbind())
    };
    let sets_of = |map: &HashMap<Slot, HashSet<Variant>>| -> PyResult<PyObject> {
        let made = PyDict::new_bound(py);
        for (slot, found) in map {
            made.set_item(slot_py(py, slot), set_py(py, found.iter().cloned())?)?;
        }
        Ok(made.into_any().unbind())
    };
    Ok(match name {
        "players" => PyTuple::new_bound(py, &choices.players).into_any().unbind(),
        "sources" => PyTuple::new_bound(py, &choices.sources).into_any().unbind(),
        "groups" => {
            for (season, order) in &choices.groups {
                dict.set_item(season, PyTuple::new_bound(py, order))?;
            }
            dict.into_any().unbind()
        }
        "player_groups" => {
            for (key, group) in &choices.player_groups {
                dict.set_item(key, group)?;
            }
            py.import_bound("core.voice_names")?.getattr("Groups")?.call1((dict,))?.unbind()
        }
        "off_episodes" => set_py(py, choices.off_episodes.iter().map(|s| slot_py(py, s).unbind()))?.into_any().unbind(),
        "off_groups" => set_py(py, choices.off_groups.iter().cloned())?.into_any().unbind(),
        "off_compilations" => {
            let class = row_class()?;
            let rows: Vec<PyObject> = choices.off_compilations.iter().map(|r| row_py(&class, r)).collect::<PyResult<_>>()?;
            PyFrozenSet::new_bound(py, &rows)?.into_any().unbind()
        }
        "reference" => by_slot_of(&choices.reference)?,
        "markup" => by_slot_of(&choices.markup)?,
        "subtitle_order" => by_slot_of(&choices.subtitle_order)?,
        "reference_deny" => sets_of(&choices.reference_deny)?,
        "voice_deny" => sets_of(&choices.voice_deny)?,
        "markup_deny" => sets_of(&choices.markup_deny)?,
        "subtitle_deny" => sets_of(&choices.subtitle_deny)?,
        "voice_order" => {
            for ((slot, group), found) in &choices.voice_order {
                let key = PyTuple::new_bound(py, [slot_py(py, slot).into_any().unbind(), group.into_py(py)]);
                dict.set_item(key, PyTuple::new_bound(py, found.iter().map(|v| -> PyObject { v.clone().into_py(py) })))?;
            }
            dict.into_any().unbind()
        }
        "voice_off" => {
            for (slot, found) in &choices.voice_off {
                dict.set_item(slot_py(py, slot), set_py(py, found.iter().cloned())?)?;
            }
            dict.into_any().unbind()
        }
        "member" => {
            let class = row_class()?;
            for (slot, found) in &choices.member {
                let row = match found {
                    Some(row) => row_py(&class, row)?,
                    None => py.None(),
                };
                dict.set_item(slot_py(py, slot), row)?;
            }
            dict.into_any().unbind()
        }
        "titles" | "languages" => {
            for (key, value) in if name == "titles" { &choices.titles } else { &choices.languages } {
                dict.set_item(key, value)?;
            }
            dict.into_any().unbind()
        }
        "delete" => {
            let class = row_class()?;
            let mut found: Vec<PyObject> = choices.delete_videos.iter().map(|name| ("video", name.as_str()).into_py(py)).collect();
            for (name, dub) in &choices.delete_tracks {
                found.push(("track", name.as_str(), row_py(&class, dub)?).into_py(py));
            }
            PyFrozenSet::new_bound(py, &found)?.into_any().unbind()
        }
        "own_reference" => set_py(py, choices.own_reference.iter().map(|s| slot_py(py, s).unbind()))?.into_any().unbind(),
        "same" | "markup_same" => {
            for (slot, same) in if name == "same" { &choices.same } else { &choices.markup_same } {
                dict.set_item(slot_py(py, slot), same_py(py, same)?)?;
            }
            dict.into_any().unbind()
        }
        other => return Err(pyo3::exceptions::PyAttributeError::new_err(format!("Choices has no field {other}"))),
    })
}

#[pymethods]
impl ChoicesPy {
    #[new]
    #[pyo3(signature = (**fields))]
    fn new(py: Python<'_>, fields: Option<&Bound<'_, PyDict>>) -> PyResult<ChoicesPy> {
        let mut choices = Choices::default();
        let read = PyDict::new_bound(py);
        for (name, value) in fields.map(|f| f.iter().collect::<Vec<_>>()).unwrap_or_default() {
            set_choice(&mut choices, &name.extract::<String>()?, &value)?;
            read.set_item(name, value)?;
        }
        Ok(ChoicesPy { choices: Arc::new(choices), read: read.unbind() })
    }

    #[classattr]
    fn __fields__() -> Vec<&'static str> {
        FIELDS.to_vec()
    }

    fn __getattr__(&self, py: Python<'_>, name: &str) -> PyResult<PyObject> {
        let read = self.read.bind(py);
        if let Some(found) = read.get_item(name)? {
            return Ok(found.unbind());
        }
        let made = choice_py(py, &self.choices, name)?;
        read.set_item(name, &made)?;
        Ok(made)
    }

    /// The choices with some of their fields changed (what `dataclasses.replace` gives a dataclass).
    #[pyo3(signature = (**changes))]
    fn replaced(&self, py: Python<'_>, changes: Option<&Bound<'_, PyDict>>) -> PyResult<ChoicesPy> {
        let mut choices = (*self.choices).clone();
        let read = self.read.bind(py).copy()?;
        for (name, value) in changes.map(|c| c.iter().collect::<Vec<_>>()).unwrap_or_default() {
            set_choice(&mut choices, &name.extract::<String>()?, &value)?;
            read.set_item(name, value)?;
        }
        Ok(ChoicesPy { choices: Arc::new(choices), read: read.unbind() })
    }

    fn __repr__(&self) -> String {
        format!("Choices(players={:?}, sources={:?}, {} episodes with a reference of their own)", self.choices.players,
                self.choices.sources, self.choices.reference.len())
    }
}

// --- the choices of the levers of the layout (core/plan/inputs.choices_of) ------------------------------------------------

/// The names of the levers and of the levels (core/contracts/output_settings), read of the contracts themselves.
struct Names {
    reference_order: String,
    reference_allow: String,
    reference_same: String,
    markup_order: String,
    markup_allow: String,
    markup_same: String,
    voice_order: String,
    voice_allow: String,
    voice_take: String,
    subtitles_order: String,
    subtitles_allow: String,
    compilation_member: String,
    compilation_take: String,
    episode: String,
    default: String,
}

impl Names {
    fn read(py: Python<'_>) -> PyResult<Names> {
        let m = py.import_bound("core.contracts")?;
        let s = |name: &str| -> PyResult<String> { m.getattr(name)?.extract() };
        let level = m.getattr("Level")?;
        Ok(Names {
            reference_order: s("REFERENCE_ORDER")?, reference_allow: s("REFERENCE_ALLOW")?, reference_same: s("REFERENCE_SAME")?,
            markup_order: s("MARKUP_ORDER")?, markup_allow: s("MARKUP_ALLOW")?, markup_same: s("MARKUP_SAME")?,
            voice_order: s("VOICE_ORDER")?, voice_allow: s("VOICE_ALLOW")?, voice_take: s("VOICE_TAKE")?,
            subtitles_order: s("SUBTITLES_ORDER")?, subtitles_allow: s("SUBTITLES_ALLOW")?,
            compilation_member: s("COMPILATION_MEMBER")?, compilation_take: s("COMPILATION_TAKE")?,
            episode: level.getattr("EPISODE")?.extract()?, default: level.getattr("DEFAULT")?.extract()?,
        })
    }
}

/// What one lever does to the choices of its episode.
enum Act {
    Nothing,
    Reference(Vec<Variant>),
    Same(Same),
    MarkupSame(Same),
    ReferenceDeny(Variant),
    Markup(Vec<Variant>),
    MarkupDeny(Variant),
    VoiceOrder(String, Vec<(i64, String)>),
    VoiceDeny(Variant),
    VoiceOff(String),
    SubtitleOrder(Vec<Variant>),
    SubtitleDeny(Variant),
    Member(Option<String>),
}

struct Lever {
    own_reference: bool,
    act: Act,
}

fn is_false(value: &Bound<'_, PyAny>) -> bool {
    value.downcast::<PyBool>().map_or(false, |b| !b.is_true())
}

fn dict_item<'py>(each: &Bound<'py, PyAny>, key: &str) -> Option<Bound<'py, PyAny>> {
    each.downcast::<PyDict>().ok()?.get_item(key).ok()?
}

/// The variants of a queue: the items that are dicts with a group, a source id and a player (`queue` of choices_of).
fn queue(value: &Bound<'_, PyAny>) -> Vec<Variant> {
    value.iter().map(|items| items.filter_map(|each| {
        let each = each.ok()?;
        Some((dict_item(&each, "group")?.extract().ok()?, dict_item(&each, "source_id")?.extract().ok()?,
              dict_item(&each, "player")?.extract().ok()?))
    }).collect()).unwrap_or_default()
}

/// The variants of a value of `subtitles.order` (core/subtitle_queue.named_of): a source id that is a boolean is none.
fn named(value: &Bound<'_, PyAny>) -> Vec<Variant> {
    value.iter().map(|items| items.filter_map(|each| {
        let each = each.ok()?;
        let group = dict_item(&each, "group")?;
        let source = dict_item(&each, "source_id")?;
        let player = dict_item(&each, "player")?;
        if !group.is_instance_of::<PyString>() || source.is_instance_of::<PyBool>() || !player.is_instance_of::<PyString>() {
            return None;
        }
        Some((group.extract().ok()?, source.extract().ok()?, player.extract().ok()?))
    }).collect()).unwrap_or_default()
}

/// The groups of equal variants of a value (core/variant_queue.Same.of): a group of more than one, sharing none with the
/// groups before it.
fn same(value: &Bound<'_, PyAny>) -> Same {
    let mut groups: Vec<HashSet<Variant>> = Vec::new();
    for each in value.iter().into_iter().flatten().flatten() {
        if !each.is_instance_of::<PyList>() {
            continue;
        }
        let members: HashSet<Variant> = each.iter().into_iter().flatten().flatten().filter_map(|item| {
            let group = dict_item(&item, "group")?;
            let player = dict_item(&item, "player")?;
            if !group.is_instance_of::<PyString>() || !player.is_instance_of::<PyString>() {
                return None;
            }
            Some((group.extract().ok()?, dict_item(&item, "source_id")?.extract().ok()?, player.extract().ok()?))
        }).collect();
        if members.len() > 1 && !groups.iter().any(|other| !other.is_disjoint(&members)) {
            groups.push(members);
        }
    }
    Same { groups }
}

/// (group, source id, player) of a target `variant:[…]` (core/contracts parse_variant_target): only the one its own
/// writing gives back.
fn variant_target(target: &str) -> Option<Variant> {
    let text = target.strip_prefix("variant:")?;
    let found: serde_json::Value = serde_json::from_str(text).ok()?;
    let items = found.as_array()?;
    if items.len() != 3 {
        return None;
    }
    let group = items[0].as_str().filter(|g| !g.is_empty())?;
    let source = items[1].as_i64()?;
    let player = items[2].as_str().filter(|p| !p.is_empty())?;
    let again = serde_json::to_string(&serde_json::json!([group, source, player])).ok()?;
    (again == text).then(|| (group.to_string(), source, player.to_string()))
}

/// The group of a target `group:<name>` (core/contracts parse_group_target).
fn group_target(target: &str) -> Option<String> {
    target.strip_prefix("group:").filter(|name| !name.is_empty()).map(str::to_string)
}

fn lever_of(names: &Names, lever: &Bound<'_, PyAny>) -> PyResult<Lever> {
    let kind: String = lever.getattr("lever")?.extract()?;
    let level: String = lever.getattr("level")?.extract()?;
    let value = lever.getattr("value")?;
    let target: Option<String> = lever.getattr("target")?.extract()?;
    let target = target.filter(|t| !t.is_empty());
    let n = names;
    let own_reference = level == n.episode && (kind == n.reference_order || kind == n.reference_allow || kind == n.reference_same);
    let list = value.is_instance_of::<PyList>();
    let off = is_false(&value);
    let act = if kind == n.reference_order && list {
        Act::Reference(queue(&value))
    } else if kind == n.reference_same && list {
        Act::Same(same(&value))
    } else if kind == n.markup_same && list {
        Act::MarkupSame(same(&value))
    } else if kind == n.reference_allow && off && target.is_some() {
        target.as_deref().and_then(variant_target).map_or(Act::Nothing, Act::ReferenceDeny)
    } else if kind == n.markup_order && list && level != n.default {
        Act::Markup(queue(&value))
    } else if kind == n.markup_allow && off && target.is_some() {
        target.as_deref().and_then(variant_target).map_or(Act::Nothing, Act::MarkupDeny)
    } else if kind == n.voice_order && list && target.is_some() {
        match target.as_deref().and_then(group_target) {
            Some(group) => Act::VoiceOrder(group, value.iter()?.filter_map(|each| {
                let each = each.ok()?;
                Some((dict_item(&each, "source_id")?.extract().ok()?, dict_item(&each, "player")?.extract().ok()?))
            }).collect()),
            None => Act::Nothing,
        }
    } else if kind == n.voice_allow && off && target.is_some() {
        target.as_deref().and_then(variant_target).map_or(Act::Nothing, Act::VoiceDeny)
    } else if kind == n.voice_take && off && target.is_some() {
        target.as_deref().and_then(group_target).map_or(Act::Nothing, Act::VoiceOff)
    } else if kind == n.subtitles_order && list {
        Act::SubtitleOrder(named(&value))
    } else if kind == n.subtitles_allow && off && target.is_some() {
        target.as_deref().and_then(variant_target).map_or(Act::Nothing, Act::SubtitleDeny)
    } else if kind == n.compilation_member && !value.is_none() {
        Act::Member(if value.is_truthy()? { value.extract::<String>().ok() } else { None })
    } else {
        Act::Nothing
    };
    Ok(Lever { own_reference, act })
}

/// The choices of the user out of the levers of the layout (core/plan/inputs.choices_of): `rows` the rows of the layout,
/// `online` the rows of the output, `episodes` (season, episode, levers) of every episode, `compilation_levers` (key,
/// levers) of every compilation. A lever object shared by many episodes (the value of their level) is read once.
#[pyfunction]
#[pyo3(signature = (rows, online, episodes, compilation_levers, *, players, player_groups, sources, voice_order,
                    off_episodes, titles=None, languages=None))]
#[allow(clippy::too_many_arguments)]
pub fn plan_choices(py: Python<'_>, rows: PyRef<'_, crate::Rows>, online: &Bound<'_, PyAny>, episodes: &Bound<'_, PyAny>,
                    compilation_levers: &Bound<'_, PyAny>, players: Vec<String>, player_groups: &Bound<'_, PyAny>,
                    sources: Vec<i64>, voice_order: HashMap<i64, Vec<String>>, off_episodes: HashSet<Slot>,
                    titles: Option<&Bound<'_, PyDict>>, languages: Option<&Bound<'_, PyDict>>) -> PyResult<ChoicesPy> {
    let names = Names::read(py)?;
    // The row of every compilation by its key, of the rows of the output (core/contracts/compilation.py).
    let online = rows_of(online)?;
    let ids: HashMap<(i64, &str), &RowId> = online.iter().map(|row| ((row.id.source, row.id.key.as_str()), &row.id)).collect();
    let mut by_key: HashMap<String, RowId> = HashMap::new();
    for row in rows.layout.rows() {
        let (Some(season), Some(start), Some(end)) = (row.season, row.episode_start, row.episode_end) else { continue };
        if row.excluded || end <= start {
            continue;
        }
        if let Some(id) = ids.get(&(row.source_id, &*row.row_key)) {
            let key = crate::rows::compilation_key(&row.voice_label, row.source_id, &row.player, season, start, end);
            by_key.insert(key, (*id).clone());
        }
    }
    let mut choices = Choices {
        players, sources, groups: voice_order, off_episodes,
        player_groups: player_groups.getattr("index")?.downcast::<PyDict>()?.iter()
            .map(|(k, v)| Ok((k.extract()?, v.extract()?))).collect::<PyResult<_>>()?,
        titles: titles.map(|t| pairs(t.as_any())).transpose()?.unwrap_or_default(),
        languages: languages.map(|l| pairs(l.as_any())).transpose()?.unwrap_or_default(),
        ..Default::default()
    };
    let mut read: HashMap<usize, Rc<Lever>> = HashMap::new();
    for each in episodes.iter()? {
        let each = each?;
        let slot: Slot = vec![each.get_item(0)?.extract()?, each.get_item(1)?.extract()?];
        for lever in each.get_item(2)?.iter()? {
            let lever = lever?;
            let at = lever.as_ptr() as usize;
            let parsed = match read.get(&at) {
                Some(parsed) => parsed.clone(),
                None => {
                    let parsed = Rc::new(lever_of(&names, &lever)?);
                    read.insert(at, parsed.clone());
                    parsed
                }
            };
            if parsed.own_reference {
                choices.own_reference.insert(slot.clone());
            }
            match &parsed.act {
                Act::Nothing => {}
                Act::Reference(found) => {
                    choices.reference.insert(slot.clone(), found.clone());
                }
                Act::Same(found) => {
                    choices.same.insert(slot.clone(), found.clone());
                }
                Act::MarkupSame(found) => {
                    choices.markup_same.insert(slot.clone(), found.clone());
                }
                Act::ReferenceDeny(variant) => {
                    choices.reference_deny.entry(slot.clone()).or_default().insert(variant.clone());
                }
                Act::Markup(found) => {
                    choices.markup.insert(slot.clone(), found.clone());
                }
                Act::MarkupDeny(variant) => {
                    choices.markup_deny.entry(slot.clone()).or_default().insert(variant.clone());
                }
                Act::VoiceOrder(group, found) => {
                    choices.voice_order.insert((slot.clone(), group.clone()), found.clone());
                }
                Act::VoiceDeny(variant) => {
                    choices.voice_deny.entry(slot.clone()).or_default().insert(variant.clone());
                }
                Act::VoiceOff(group) => {
                    choices.voice_off.entry(slot.clone()).or_default().insert(group.clone());
                }
                Act::SubtitleOrder(found) => {
                    choices.subtitle_order.insert(slot.clone(), found.clone());
                }
                Act::SubtitleDeny(variant) => {
                    choices.subtitle_deny.entry(slot.clone()).or_default().insert(variant.clone());
                }
                Act::Member(key) => {
                    choices.member.insert(slot.clone(), key.as_ref().and_then(|k| by_key.get(k).cloned()));
                }
            }
        }
    }
    for each in compilation_levers.iter()? {
        let each = each?;
        let Ok(key) = each.get_item(0)?.extract::<String>() else { continue };
        let Some(row) = by_key.get(&key) else { continue };
        let mut off = false;
        for lever in each.get_item(1)?.iter()? {
            let lever = lever?;
            if lever.getattr("lever")?.extract::<String>()? == names.compilation_take && is_false(&lever.getattr("value")?) {
                off = true;
            }
        }
        if off {
            choices.off_compilations.insert(row.clone());
        }
    }
    Ok(ChoicesPy { choices: Arc::new(choices), read: PyDict::new_bound(py).unbind() })
}

/// The choices of the planner's inputs, as the core keeps them.
pub fn choices_ref(object: &Bound<'_, PyAny>) -> PyResult<Arc<Choices>> {
    Ok(object.downcast::<ChoicesPy>()?.get().choices.clone())
}
