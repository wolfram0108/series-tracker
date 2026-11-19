//! The binding of the native core to Python (`core/_native`): handles on the data of the core. A handle crosses the
//! bus and the boundary of the interpreter as one object; its rows never become objects of Python. The core lets go of
//! the lock of the interpreter while it works, so the work of several series runs on several cores.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyTuple;
use st_composition::{rows_of, Row};

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

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("COLUMNS", st_composition::COLUMNS)?;
    m.add_class::<Rows>()?;
    m.add_function(wrap_pyfunction!(layout_rows, m)?)?;
    m.add_function(wrap_pyfunction!(layout_rows_given, m)?)?;
    Ok(())
}
