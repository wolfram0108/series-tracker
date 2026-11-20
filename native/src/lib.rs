//! `core.native`: the count of the data of a series in compiled code — the rules constructor, the tables of pages and the
//! rows of layouts kept by columns, the projection of a layout — bound to Python. Every heavy call lets the interpreter go
//! (the loop of the service runs while a count runs in the pool of computation).

mod cols;
mod layout;
mod plan;
mod plan_py;
mod rows;
mod rules;
mod table;
mod text;

use pyo3::exceptions::{PyIndexError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use cols::Column;

fn value_error(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn bytes<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    PyBytes::new_bound(py, data)
}

// --- the tables of pages ----------------------------------------------------------------------------------------

/// The rows of a table, from the JSON of their models (TableRow), as columns.
#[pyfunction]
fn encode_table<'py>(py: Python<'py>, json: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| table::encode_json(json)).map_err(value_error)?;
    Ok(bytes(py, &made))
}

/// The rows of a table kept by columns as the JSON of their models.
#[pyfunction]
fn table_json<'py>(py: Python<'py>, rows: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| {
        let store = cols::Store::of(rows.to_vec(), table::COLUMNS)?;
        Ok::<_, String>(table::to_json(&store))
    }).map_err(value_error)?;
    Ok(bytes(py, &made))
}

/// Every record of a field of columns as JSON objects of its columns (for a reader that knows none of them).
#[pyfunction]
fn records<'py>(py: Python<'py>, field: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| cols::View::of(field).map(|v| v.records_json(field))).map_err(value_error)?;
    Ok(bytes(py, made.as_bytes()))
}

/// How many rows a field of columns holds.
#[pyfunction]
fn count(rows: &[u8]) -> PyResult<usize> {
    Ok(cols::View::of(rows).map_err(value_error)?.rows)
}

// --- the rules constructor --------------------------------------------------------------------------------------

// The threads one answer of the constructor takes at most, and the fewest lines worth a thread of their own.
const APPLY_THREADS: usize = 4;
const APPLY_PART: usize = 1000;

/// A profile of rules compiled once; the rules that do not compile are left out and named in `invalid`.
#[pyclass(frozen, module = "core.native")]
struct Profile {
    profile: rules::Profile,
}

/// The lines of a field of columns, borrowed.
fn lines_of<'a>(view: &cols::View<'a>) -> Vec<rules::Line<'a>> {
    let (keys, texts, players, voices) = (view.text("key"), view.text("text"), view.text("player"), view.text("voice"));
    (0..view.rows).map(|i| rules::Line {
        key: keys.get(i).unwrap_or(""), text: texts.get(i).unwrap_or(""), player: players.get(i).unwrap_or(""),
        voice: voices.get(i).unwrap_or(""),
    }).collect()
}

/// The answer of the constructor to lines, of what the rules made of each: the default season the card's, the first part
/// settled; refused for keys that repeat.
fn answered(given: &[rules::Line], outcomes: Vec<rules::Outcome>, card: Option<&str>) -> Result<Vec<u8>, String> {
    if outcomes.len() != given.len() {
        return Err("an outcome for every line".into());
    }
    settled(given, outcomes.into_iter().map(|o| rules::Answer::of(o, card)).collect())
}

/// The answers with their first parts settled, as columns; refused for keys that repeat.
fn settled(given: &[rules::Line], mut answers: Vec<rules::Answer>) -> Result<Vec<u8>, String> {
    let mut seen = HashSet::new();
    let mut repeated: Vec<&str> = given.iter().filter(|l| !seen.insert(l.key)).map(|l| l.key).collect();
    if !repeated.is_empty() {
        repeated.sort();
        repeated.dedup();
        return Err(text::reason("apply_keys_repeated", &[("keys", text::Param::Json(Value::from(repeated)))]));
    }
    rules::settle_parts(given, &mut answers);
    Ok(answer_columns(given.iter().map(|l| l.key).collect(), &answers))
}

/// The answer to lines (columns) of the outcomes given for them (JSON: [{excluded, extracted, events, errors}]), as
/// `Profile.apply` makes it of its own.
#[pyfunction]
#[pyo3(signature = (lines, outcomes, card=None))]
fn answers<'py>(py: Python<'py>, lines: &[u8], outcomes: &str, card: Option<&str>) -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| -> Result<Vec<u8>, String> {
        let view = cols::View::of(lines)?;
        let parsed: Vec<rules::Outcome> = serde_json::from_str(outcomes).map_err(|e| e.to_string())?;
        answered(&lines_of(&view), parsed, card)
    }).map_err(value_error)?;
    Ok(bytes(py, &made))
}

/// Answers given as the JSON of {key: RuleResult}, as columns (in the order of the keys).
#[pyfunction]
fn encode_answers<'py>(py: Python<'py>, json: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    struct ByKey(Vec<(String, rules::Extracted)>);
    impl<'de> serde::Deserialize<'de> for ByKey {
        fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
            struct Entries;
            impl<'de> serde::de::Visitor<'de> for Entries {
                type Value = ByKey;
                fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                    f.write_str("an object of answers")
                }
                fn visit_map<A: serde::de::MapAccess<'de>>(self, mut map: A) -> Result<ByKey, A::Error> {
                    let mut out = Vec::new();
                    while let Some(entry) = map.next_entry::<String, rules::Extracted>()? {
                        out.push(entry);
                    }
                    Ok(ByKey(out))
                }
            }
            deserializer.deserialize_map(Entries)
        }
    }
    let made = py.allow_threads(|| -> Result<Vec<u8>, String> {
        let ByKey(given) = serde_json::from_slice(json).map_err(|e| e.to_string())?;
        let mut keys = Vec::new();
        let mut answers = Vec::new();
        for (key, mut fields) in given {
            let int = |v: Option<Value>| v.and_then(|v| v.as_i64());
            let events = fields.pop("events").map_or("[]".to_string(), |v| v.to_string());
            answers.push(rules::Answer {
                excluded: fields.pop("excluded").and_then(|v| v.as_bool()).unwrap_or(false),
                season: int(fields.pop("season")), episode: int(fields.pop("episode")), start: int(fields.pop("start")),
                end: int(fields.pop("end")), part: int(fields.pop("part")),
                voiceover: fields.pop("voiceover").and_then(|v| v.as_str().map(str::to_string)),
                events, errors: fields.pop("errors").map_or("[]".to_string(), |v| v.to_string()), extras: fields,
            });
            keys.push(key);
        }
        Ok(answer_columns(keys.iter().map(String::as_str).collect(), &answers))
    }).map_err(value_error)?;
    Ok(bytes(py, &made))
}

fn answer_columns(keys: Vec<&str>, answers: &[rules::Answer]) -> Vec<u8> {
    let i = |f: &dyn Fn(&rules::Answer) -> Option<i64>| Column::Int(answers.iter().map(f).collect());
    let extras: Vec<String> = answers.iter().map(|a| {
        let mut out = String::new();
        a.extras.write_json(&mut out);
        out
    }).collect();
    let columns = vec![
        ("key", Column::Strs(keys)), ("excluded", Column::Flag(answers.iter().map(|a| a.excluded).collect())),
        ("season", i(&|a| a.season)), ("episode", i(&|a| a.episode)), ("start", i(&|a| a.start)), ("end", i(&|a| a.end)),
        ("part", i(&|a| a.part)), ("voiceover", Column::MaybeStrs(answers.iter().map(|a| a.voiceover.as_deref()).collect())),
        ("extras", Column::Strs(extras.iter().map(String::as_str).collect())),
        ("events", Column::Strs(answers.iter().map(|a| a.events.as_str()).collect())),
        ("errors", Column::Strs(answers.iter().map(|a| a.errors.as_str()).collect())),
    ];
    cols::encode(answers.len(), &columns)
}

#[pymethods]
impl Profile {
    /// `rules`: the JSON of the rules of the profile as the tables of the constructor keep them.
    #[new]
    fn new(py: Python<'_>, rules: &str) -> PyResult<Self> {
        let parsed: Vec<Value> = serde_json::from_str(rules).map_err(|e| value_error(e.to_string()))?;
        Ok(Profile { profile: py.allow_threads(|| rules::Profile::compile(&parsed)) })
    }

    /// [{rule_id, name, error}] of the rules left out, as JSON.
    #[getter]
    fn invalid(&self) -> String {
        Value::from(self.profile.invalid.clone()).to_string()
    }

    /// What the profile makes of one title: {excluded, extracted, events, errors} as JSON.
    fn process(&self, title: &str) -> String {
        self.profile.process(title)
    }

    /// The answers to lines kept by columns (key, text, player, voice), as columns: one per line, the default season the
    /// card's (`card`, its text), the first part settled. Refused for keys that repeat.
    #[pyo3(signature = (lines, card=None))]
    fn apply<'py>(&self, py: Python<'py>, lines: &[u8], card: Option<&str>) -> PyResult<Bound<'py, PyBytes>> {
        let made = py.allow_threads(|| -> Result<Vec<u8>, String> {
            let view = cols::View::of(lines)?;
            let given = lines_of(&view);
            // A page of thousands of lines is read in parts side by side; the parts are settled together after.
            let parts = std::thread::available_parallelism().map_or(1, |n| n.get()).min(APPLY_THREADS)
                .min(given.len() / APPLY_PART + 1);
            let size = given.len().div_ceil(parts.max(1)).max(1);
            let answers: Vec<rules::Answer> = std::thread::scope(|scope| {
                let made: Vec<_> = given.chunks(size).map(|chunk| scope.spawn(move || {
                    chunk.iter().map(|l| self.profile.answer(l.text, card)).collect::<Vec<_>>()
                })).collect();
                made.into_iter().flat_map(|each| each.join().expect("a part of the answer")).collect()
            });
            settled(&given, answers)
        }).map_err(value_error)?;
        Ok(bytes(py, &made))
    }
}

/// The reason a rule (JSON) is refused on saving; None for a rule that compiles.
#[pyfunction]
fn rule_error(rule: &str) -> PyResult<Option<String>> {
    let parsed: Value = serde_json::from_str(rule).map_err(|e| value_error(e.to_string()))?;
    Ok(rules::invalid_reason(&parsed))
}

/// The words of the constructor the engine understands, by kind.
#[pyfunction]
fn vocabulary() -> HashMap<&'static str, Vec<&'static str>> {
    rules::vocabulary().into_iter().collect()
}

/// The intervals of the spec of a range block («1, 3-10»): sorted, adjacent ones merged.
#[pyfunction]
fn range_spec(raw: &str) -> PyResult<Vec<(i64, i64)>> {
    rules::range_spec(raw).map_err(value_error)
}

/// Lines given one by one (a release's file names, typed lines) as columns.
#[pyfunction]
fn encode_lines<'py>(py: Python<'py>, keys: Vec<String>, texts: Vec<String>, players: Vec<String>, voices: Vec<String>)
    -> PyResult<Bound<'py, PyBytes>> {
    let n = keys.len();
    if texts.len() != n || players.len() != n || voices.len() != n {
        return Err(value_error("the fields of the lines differ in length"));
    }
    let columns = vec![("key", Column::Text(keys)), ("text", Column::Text(texts)), ("player", Column::Text(players)),
                       ("voice", Column::Text(voices))];
    Ok(bytes(py, &cols::encode(n, &columns)))
}

fn answer_json(view: &cols::View, i: usize, out: &mut String) {
    let int = |name: &str| view.int(name).get(i).map_or("null".to_string(), |n| n.to_string());
    out.push_str("{\"excluded\":");
    out.push_str(if view.flag("excluded").get(i) { "true" } else { "false" });
    for name in ["season", "episode", "start", "end", "part"] {
        out.push_str(",\"");
        out.push_str(name);
        out.push_str("\":");
        out.push_str(&int(name));
    }
    out.push_str(",\"voiceover\":");
    out.push_str(&view.text("voiceover").get(i).map_or("null".to_string(), |v| serde_json::to_string(v).unwrap()));
    out.push_str(",\"events\":");
    out.push_str(view.text("events").get(i).filter(|t| !t.is_empty()).unwrap_or("[]"));
    out.push_str(",\"errors\":");
    out.push_str(view.text("errors").get(i).filter(|t| !t.is_empty()).unwrap_or("[]"));
    let extras = view.text("extras").get(i).unwrap_or("{}");
    if extras.len() > 2 {
        out.push(',');
        out.push_str(&extras[1..extras.len() - 1]);
    }
    out.push('}');
}

/// The answers kept by columns as the JSON of {key: RuleResult}, in the order of the lines.
#[pyfunction]
fn answers_json<'py>(py: Python<'py>, answers: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| -> Result<String, String> {
        if answers.is_empty() {
            return Ok("{}".into());
        }
        let view = cols::View::of(answers)?;
        let keys = view.text("key");
        let mut out = String::from("{");
        for i in 0..view.rows {
            if i > 0 {
                out.push(',');
            }
            out.push_str(&serde_json::to_string(keys.get(i).unwrap_or("")).unwrap());
            out.push(':');
            answer_json(&view, i, &mut out);
        }
        out.push('}');
        Ok(out)
    }).map_err(value_error)?;
    Ok(bytes(py, made.as_bytes()))
}

// --- the count of a layout --------------------------------------------------------------------------------------

fn get<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<T>> {
    match d.get_item(key)? {
        Some(value) if !value.is_none() => Ok(Some(value.extract()?)),
        _ => Ok(None),
    }
}

fn source_of(d: &Bound<'_, PyDict>) -> PyResult<layout::Source> {
    Ok(layout::Source {
        id: get(d, "id")?.unwrap_or(0), series_id: get(d, "series_id")?.unwrap_or(0),
        position: get(d, "position")?.unwrap_or(0), url: get(d, "url")?.unwrap_or_default(),
        profile: get(d, "parser_profile_id")?, vibix_season: get(d, "vibix_season")?,
        players_off: get(d, "players_off")?.unwrap_or_default(),
    })
}

fn head_of(d: &Bound<'_, PyDict>) -> PyResult<table::Head> {
    Ok(table::Head {
        url: get(d, "url")?.unwrap_or_default(), names: get(d, "names")?.unwrap_or_default(),
        title: get(d, "title")?.unwrap_or_default(), channel: get(d, "channel")?.unwrap_or_default(),
        read_at: get(d, "read_at")?, unread: get(d, "unread")?.unwrap_or_default(),
    })
}

/// What an input of a count holds, taken out of its dict: {source, head?, rows?, answers?, failure?, table_url?, everywhere}.
struct Given {
    source: layout::Source,
    head: Option<table::Head>,
    failure: Option<String>,
    table_url: Option<String>,
    everywhere: HashSet<String>,
}

/// The fields of the inputs (their rows and answers), held while a count reads them in place.
type Fields<'py> = Vec<(Option<Bound<'py, PyBytes>>, Option<Bound<'py, PyBytes>>)>;

fn given<'py>(item: &Bound<'py, PyAny>) -> PyResult<(Given, (Option<Bound<'py, PyBytes>>, Option<Bound<'py, PyBytes>>))> {
    let d = item.downcast::<PyDict>()?;
    let source = d.get_item("source")?.ok_or_else(|| value_error("an input without its source"))?;
    let head = match d.get_item("head")? {
        Some(h) if !h.is_none() => Some(head_of(h.downcast::<PyDict>()?)?),
        _ => None,
    };
    let blob = |key: &str| -> PyResult<Option<Bound<'py, PyBytes>>> {
        match d.get_item(key)? {
            Some(b) if !b.is_none() => Ok(Some(b.downcast_into::<PyBytes>()?)),
            _ => Ok(None),
        }
    };
    Ok((Given {
        source: source_of(source.downcast::<PyDict>()?)?, head, failure: get(d, "failure")?,
        table_url: get(d, "table_url")?,
        everywhere: get::<Vec<String>>(d, "everywhere")?.unwrap_or_default().into_iter().collect(),
    }, (blob("rows")?, blob("answers")?)))
}

fn gathered<'py>(items: &Bound<'py, PyList>) -> PyResult<(Vec<Given>, Fields<'py>)> {
    let mut given_all = Vec::new();
    let mut fields = Vec::new();
    for item in items.iter() {
        let (g, f) = given(&item)?;
        given_all.push(g);
        fields.push(f);
    }
    Ok((given_all, fields))
}

/// The inputs of a count borrowing the fields given (their tables and answers are read in place).
fn inputs<'a>(given: &'a [Given], raw: &'a [(Option<&'a [u8]>, Option<&'a [u8]>)]) -> Result<Vec<layout::Input<'a>>, String> {
    given.iter().zip(raw).map(|(g, (rows, answers))| {
        let table = match (&g.head, rows) {
            (Some(head), Some(rows)) => Some(table::Table::of(head, rows)?),
            _ => None,
        };
        Ok(layout::Input {
            source: layout::Source { players_off: g.source.players_off.clone(), url: g.source.url.clone(), ..g.source },
            table, answers: answers.map(layout::Answers::of).transpose()?, failure: g.failure.clone(),
            table_url: g.table_url.clone(), everywhere: g.everywhere.clone(),
        })
    }).collect()
}

fn raw<'a>(fields: &'a Fields) -> Vec<(Option<&'a [u8]>, Option<&'a [u8]>)> {
    fields.iter().map(|(rows, answers)| (rows.as_ref().map(|b| b.as_bytes()), answers.as_ref().map(|b| b.as_bytes())))
        .collect()
}

/// The lines the constructor is asked about a source (one input of a count), as columns.
#[pyfunction]
fn lines<'py>(py: Python<'py>, item: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let (one, field) = given(item)?;
    let (g, f) = (vec![one], vec![field]);
    let r = raw(&f);
    let made = py.allow_threads(|| -> Result<Vec<u8>, String> {
        let made = inputs(&g, &r)?;
        let input = &made[0];
        let table = input.table.as_ref().ok_or("lines of a source without a table")?;
        Ok(layout::lines(&input.source, table, &input.everywhere))
    }).map_err(value_error)?;
    Ok(bytes(py, &made))
}

/// The distinct voice-over names of the rows the constructors answered.
#[pyfunction]
fn voice_names(py: Python<'_>, items: &Bound<'_, PyList>) -> PyResult<Vec<String>> {
    let (g, f) = gathered(items)?;
    let r = raw(&f);
    py.allow_threads(|| -> Result<Vec<String>, String> { Ok(layout::voice_names(&inputs(&g, &r)?)) }).map_err(value_error)
}

/// The layout of a series: (rows of sound, rows of subtitles, the summary of every source as JSON, the numbering of
/// unknown voice-overs).
#[pyfunction]
fn project(py: Python<'_>, items: &Bound<'_, PyList>, groups: HashMap<String, Option<String>>,
           numbering: Vec<(String, i64)>) -> PyResult<(Rows, Rows, Vec<String>, Vec<(String, i64)>)> {
    let (g, f) = gathered(items)?;
    let r = raw(&f);
    let made = py.allow_threads(|| -> Result<_, String> {
        let made = layout::project(&inputs(&g, &r)?, &groups, numbering);
        // The fields made are the rows the module keeps: moved into them, not copied.
        Ok((rows::Layout::of(made.rows)?, rows::Layout::of(made.subtitles)?, made.summaries, made.numbering))
    }).map_err(value_error)?;
    let (sound, subtitles, summaries, numbering) = made;
    Ok((Rows { layout: Arc::new(sound) }, Rows { layout: Arc::new(subtitles) }, summaries, numbering))
}

/// The lines of a page as a window shows them (PreviewLine): the key, the line, the answer when a profile gave one, the
/// resolutions of a row that plays.
#[pyfunction]
#[pyo3(signature = (lines, rows, answers=None))]
fn preview_lines<'py>(py: Python<'py>, lines: &[u8], rows: &[u8], answers: Option<&[u8]>)
    -> PyResult<Bound<'py, PyBytes>> {
    let made = py.allow_threads(|| -> Result<String, String> {
        let view = cols::View::of(lines)?;
        let table = cols::View::of(rows)?;
        let (keys, texts) = (view.text("key"), view.text("text"));
        let (row_keys, states, heights) = (table.text("key"), table.text("state"), table.ints("heights"));
        let playing: HashMap<&str, usize> = (0..table.rows)
            .filter(|i| states.get(*i) == Some("playable")).filter_map(|i| row_keys.get(i).map(|k| (k, i))).collect();
        let answered = answers.filter(|a| !a.is_empty()).map(cols::View::of).transpose()?;
        let at: HashMap<&str, usize> = answered.as_ref().map(|a| {
            let k = a.text("key");
            (0..a.rows).filter_map(|i| k.get(i).map(|key| (key, i))).collect()
        }).unwrap_or_default();
        let mut out = String::from("[");
        for i in 0..view.rows {
            let key = keys.get(i).unwrap_or("");
            if i > 0 {
                out.push(',');
            }
            out.push_str("{\"key\":");
            out.push_str(&serde_json::to_string(key).unwrap());
            out.push_str(",\"line\":");
            out.push_str(&serde_json::to_string(texts.get(i).unwrap_or("")).unwrap());
            out.push_str(",\"result\":");
            match (&answered, at.get(key)) {
                (Some(a), Some(j)) => answer_json(a, *j, &mut out),
                _ => out.push_str("null"),
            }
            out.push_str(",\"heights\":");
            let shown: Vec<i64> = playing.get(key).map(|j| heights.get(*j).collect()).unwrap_or_default();
            out.push_str(&Value::from(shown).to_string());
            out.push('}');
        }
        out.push(']');
        Ok(out)
    }).map_err(value_error)?;
    Ok(bytes(py, made.as_bytes()))
}

// --- the rows of a layout ---------------------------------------------------------------------------------------

/// The rows of a layout kept by columns, read in place: a sequence of rows with the fields of CompositionRow.
#[pyclass(frozen, sequence, module = "core.native")]
struct Rows {
    layout: Arc<rows::Layout>,
}

#[pymethods]
impl Rows {
    #[new]
    #[pyo3(signature = (blob=None))]
    fn new(blob: Option<&[u8]>) -> PyResult<Self> {
        Ok(Rows { layout: Arc::new(rows::Layout::of(blob.map(<[u8]>::to_vec).unwrap_or_default()).map_err(value_error)?) })
    }

    /// The rows from the JSON of their models (CompositionRow).
    #[staticmethod]
    fn from_json(py: Python<'_>, json: &[u8]) -> PyResult<Self> {
        let made = py.allow_threads(|| rows::encode_json(json).and_then(rows::Layout::of)).map_err(value_error)?;
        Ok(Rows { layout: Arc::new(made) })
    }

    /// The field as it is stored.
    #[getter]
    fn blob<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        bytes(py, &self.layout.store.bytes)
    }

    fn __len__(&self) -> usize {
        self.layout.len()
    }

    fn __getitem__(&self, index: isize) -> PyResult<Row> {
        let n = self.layout.len() as isize;
        let at = if index < 0 { index + n } else { index };
        if at < 0 || at >= n {
            return Err(PyIndexError::new_err("row index out of range"));
        }
        Ok(Row { layout: self.layout.clone(), at: at as usize })
    }

    fn __iter__(&self) -> RowsIter {
        RowsIter { layout: self.layout.clone(), at: std::sync::atomic::AtomicUsize::new(0) }
    }

    fn __eq__(&self, other: &Bound<'_, PyAny>) -> bool {
        other.downcast::<Rows>().map_or(false, |o| o.get().layout.store.bytes == self.layout.store.bytes)
    }

    /// The JSON of the rows as the model writes it; the excluded ones only when asked.
    #[pyo3(signature = (include_excluded=true))]
    fn json<'py>(&self, py: Python<'py>, include_excluded: bool) -> Bound<'py, PyBytes> {
        let layout = self.layout.clone();
        let made = py.allow_threads(move || layout.json(include_excluded));
        bytes(py, &made)
    }

    /// The (season, episode) pairs the layout covers.
    fn episodes(&self, py: Python<'_>) -> HashSet<(i64, i64)> {
        let layout = self.layout.clone();
        py.allow_threads(move || layout.episodes())
    }

    /// The episodes whose rows differ from those of `before`; None when the difference cannot be told as episodes.
    fn touched(&self, py: Python<'_>, before: &Rows) -> Option<HashSet<(i64, i64)>> {
        let (now, was) = (self.layout.clone(), before.layout.clone());
        py.allow_threads(move || now.touched(&was))
    }

    /// [(old label, new label)] of the voice-overs that kept their names and changed their group since `before`.
    fn renames(&self, py: Python<'_>, before: &Rows) -> Vec<(String, String)> {
        let (now, was) = (self.layout.clone(), before.layout.clone());
        py.allow_threads(move || now.renames(&was))
    }

    /// [(season, labels)] of the rows of the output, in the order of the layout.
    fn season_labels(&self) -> Vec<(i64, Vec<String>)> {
        self.layout.season_labels()
    }

    /// {(season, label): [episodes]} of the rows of the output.
    fn season_episodes(&self) -> HashMap<(i64, String), Vec<i64>> {
        self.layout.season_episodes().into_iter().map(|(k, v)| {
            let mut v: Vec<i64> = v.into_iter().collect();
            v.sort();
            (k, v)
        }).collect()
    }

    /// [(key, source id, row key, player, group, height, start, end)] of the compilations of the layout.
    fn compilations(&self) -> Vec<(String, i64, String, String, String, Option<i64>, i64, i64)> {
        self.layout.compilations()
    }

    /// The rows of the output (`in_output`) as the planner reads them, each with its place in the layout (every row
    /// counted): (place, source id, row key, video, season, start, end, player, group, height, link, label, part,
    /// playable, current, player label, timeline, track, language, subtitle type).
    #[allow(clippy::type_complexity)]
    fn output<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let rows = PyList::empty_bound(py);
        for (place, row) in self.layout.rows().enumerate() {
            if !row.output() {
                continue;
            }
            let start = row.episode_start.unwrap_or(0);
            // Three tuples: the binding makes tuples of twelve fields at most.
            let head = (place, row.source_id, row.row_key, row.video, row.season.unwrap_or(0), start,
                        row.episode_end.unwrap_or(start), row.player);
            let body = (row.voice_group.as_deref().unwrap_or(""), row.heights.iter().copied().max().unwrap_or(0), row.link,
                        row.voice_label.as_ref(), row.part, row.state == "playable", !row.stale, row.player_label);
            let tail = (row.timeline, row.track, row.language, row.subtitle_type);
            let made: (PyObject, PyObject, PyObject) = (head.into_py(py), body.into_py(py), tail.into_py(py));
            rows.append(made)?;
        }
        Ok(rows)
    }

    /// {(season, episode): the best height of its single videos that play}.
    fn singles(&self) -> HashMap<(i64, i64), i64> {
        self.layout.singles()
    }
}

#[pyclass(module = "core.native")]
struct RowsIter {
    layout: Arc<rows::Layout>,
    at: std::sync::atomic::AtomicUsize,
}

#[pymethods]
impl RowsIter {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&self) -> Option<Row> {
        let at = self.at.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        (at < self.layout.len()).then(|| Row { layout: self.layout.clone(), at })
    }
}

/// One row of a layout, its fields read from the columns when asked.
#[pyclass(frozen, module = "core.native")]
struct Row {
    layout: Arc<rows::Layout>,
    at: usize,
}

#[pymethods]
impl Row {
    #[getter]
    fn series_id(&self) -> i64 {
        self.layout.store.int(rows::c::SERIES_ID, self.at).unwrap_or(0)
    }
    #[getter]
    fn source_id(&self) -> i64 {
        self.layout.store.int(rows::c::SOURCE_ID, self.at).unwrap_or(0)
    }
    #[getter]
    fn row_key(&self) -> &str {
        self.layout.store.str(rows::c::ROW_KEY, self.at)
    }
    #[getter]
    fn excluded(&self) -> bool {
        self.layout.store.flag(rows::c::EXCLUDED, self.at)
    }
    #[getter]
    fn exclusion(&self) -> Option<&str> {
        self.layout.store.text(rows::c::EXCLUSION, self.at)
    }
    #[getter]
    fn season(&self) -> Option<i64> {
        self.layout.store.int(rows::c::SEASON, self.at)
    }
    #[getter]
    fn episode_start(&self) -> Option<i64> {
        self.layout.store.int(rows::c::START, self.at)
    }
    #[getter]
    fn episode_end(&self) -> Option<i64> {
        self.layout.store.int(rows::c::END, self.at)
    }
    #[getter]
    fn part(&self) -> Option<i64> {
        self.layout.store.int(rows::c::PART, self.at)
    }
    #[getter]
    fn voice_group(&self) -> Option<&str> {
        self.layout.store.text(rows::c::VOICE_GROUP, self.at)
    }
    #[getter]
    fn voice_label(&self) -> &str {
        self.layout.store.str(rows::c::VOICE_LABEL, self.at)
    }
    #[getter]
    fn voice_name(&self) -> &str {
        self.layout.store.str(rows::c::VOICE_NAME, self.at)
    }
    #[getter]
    fn voice_unknown(&self) -> Option<i64> {
        self.layout.store.int(rows::c::VOICE_UNKNOWN, self.at)
    }
    #[getter]
    fn player(&self) -> &str {
        self.layout.store.str(rows::c::PLAYER, self.at)
    }
    #[getter]
    fn player_label(&self) -> &str {
        self.layout.store.str(rows::c::PLAYER_LABEL, self.at)
    }
    #[getter]
    fn group(&self) -> &str {
        self.layout.store.str(rows::c::GROUP, self.at)
    }
    #[getter]
    fn text(&self) -> &str {
        self.layout.store.str(rows::c::TEXT, self.at)
    }
    #[getter]
    fn heights(&self) -> Vec<i64> {
        self.layout.store.ints(rows::c::HEIGHTS, self.at)
    }
    #[getter]
    fn state(&self) -> &str {
        self.layout.store.str(rows::c::STATE, self.at)
    }
    #[getter]
    fn published(&self) -> Option<&str> {
        self.layout.store.text(rows::c::PUBLISHED, self.at)
    }
    #[getter]
    fn link(&self) -> &str {
        self.layout.store.str(rows::c::LINK, self.at)
    }
    #[getter]
    fn video(&self) -> &str {
        self.layout.store.str(rows::c::VIDEO, self.at)
    }
    #[getter]
    fn timeline(&self) -> &str {
        self.layout.store.str(rows::c::TIMELINE, self.at)
    }
    #[getter]
    fn track(&self) -> &str {
        self.layout.store.text(rows::c::TRACK, self.at).unwrap_or("sound")
    }
    #[getter]
    fn language(&self) -> &str {
        self.layout.store.str(rows::c::LANGUAGE, self.at)
    }
    #[getter]
    fn subtitle_type(&self) -> &str {
        self.layout.store.str(rows::c::SUBTITLE_TYPE, self.at)
    }
    #[getter]
    fn stale(&self) -> bool {
        self.layout.store.flag(rows::c::STALE, self.at)
    }
    /// The rules gave the row its numbers and the groups took it.
    #[getter]
    fn in_output(&self) -> bool {
        self.layout.row(self.at).output()
    }
    #[getter]
    fn is_compilation(&self) -> bool {
        matches!((self.episode_start(), self.episode_end()), (Some(s), Some(e)) if e > s)
    }
    #[getter]
    fn height(&self) -> Option<i64> {
        self.heights().into_iter().max()
    }
    /// The voice-over of the row is one of the user's groups.
    #[getter]
    fn grouped(&self) -> bool {
        self.voice_group().is_some()
    }
    /// The configuration took this video out on purpose (an exclusion of the rules, the source or the settings).
    #[getter]
    fn left_out(&self) -> bool {
        self.excluded() && matches!(self.exclusion(), Some("rule" | "source" | "settings"))
    }
    /// The layout has a word on this video: it numbers it, or the configuration took it out.
    #[getter]
    fn decided(&self) -> bool {
        !self.excluded() || self.left_out()
    }
    /// The JSON of the row as the model writes it.
    fn json(&self) -> String {
        serde_json::to_string(&self.layout.row(self.at)).unwrap()
    }
    fn __repr__(&self) -> String {
        format!("Row({})", self.json())
    }
}

#[pymodule]
fn native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(encode_table, m)?)?;
    m.add_function(wrap_pyfunction!(table_json, m)?)?;
    m.add_function(wrap_pyfunction!(count, m)?)?;
    m.add_function(wrap_pyfunction!(records, m)?)?;
    m.add_function(wrap_pyfunction!(rule_error, m)?)?;
    m.add_function(wrap_pyfunction!(encode_lines, m)?)?;
    m.add_function(wrap_pyfunction!(vocabulary, m)?)?;
    m.add_function(wrap_pyfunction!(range_spec, m)?)?;
    m.add_function(wrap_pyfunction!(answers_json, m)?)?;
    m.add_function(wrap_pyfunction!(answers, m)?)?;
    m.add_function(wrap_pyfunction!(encode_answers, m)?)?;
    m.add_function(wrap_pyfunction!(lines, m)?)?;
    m.add_function(wrap_pyfunction!(voice_names, m)?)?;
    m.add_function(wrap_pyfunction!(project, m)?)?;
    m.add_function(wrap_pyfunction!(preview_lines, m)?)?;
    m.add_function(wrap_pyfunction!(plan_py::plan_goals, m)?)?;
    m.add_class::<Profile>()?;
    m.add_class::<Rows>()?;
    m.add_class::<Row>()?;
    Ok(())
}

