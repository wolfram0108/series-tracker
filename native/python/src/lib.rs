//! The binding of the native core to Python (`core/_native`): handles on the data of the core. A handle crosses the
//! bus and the boundary of the interpreter as one object; its rows never become objects of Python. The core lets go of
//! the lock of the interpreter while it works, so the work of several series runs on several cores.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;
use pyo3::types::PyTuple;
use st_composition::{levers, rows_of, Row};

/// The rows of the layout of a series, held by the core.
#[pyclass(frozen, module = "core._native")]
struct Rows {
    rows: Vec<Row>,
}

#[pymethods]
impl Rows {
    fn __len__(&self) -> usize {
        self.rows.len()
    }

    /// The rows as tuples in the order of `COLUMNS`: for a check of the core against the module, not for the work.
    fn tuples(&self, py: Python<'_>) -> Vec<PyObject> {
        self.rows.iter().map(|row| {
            let fields: [PyObject; 27] = [
                row.series_id.into_py(py), row.source_id.into_py(py), row.row_key.clone().into_py(py),
                row.excluded.into_py(py), row.exclusion.clone().into_py(py), row.season.into_py(py),
                row.episode_start.into_py(py), row.episode_end.into_py(py), row.part.into_py(py),
                row.voice_group.clone().into_py(py), row.voice_label.clone().into_py(py),
                row.voice_name.clone().into_py(py), row.voice_unknown.into_py(py), row.player.clone().into_py(py),
                row.player_label.clone().into_py(py), row.group.clone().into_py(py), row.text.clone().into_py(py),
                row.heights.clone().into_py(py), row.state.clone().into_py(py), row.published.clone().into_py(py),
                row.link.clone().into_py(py), row.video.clone().into_py(py), row.stale.into_py(py),
                row.timeline.clone().into_py(py), row.track.clone().into_py(py), row.language.clone().into_py(py),
                row.subtitle_type.clone().into_py(py),
            ];
            PyTuple::new_bound(py, fields).into_py(py)
        }).collect()
    }
}

/// The rows of a series read by the core from the table of the module.
#[pyfunction]
fn layout_rows(py: Python<'_>, path: String, series_id: i64) -> PyResult<Rows> {
    py.allow_threads(|| rows_of(&path, series_id)).map(|rows| Rows { rows }).map_err(PyValueError::new_err)
}

/// The rows of a series given by the module as the tuples its query read, in the order of `COLUMNS`.
#[pyfunction]
fn layout_rows_given(given: Vec<Bound<'_, PyAny>>) -> PyResult<Rows> {
    let mut rows = Vec::with_capacity(given.len());
    for each in given {
        let heights: String = each.get_item(17)?.extract()?;
        rows.push(Row {
            series_id: each.get_item(0)?.extract()?, source_id: each.get_item(1)?.extract()?,
            row_key: each.get_item(2)?.extract()?, excluded: each.get_item(3)?.extract::<i64>()? != 0,
            exclusion: each.get_item(4)?.extract()?, season: each.get_item(5)?.extract()?,
            episode_start: each.get_item(6)?.extract()?, episode_end: each.get_item(7)?.extract()?,
            part: each.get_item(8)?.extract()?, voice_group: each.get_item(9)?.extract()?,
            voice_label: each.get_item(10)?.extract()?, voice_name: each.get_item(11)?.extract()?,
            voice_unknown: each.get_item(12)?.extract()?, player: each.get_item(13)?.extract()?,
            player_label: each.get_item(14)?.extract()?, group: each.get_item(15)?.extract()?,
            text: each.get_item(16)?.extract()?,
            heights: st_composition::heights_of(&heights).map_err(PyValueError::new_err)?,
            state: each.get_item(18)?.extract()?, published: each.get_item(19)?.extract()?,
            link: each.get_item(20)?.extract()?, video: each.get_item(21)?.extract()?,
            stale: each.get_item(22)?.extract::<i64>()? != 0, timeline: each.get_item(23)?.extract()?,
            track: each.get_item(24)?.extract()?, language: each.get_item(25)?.extract()?,
            subtitle_type: each.get_item(26)?.extract()?,
        });
    }
    Ok(Rows { rows })
}

// --- the forms of the contracts: made and read in the core only ---------------------------------------------------------

#[pyfunction]
fn variant_target(group: &str, source_id: i64, player: &str) -> String {
    st_contracts::variant_target(group, source_id, player)
}

#[pyfunction]
fn parse_variant_target(target: &str) -> Option<(String, i64, String)> {
    st_contracts::parse_variant_target(target)
}

#[pyfunction]
fn group_target(name: &str) -> String {
    st_contracts::group_target(name)
}

#[pyfunction]
fn parse_group_target(target: &str) -> Option<String> {
    st_contracts::parse_group_target(target)
}

#[pyfunction]
fn compilation_key(group: &str, source_id: i64, player: &str, season: i64, start: i64, end: i64) -> String {
    st_contracts::compilation_key(group, source_id, player, season, start, end)
}

#[pyfunction]
fn parse_compilation_key(key: &str) -> Option<st_contracts::CompilationKey> {
    st_contracts::parse_compilation_key(key)
}

// --- the forms of the levers: the module declares a lever, the core tells its records --------------------------------

fn kinds(target: &str, shape: &str) -> PyResult<(levers::Target, levers::Shape)> {
    let target = levers::Target::of(target).ok_or_else(|| PyValueError::new_err(format!("no target kind {target}")))?;
    let shape = levers::Shape::of(shape).ok_or_else(|| PyValueError::new_err(format!("no value shape {shape}")))?;
    Ok((target, shape))
}

fn json_of(value: &str) -> PyResult<serde_json::Value> {
    serde_json::from_str(value).map_err(|exc| PyValueError::new_err(exc.to_string()))
}

/// A lever the module declares: its form, its default (JSON) and the levels it may be set on (none: all).
#[pyfunction]
#[pyo3(signature = (name, target, shape, default, levels=None))]
fn lever_register(name: &str, target: &str, shape: &str, default: &str, levels: Option<Vec<String>>) -> PyResult<()> {
    let (target, shape) = kinds(target, shape)?;
    levers::register(levers::Form { name: name.to_string(), target, shape, default: json_of(default)?, levels });
    Ok(())
}

#[pyfunction]
fn lever_unregister(name: &str) {
    levers::unregister(name);
}

/// Whether a value (its JSON) is one of the values of a lever of this shape and target.
#[pyfunction]
fn lever_accepts(target: &str, shape: &str, value: &str) -> PyResult<bool> {
    let (target, shape) = kinds(target, shape)?;
    Ok(levers::accepts(shape, target, &json_of(value)?))
}

#[pyfunction]
#[pyo3(signature = (kind, target=None))]
fn lever_accepts_target(kind: &str, target: Option<&str>) -> PyResult<bool> {
    let kind = levers::Target::of(kind).ok_or_else(|| PyValueError::new_err(format!("no target kind {kind}")))?;
    Ok(levers::accepts_target(kind, target))
}

/// None when a record (its value as JSON) is in the registry's terms; else the code of the refusal.
#[pyfunction]
#[pyo3(signature = (lever, target, value, season=None, episode=None, compilation=None))]
fn lever_problem(lever: &str, target: Option<&str>, value: &str, season: Option<i64>, episode: Option<i64>,
                 compilation: Option<&str>) -> PyResult<Option<&'static str>> {
    let value = json_of(value)?;
    Ok(levers::problem(&levers::Record { lever, target, value: &value, season, episode, compilation }))
}

// --- the levers of a series: read and counted by the core --------------------------------------------------------------

/// The levers of a series as the core counts them from the module's records: the headers of the series, of the seasons
/// and of the compilations given (JSON of `levels`), and the levers of each episode given with the compilation it stands
/// in (its key and its JSON as the layout tells it; JSON of `settings`).
#[pyfunction]
fn layout_levers(py: Python<'_>, path: String, series_id: i64, seasons: Vec<i64>, compilations: Vec<(i64, String)>,
                 episodes: Vec<(i64, i64, Option<String>, String)>) -> PyResult<(String, String)> {
    py.allow_threads(move || {
        let conn = st_composition::settings::open(&path)?;
        let index = st_composition::settings::index(&st_composition::settings::settings_of(&conn, series_id)?);
        Ok::<_, String>((st_composition::settings::levels(&index, &seasons, &compilations),
                         st_composition::settings::episode_settings(&index, &episodes)))
    }).map_err(PyValueError::new_err)
}

/// What the layout tells of a series besides its rows, counted by the core from the module's tables: the JSON of
/// (`levels`, `settings`, `compilations`, `voice_orders`).
#[pyfunction]
fn layout_derived(py: Python<'_>, path: String, series_id: i64) -> PyResult<(String, String, String, String)> {
    py.allow_threads(move || st_composition::derived(&path, series_id))
        .map(|each| (each.levels, each.settings, each.compilations, each.voice_orders)).map_err(PyValueError::new_err)
}

/// The layout of a series (or the part asked) as the JSON of `CompositionReply`, counted by the core from the module's
/// tables: `fields` in the order of the reply, `episodes` the ones a part `levers` holds (none: all), `given` the parts the
/// module gives as they are.
#[pyfunction]
#[pyo3(signature = (path, series_id, part, fields, episodes, include_excluded, given))]
fn layout_json(py: Python<'_>, path: String, series_id: i64, part: String, fields: Vec<String>,
               episodes: Option<Vec<(i64, i64)>>, include_excluded: bool, given: HashMap<String, String>) -> PyResult<String> {
    py.allow_threads(move || {
        let asked = st_composition::Asked { part: &part, fields: &fields, episodes: episodes.map(|each| each.into_iter().collect()),
                                            include_excluded, given: &given };
        st_composition::layout_json(&path, series_id, &asked)
    }).map_err(PyValueError::new_err)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("COLUMNS", st_composition::COLUMNS)?;
    m.add("UNKNOWN_VOICE_LABEL", st_composition::compilations::UNKNOWN_VOICE_LABEL)?;
    for (name, value) in st_contracts::names::ALL {
        m.add(*name, *value)?;
    }
    m.add_class::<Rows>()?;
    for function in [wrap_pyfunction!(layout_rows, m)?, wrap_pyfunction!(layout_rows_given, m)?,
                     wrap_pyfunction!(variant_target, m)?, wrap_pyfunction!(parse_variant_target, m)?,
                     wrap_pyfunction!(group_target, m)?, wrap_pyfunction!(parse_group_target, m)?,
                     wrap_pyfunction!(compilation_key, m)?, wrap_pyfunction!(parse_compilation_key, m)?,
                     wrap_pyfunction!(lever_register, m)?, wrap_pyfunction!(lever_unregister, m)?,
                     wrap_pyfunction!(lever_accepts, m)?, wrap_pyfunction!(lever_accepts_target, m)?,
                     wrap_pyfunction!(lever_problem, m)?, wrap_pyfunction!(layout_levers, m)?,
                     wrap_pyfunction!(layout_derived, m)?, wrap_pyfunction!(layout_json, m)?] {
        m.add_function(function)?;
    }
    Ok(())
}
