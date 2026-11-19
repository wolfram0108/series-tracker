//! The output settings of one series (modules/composition/settings.py, the read side): the value of every lever for an
//! episode, a compilation, a season or the series. A record exists only where the user set a value of its own; the
//! value of an episode is its own, else that of the compilation it stands in, else its season's, else the series',
//! else the lever's default. The table `output_settings` is the module's own memory: its SQL lives here.

use crate::levers::{self, Record, Target};
use rusqlite::{Connection, OpenFlags};
use serde_json::Value;
use st_contracts::json_text;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

/// A record of a lever as the module wrote it.
#[derive(Debug, Clone)]
pub struct Setting {
    pub lever: String,
    pub target: Option<String>,
    pub value: Value,
    pub season: Option<i64>,
    pub episode: Option<i64>,
    pub compilation: Option<String>,
}

/// The records of the levers of a series.
pub fn settings_of(conn: &Connection, series_id: i64) -> Result<Vec<Setting>, String> {
    let mut query = conn.prepare_cached(
        "SELECT lever, target, value, season, episode, compilation FROM output_settings WHERE series_id = ?1")
        .map_err(|exc| exc.to_string())?;
    let rows = query.query_map([series_id], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?, row.get::<_, String>(2)?, row.get(3)?,
            row.get(4)?, row.get::<_, Option<String>>(5)?))
    }).map_err(|exc| exc.to_string())?;
    rows.map(|each| {
        let (lever, target, value, season, episode, compilation) = each.map_err(|exc| exc.to_string())?;
        let value = serde_json::from_str(&value).map_err(|exc| format!("the value of {lever} is no JSON: {exc}"))?;
        Ok(Setting { lever, target, value, season, episode, compilation })
    }).collect()
}

pub fn open(path: &str) -> Result<Connection, String> {
    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|exc| exc.to_string())
}

/// A level: (season, episode, compilation key).
type Scope = (Option<i64>, Option<i64>, Option<String>);

/// lever -> target -> level -> value, over the records the registry still takes. A value is held as the JSON it is
/// written in, made once: every episode that inherits it shares it (an order of the series stands in all its episodes).
pub struct Index {
    by_lever: HashMap<String, BTreeMap<Option<String>, HashMap<Scope, Arc<str>>>>,
    forms: Vec<(levers::Form, Arc<str>)>,
}

pub fn index(settings: &[Setting]) -> Index {
    let mut by_lever: HashMap<String, BTreeMap<Option<String>, HashMap<Scope, Arc<str>>>> = HashMap::new();
    for each in settings {
        let record = Record { lever: &each.lever, target: each.target.as_deref(), value: &each.value, season: each.season,
                              episode: each.episode, compilation: each.compilation.as_deref() };
        if levers::problem(&record).is_none() {
            by_lever.entry(each.lever.clone()).or_default().entry(each.target.clone()).or_default()
                .insert((each.season, each.episode, each.compilation.clone()), Arc::from(each.value.to_string()));
        }
    }
    let forms = levers::all().into_iter().map(|form| {
        let default = Arc::from(form.default.to_string());
        (form, default)
    }).collect();
    Index { by_lever, forms }
}

/// The value of a lever at a level (its JSON), and the level it comes from.
#[derive(Debug, Clone, PartialEq)]
pub struct Effective<'a> {
    pub lever: &'a str,
    pub target: Option<&'a str>,
    pub value: Arc<str>,
    pub level: &'static str,
}

impl Effective<'_> {
    /// The lever as the layout writes it: `{"lever", "target", "value", "level"}`.
    pub fn write(&self, out: &mut String) {
        out.push_str("{\"lever\":");
        out.push_str(&json_text(self.lever));
        out.push_str(",\"target\":");
        match self.target {
            Some(target) => out.push_str(&json_text(target)),
            None => out.push_str("null"),
        }
        out.push_str(",\"value\":");
        out.push_str(&self.value);
        out.push_str(",\"level\":\"");
        out.push_str(self.level);
        out.push_str("\"}");
    }
}

fn write_levers(levers: &[Effective], out: &mut String) {
    out.push('[');
    for (at, each) in levers.iter().enumerate() {
        if at > 0 {
            out.push(',');
        }
        each.write(out);
    }
    out.push(']');
}

/// Every lever's value for an episode (or, without `episode`, for a compilation or a season; without `season`, for the
/// series). A lever without a target gives one entry; a lever with targets one per target that has a record on the
/// chain of levels of the episode.
pub fn resolve<'a>(index: &'a Index, season: Option<i64>, episode: Option<i64>, compilation: Option<&str>) -> Vec<Effective<'a>> {
    let mut chain: Vec<(&'static str, Scope)> = Vec::with_capacity(4);
    if episode.is_some() {
        chain.push(("episode", (season, episode, None)));
    }
    if let Some(key) = compilation {
        chain.push(("compilation", (season, None, Some(key.to_string()))));
    }
    if season.is_some() {
        chain.push(("season", (season, None, None)));
    }
    chain.push(("series", (None, None, None)));
    let mut out = Vec::new();
    for (form, default) in &index.forms {
        let by_target = index.by_lever.get(&form.name);
        let found_for = |values: &'a HashMap<Scope, Arc<str>>| {
            chain.iter().find_map(|(level, scope)| values.get(scope).map(|value| (*level, value.clone())))
        };
        if form.target == Target::None {
            let found = by_target.and_then(|targets| targets.get(&None)).and_then(found_for);
            let (level, value) = found.unwrap_or(("default", default.clone()));
            out.push(Effective { lever: &form.name, target: None, value, level });
            continue;
        }
        for (target, values) in by_target.into_iter().flatten() {
            if let Some((level, value)) = found_for(values) {
                out.push(Effective { lever: &form.name, target: target.as_deref(), value, level });
            }
        }
    }
    out
}

/// The levers of the series, then of each season given, then of each compilation (season, key) given: what the headers
/// of the levels show (the JSON of the list `levels` of the layout).
pub fn levels(index: &Index, seasons: &[i64], compilations: &[(i64, String)]) -> String {
    let mut seasons = seasons.to_vec();
    seasons.sort();
    seasons.dedup();
    let mut compilations = compilations.to_vec();
    compilations.sort();
    compilations.dedup();
    let mut out = String::from("[{\"season\":null,\"compilation\":null,\"levers\":");
    write_levers(&resolve(index, None, None, None), &mut out);
    out.push('}');
    for season in seasons {
        out.push_str(&format!(",{{\"season\":{season},\"compilation\":null,\"levers\":"));
        write_levers(&resolve(index, Some(season), None, None), &mut out);
        out.push('}');
    }
    for (season, key) in compilations {
        out.push_str(&format!(",{{\"season\":{season},\"compilation\":{},\"levers\":", json_text(&key)));
        write_levers(&resolve(index, Some(season), None, Some(&key)), &mut out);
        out.push('}');
    }
    out.push(']');
    out
}

/// The levers of each episode with the compilation it stands in (`member`: its key and the JSON the layout tells it
/// in), in the order of season and episode (the JSON of the list `settings` of the layout).
pub fn episode_settings(index: &Index, episodes: &[(i64, i64, Option<String>, String)]) -> String {
    let mut episodes: Vec<&(i64, i64, Option<String>, String)> = episodes.iter().collect();
    episodes.sort_by(|one, two| (one.0, one.1).cmp(&(two.0, two.1)));
    episodes.dedup_by(|one, two| (one.0, one.1) == (two.0, two.1));
    let mut out = String::from("[");
    for (at, (season, episode, key, member)) in episodes.into_iter().enumerate() {
        if at > 0 {
            out.push(',');
        }
        out.push_str(&format!("{{\"season\":{season},\"episode\":{episode},\"levers\":"));
        write_levers(&resolve(index, Some(*season), Some(*episode), key.as_deref()), &mut out);
        out.push_str(",\"compilation\":");
        out.push_str(member);
        out.push('}');
    }
    out.push(']');
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::levers::{register, Form, Shape};
    use serde_json::json;

    fn setting(lever: &str, target: Option<&str>, value: Value, season: Option<i64>, episode: Option<i64>) -> Setting {
        Setting { lever: lever.into(), target: target.map(Into::into), value, season, episode, compilation: None }
    }

    #[test]
    fn an_episode_takes_its_own_value_else_its_seasons_else_the_series_else_the_default() {
        register(Form { name: "t.take".into(), target: Target::Group, shape: Shape::Flag, default: json!(true), levels: None });
        register(Form { name: "t.order".into(), target: Target::None, shape: Shape::VariantOrder, default: json!([]),
                        levels: None });
        let index = index(&[setting("t.take", Some("group:G"), json!(false), Some(1), None),
                            setting("t.take", Some("group:H"), json!(false), Some(2), Some(3)),
                            setting("t.take", Some("group:G"), json!(true), Some(1), Some(2))]);
        let of = |season, episode| resolve(&index, season, episode, None).into_iter()
            .filter(|each| each.lever.starts_with("t.")).map(|each| (each.lever.to_string(),
                each.target.map(str::to_string), each.value.to_string(), each.level)).collect::<Vec<_>>();
        assert_eq!(of(Some(1), Some(1)), vec![("t.take".into(), Some("group:G".into()), "false".into(), "season"),
                                               ("t.order".into(), None, "[]".into(), "default")]);
        assert_eq!(of(Some(1), Some(2))[0], ("t.take".into(), Some("group:G".into()), "true".into(), "episode"));
        assert_eq!(of(Some(2), Some(1)).len(), 1); // a target with no record on the chain of the episode is no entry
    }
}
