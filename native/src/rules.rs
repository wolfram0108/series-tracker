//! The engine of the rules constructor (Р-8): a profile is compiled once — the templates of its blocks made into patterns,
//! every condition of every rule put into one set, so that one pass over a line tells which of them match — then lines are
//! read by it. A rule that does not compile is left out of the profile and told with its reason (decision Б).
//!
//! The semantics are those of the patterns of Python the constructor was written for: `\d` and `\s` of Unicode, numbers
//! read by `int()`, `$` at the end or before a newline that ends the line, case ignored.

use regex::{Regex, RegexBuilder, RegexSet, RegexSetBuilder};
use serde_json::{Map, Value};
use std::collections::HashSet;

use crate::text::{decimal, digits_value, fold_name, py_int, py_space, py_strip, reason, Param};

const CONDITION_TYPES: &[&str] = &["contains", "not_contains"];
const EXTRACT_TYPES: &[&str] = &["extract_single", "extract_range", "extract_season", "extract_part"];
const ASSIGN_TYPES: &[&str] = &[
    "assign_voiceover", "assign_episode", "assign_season", "assign_part", "assign_quality", "assign_resolution",
    "assign_range", "assign_voice_folder", "assign_subtitle_folder", "assign_language", "assign_subtitle_type",
    "assign_video_tag",
];
const BLOCK_TYPES: &[&str] = &["text", "number", "range", "add", "subtract", "whitespace", "any_text", "start_of_line",
    "end_of_line", "or"];
const FOLDER_ASSIGN: &[&str] = &["assign_voice_folder", "assign_subtitle_folder"];
pub const SUBTITLE_TYPES: &[&str] = &["full", "signs"];
// The white space of a pattern: `\s` of Python also takes the separators of files, groups, records and units.
const SPACE: &str = r"[\s\x1c-\x1f]";

fn action_known(kind: &str) -> bool {
    kind == "exclude" || EXTRACT_TYPES.contains(&kind) || ASSIGN_TYPES.contains(&kind)
}

fn editor_key(kind: &str) -> Option<&'static str> {
    match kind {
        "assign_voice_folder" => Some("voice_folder"),
        "assign_subtitle_folder" => Some("subtitle_folder"),
        "assign_language" => Some("language"),
        "assign_subtitle_type" => Some("subtitle_type"),
        "assign_video_tag" => Some("video_tag"),
        _ => None,
    }
}

/// `str(value)` of Python for the values a rule holds.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match n.as_f64() {
            Some(f) if !n.is_i64() && !n.is_u64() => {
                if f.fract() == 0.0 && f.abs() < 1e16 { format!("{f:.1}") } else { f.to_string() }
            }
            _ => n.to_string(),
        },
        other => other.to_string(),
    }
}

/// The truth of a value as `if value:` reads it.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Null => false,
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(value)`: None where Python raises.
fn int_of(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => py_int(s),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    }
}

fn or_empty(value: Option<&Value>) -> String {
    value.filter(|v| truthy(v)).map(py_str).unwrap_or_default()
}

/// (name, language, type) of the value of a folder action: `{"name", "language", "type"}` as JSON, or the name alone.
pub fn folder_value(raw: Option<&Value>) -> (String, String, String) {
    let text = or_empty(raw);
    let text = py_strip(&text);
    if text.starts_with('{') {
        if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(text) {
            let get = |k: &str| py_strip(&or_empty(map.get(k))).to_string();
            return (get("name"), get("language"), get("type"));
        }
    }
    (text.to_string(), String::new(), String::new())
}

// --- the blocks of a template -------------------------------------------------------------------------------------

type Err = String;

fn kind_of(block: &Value) -> Value {
    block.get("type").cloned().unwrap_or(Value::Null)
}

fn is_kind(block: &Value, kind: &str) -> bool {
    block.get("type").and_then(Value::as_str) == Some(kind)
}

fn parse_blocks(raw: Option<&Value>) -> Result<Vec<Value>, Err> {
    let text = match raw {
        Some(Value::String(s)) => s.as_str(),
        None | Some(Value::Null) => "",
        Some(other) => return Err(format!("the JSON object must be str, bytes or bytearray, not {}", type_name(other))),
    };
    let parsed: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    match parsed {
        Value::Array(blocks) => Ok(blocks),
        _ => Err(reason("template_not_list", &[])),
    }
}

fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// The texts joined by «или», by the index of the first text of each group.
fn or_groups(blocks: &[Value]) -> Result<Vec<(usize, Vec<usize>)>, Err> {
    for (index, block) in blocks.iter().enumerate() {
        if !is_kind(block, "or") {
            continue;
        }
        let texts = index > 0 && index + 1 < blocks.len() && is_kind(&blocks[index - 1], "text")
            && is_kind(&blocks[index + 1], "text");
        if !texts {
            return Err(reason("or_place", &[]));
        }
    }
    let mut groups = Vec::new();
    let mut index = 0;
    while index < blocks.len() {
        if is_kind(&blocks[index], "text") && index + 1 < blocks.len() && is_kind(&blocks[index + 1], "or") {
            let mut members = vec![index];
            while members.last().unwrap() + 2 < blocks.len() && is_kind(&blocks[members.last().unwrap() + 1], "or") {
                members.push(members.last().unwrap() + 2);
            }
            if members.iter().any(|m| !truthy(blocks[*m].get("value").unwrap_or(&Value::Null))) {
                return Err(reason("or_text_empty", &[]));
            }
            index = members.last().unwrap() + 1;
            groups.push((members[0], members));
        } else {
            index += 1;
        }
    }
    Ok(groups)
}

fn text_value(block: &Value) -> String {
    match block.get("value") {
        Some(Value::String(s)) => s.clone(),
        None => String::new(),
        Some(other) => py_str(other),
    }
}

fn regex_of(blocks: &[Value], capture: bool, allow_range: bool) -> Result<String, Err> {
    let groups = or_groups(blocks)?;
    let joined: HashSet<usize> = groups.iter().flat_map(|(_, m)| m[1..].iter().copied()).collect();
    let mut out = String::new();
    for (index, block) in blocks.iter().enumerate() {
        let kind = kind_of(block);
        let name = kind.as_str().unwrap_or("");
        if !BLOCK_TYPES.contains(&name) {
            return Err(reason("block_unknown", &[("type", Param::Text(&py_str(&kind)))]));
        }
        if let Some((_, members)) = groups.iter().find(|(first, _)| *first == index) {
            let texts: Vec<String> = members.iter().map(|m| regex::escape(&text_value(&blocks[*m]))).collect();
            out.push_str("(?:");
            out.push_str(&texts.join("|"));
            out.push(')');
            continue;
        }
        if name == "or" || joined.contains(&index) {
            continue;
        }
        match name {
            "add" | "subtract" => {}
            "range" => {
                if !allow_range {
                    return Err(reason("range_in_action", &[]));
                }
            }
            "text" => out.push_str(&regex::escape(&text_value(block))),
            "number" => out.push_str(if capture { r"(\d+)" } else { r"\d+" }),
            "whitespace" => {
                out.push_str(SPACE);
                out.push('+');
            }
            "any_text" => out.push_str(".*?"),
            "start_of_line" => out.push_str(r"\A"),
            "end_of_line" => out.push_str(r"(?:\n?\z)"),
            _ => unreachable!(),
        }
    }
    if out.is_empty() {
        return Err(reason("template_empty", &[]));
    }
    Ok(out)
}

/// The index of the block that hangs on the only number of a template (a range, a modifier).
fn suffix_index(blocks: &[Value], kinds: &[&str], block: &str, place: &str) -> Result<Option<usize>, Err> {
    let found: Vec<usize> = blocks.iter().enumerate()
        .filter(|(_, b)| b.get("type").and_then(Value::as_str).map_or(false, |k| kinds.contains(&k))).map(|(i, _)| i).collect();
    let Some(&index) = found.first() else { return Ok(None) };
    if found.len() > 1 {
        return Err(reason("block_repeated", &[("place", Param::Text(place)), ("block", Param::Text(block))]));
    }
    if index == 0 {
        return Err(reason("block_first", &[("block", Param::Text(block))]));
    }
    if !is_kind(&blocks[index - 1], "number") {
        let after = py_str(&kind_of(&blocks[index - 1]));
        return Err(reason("block_after_number", &[("block", Param::Text(block)), ("after", Param::Text(&after))]));
    }
    if blocks[index + 1..].iter().any(|b| is_kind(b, "number")) {
        return Err(reason("block_after_last_number", &[("block", Param::Text(block)), ("place", Param::Text(place))]));
    }
    if blocks.iter().filter(|b| is_kind(b, "number")).count() != 1 {
        return Err(reason("block_one_number", &[("block", Param::Text(block))]));
    }
    Ok(Some(index))
}

fn compiled(pattern: &str) -> Result<Regex, Err> {
    RegexBuilder::new(pattern).case_insensitive(true).build().map_err(|e| e.to_string())
}

/// `^(\d+)\s*[-–—]\s*(\d+)$` of a stripped text.
fn dash_range(text: &str) -> Option<(i64, i64)> {
    let digits = |s: &str| -> usize { s.char_indices().find(|(_, c)| decimal(*c).is_none()).map_or(s.len(), |(i, _)| i) };
    let first = digits(text);
    if first == 0 {
        return None;
    }
    let rest = text[first..].trim_start_matches(py_space);
    let mut chars = rest.chars();
    let dash = chars.next()?;
    if !matches!(dash, '-' | '–' | '—') {
        return None;
    }
    let rest = chars.as_str().trim_start_matches(py_space);
    let second = digits(rest);
    if second == 0 || (second != rest.len() && &rest[second..] != "\n") {
        return None;
    }
    Some((digits_value(&text[..first])?, digits_value(&rest[..second])?))
}

pub fn range_spec(raw: &str) -> Result<Vec<(i64, i64)>, Err> {
    if py_strip(raw).is_empty() {
        return Err(reason("range_empty", &[]));
    }
    let parts: Vec<&str> = raw.split(',').map(py_strip).filter(|p| !p.is_empty()).collect();
    if parts.is_empty() {
        return Err(reason("range_empty", &[]));
    }
    let mut intervals = Vec::new();
    for part in parts {
        if let Some((a, b)) = dash_range(part) {
            // The user types this spec, so bounds in any order mean one interval.
            intervals.push((a.min(b), a.max(b)));
        } else if !part.is_empty() && part.chars().all(|c| decimal(c).is_some()) {
            let value = digits_value(part).ok_or_else(|| reason("range_item", &[("part", Param::Text(part))]))?;
            intervals.push((value, value));
        } else {
            return Err(reason("range_item", &[("part", Param::Text(part))]));
        }
    }
    intervals.sort();
    let mut merged: Vec<(i64, i64)> = Vec::new();
    for (start, end) in intervals {
        match merged.last_mut() {
            Some(last) if start <= last.1 + 1 => last.1 = last.1.max(end),
            _ => merged.push((start, end)),
        }
    }
    Ok(merged)
}

/// The range a rule gives to a row: one interval «A-B» with A below B.
fn assigned_range(raw: &str) -> Result<(i64, i64), Err> {
    let (start, end) = dash_range(py_strip(raw)).ok_or_else(|| reason("range_shape", &[("raw", Param::Text(raw))]))?;
    if start >= end {
        return Err(reason("range_order", &[("raw", Param::Text(raw))]));
    }
    Ok((start, end))
}

// --- the compiled profile -----------------------------------------------------------------------------------------

struct Condition {
    regex: Regex,
    // Its place in the set of every condition of the profile.
    at: usize,
    negate: bool,
    or_after: bool,
    range: Option<Vec<(i64, i64)>>,
}

struct Action {
    kind: String,
    raw: Value,
    regex: Option<Regex>,
    modifiers: Vec<Option<i64>>,
}

struct Rule {
    name: Value,
    // The start of the JSON of an event of the rule, made once: `{"rule":…,"order":…,"number":…,"action":"`.
    head: String,
    number: usize,
    continue_after: bool,
    conditions: Vec<Condition>,
    actions: Vec<Action>,
}

pub struct Profile {
    rules: Vec<Rule>,
    set: RegexSet,
    /// [{rule_id, name, error}] of the rules left out.
    pub invalid: Vec<Value>,
}

fn compile_rule(rule: &Value) -> Result<Rule, Err> {
    let mut conditions = Vec::new();
    for cond in rule.get("conditions").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]) {
        let ctype = cond.get("condition_type").cloned().unwrap_or(Value::Null);
        if !ctype.as_str().map_or(false, |t| CONDITION_TYPES.contains(&t)) {
            return Err(reason("condition_type_unknown", &[("type", Param::Text(&py_str(&ctype)))]));
        }
        let made = (|| -> Result<(String, Option<Vec<(i64, i64)>>), Err> {
            let blocks = parse_blocks(cond.get("pattern"))?;
            if blocks.iter().any(|b| is_kind(b, "add") || is_kind(b, "subtract")) {
                return Err(reason("modifier_in_condition", &[]));
            }
            match suffix_index(&blocks, &["range"], "range", "condition")? {
                Some(index) => {
                    let spec = blocks[index].get("value").filter(|v| truthy(v)).map(py_str).unwrap_or_default();
                    let range = range_spec(&spec)?;
                    Ok((regex_of(&blocks, true, true)?, Some(range)))
                }
                None => Ok((regex_of(&blocks, false, true)?, None)),
            }
        })();
        let (pattern, range) = made.map_err(|detail| reason("condition_broken", &[("detail", Param::Text(&detail))]))?;
        conditions.push(Condition {
            regex: compiled(&pattern)?, at: 0, negate: ctype.as_str() != Some("contains"),
            or_after: cond.get("logical_operator").and_then(Value::as_str) == Some("OR"), range,
        });
    }
    let actions_text = rule.get("action_pattern").filter(|v| truthy(v)).map(py_str).unwrap_or_else(|| "[]".into());
    let raw_actions: Value = serde_json::from_str(&actions_text).map_err(|e| e.to_string())?;
    let mut actions = Vec::new();
    for action in raw_actions.as_array().map(Vec::as_slice).unwrap_or(&[]) {
        let kind = action.get("action_type").cloned().unwrap_or(Value::Null);
        let raw = action.get("action_pattern").cloned().unwrap_or_else(|| Value::String("[]".into()));
        let name = kind.as_str().unwrap_or("");
        if !action_known(name) {
            return Err(reason("action_unknown", &[("type", Param::Text(&py_str(&kind)))]));
        }
        if name == "exclude" {
            actions.push(Action { kind: name.into(), raw: Value::Null, regex: None, modifiers: vec![] });
        } else if ASSIGN_TYPES.contains(&name) {
            if name == "assign_range" {
                // A wrong range is refused on saving, not found in a title.
                assigned_range(&or_empty(Some(&raw)))?;
            }
            actions.push(Action { kind: name.into(), raw, regex: None, modifiers: vec![] });
        } else {
            let blocks = parse_blocks(Some(&raw))?;
            suffix_index(&blocks, &["add", "subtract"], "modifier", "action")?;
            let pattern = regex_of(&blocks, true, false)?;
            let mut modifiers = Vec::new();
            for (i, block) in blocks.iter().enumerate() {
                if !is_kind(block, "number") {
                    continue;
                }
                let mut modifier = None;
                if let Some(next) = blocks.get(i + 1) {
                    let add = is_kind(next, "add");
                    if add || is_kind(next, "subtract") {
                        let given = next.get("value").cloned().unwrap_or(Value::from(0));
                        let value = int_of(&given).ok_or_else(|| reason("modifier_not_number", &[
                            ("sign", Param::Text(if add { "+" } else { "−" })),
                            ("value", Param::Text(&py_str(next.get("value").unwrap_or(&Value::Null))))]))?;
                        modifier = Some(if add { value } else { -value });
                    }
                }
                modifiers.push(modifier);
            }
            actions.push(Action { kind: name.into(), raw: Value::Null, regex: Some(compiled(&pattern)?), modifiers });
        }
    }
    Ok(Rule {
        name: rule.get("name").cloned().unwrap_or(Value::Null), head: String::new(), number: 0,
        continue_after: rule.get("continue_after_match").map_or(false, truthy), conditions, actions,
    })
}

/// The rows of a rule «Таблица» as the ordinary rules they are, in their order, at the place of the table.
fn table_rules(rule: &Value) -> Result<Vec<Value>, Err> {
    let Some(table) = rule.get("rule_table").and_then(Value::as_object) else {
        return Err(reason("table_broken", &[]));
    };
    let column = table.get("action_type").cloned().unwrap_or(Value::Null);
    if !column.as_str().map_or(false, action_known) {
        return Err(reason("action_unknown", &[("type", Param::Text(&py_str(&column)))]));
    }
    let rows = match table.get("rows") {
        Some(Value::Array(rows)) if !rows.is_empty() => rows,
        _ => return Err(reason("table_empty", &[])),
    };
    let language = py_strip(&or_empty(table.get("language"))).to_string();
    let rule_name = py_str(rule.get("name").unwrap_or(&Value::Null));
    let mut expanded: Vec<Map<String, Value>> = Vec::new();
    for (number, row) in rows.iter().enumerate().map(|(i, r)| (i + 1, r)) {
        let blocks = row.get("if").filter(|b| truthy(b));
        let Some(blocks) = blocks.filter(|_| row.is_object()) else {
            return Err(reason("table_row_if_empty", &[("number", Param::Int(number as i64))]));
        };
        let then = row.get("then").cloned().unwrap_or(Value::Null);
        let row_type = row.get("action_type").filter(|v| truthy(v)).cloned().unwrap_or_else(|| column.clone());
        let kind = row_type.as_str().filter(|k| action_known(k))
            .ok_or_else(|| reason("action_unknown", &[("type", Param::Text(&py_str(&row_type)))]))?;
        let pattern = if kind == "exclude" {
            "[]".to_string()
        } else if ASSIGN_TYPES.contains(&kind) {
            let mut pattern = py_strip(&if then.is_null() { String::new() } else { py_str(&then) }).to_string();
            if pattern.is_empty() {
                return Err(reason("table_row_then_empty", &[("number", Param::Int(number as i64))]));
            }
            if !language.is_empty() && FOLDER_ASSIGN.contains(&kind) {
                let (name, own, folder) = folder_value(Some(&Value::String(pattern.clone())));
                if !name.is_empty() && own.is_empty() {
                    let mut value = Map::new();
                    value.insert("name".into(), Value::String(name));
                    value.insert("language".into(), Value::String(language.clone()));
                    if !folder.is_empty() {
                        value.insert("type".into(), Value::String(folder));
                    }
                    pattern = Value::Object(value).to_string();
                }
            }
            pattern
        } else {
            if !truthy(&then) {
                return Err(reason("table_row_template_empty", &[("number", Param::Int(number as i64))]));
            }
            then.to_string()
        };
        let mut action = Map::new();
        action.insert("action_type".into(), Value::String(kind.into()));
        action.insert("action_pattern".into(), Value::String(pattern));
        if row.get("if_joined").map_or(false, truthy) && !expanded.is_empty() {
            let last = expanded.last_mut().unwrap();
            let mut actions: Vec<Value> = serde_json::from_str(last["action_pattern"].as_str().unwrap()).unwrap();
            actions.push(Value::Object(action));
            last.insert("action_pattern".into(), Value::String(Value::Array(actions).to_string()));
            let first = last["first"].as_i64().unwrap();
            last.insert("name".into(), Value::String(format!("{rule_name} · {first}–{number}")));
            continue;
        }
        let mut made = Map::new();
        made.insert("id".into(), rule.get("id").cloned().unwrap_or(Value::Null));
        made.insert("name".into(), Value::String(format!("{rule_name} · {number}")));
        made.insert("first".into(), Value::from(number as i64));
        made.insert("continue_after_match".into(), rule.get("continue_after_match").cloned().unwrap_or(Value::Null));
        made.insert("action_pattern".into(), Value::String(Value::Array(vec![Value::Object(action)]).to_string()));
        let mut condition = Map::new();
        condition.insert("condition_type".into(), Value::String("contains".into()));
        condition.insert("logical_operator".into(), Value::String("AND".into()));
        condition.insert("pattern".into(), Value::String(blocks.to_string()));
        made.insert("conditions".into(), Value::Array(vec![Value::Object(condition)]));
        expanded.push(made);
    }
    Ok(expanded.into_iter().map(Value::Object).collect())
}

fn compile_any(rule: &Value) -> Result<Vec<Rule>, Err> {
    if !rule.get("rule_table").map_or(false, truthy) {
        return Ok(vec![compile_rule(rule)?]);
    }
    let mut out = Vec::new();
    for (number, row_rule) in table_rules(rule)?.iter().enumerate().map(|(i, r)| (i + 1, r)) {
        out.push(compile_rule(row_rule).map_err(|detail| reason("table_row", &[
            ("number", Param::Int(number as i64)), ("detail", Param::Text(&detail))]))?);
    }
    Ok(out)
}

impl Profile {
    /// The rules in the form of the tables of the constructor (parser_rules with their conditions).
    pub fn compile(rules: &[Value]) -> Profile {
        let mut out = Vec::new();
        let mut invalid = Vec::new();
        for (number, rule) in rules.iter().enumerate().map(|(i, r)| (i + 1, r)) {
            match compile_any(rule) {
                Ok(made) => out.extend(made.into_iter().map(|mut r| {
                    r.number = number;
                    r
                })),
                Err(error) => {
                    let mut bad = Map::new();
                    bad.insert("rule_id".into(), rule.get("id").cloned().unwrap_or(Value::Null));
                    bad.insert("name".into(), rule.get("name").cloned().unwrap_or(Value::Null));
                    bad.insert("error".into(), Value::String(error));
                    invalid.push(Value::Object(bad));
                }
            }
        }
        for (order, rule) in out.iter_mut().enumerate() {
            rule.head = format!("{{\"rule\":{},\"order\":{order},\"number\":{},\"action\":\"", rule.name, rule.number);
        }
        let mut patterns = Vec::new();
        for rule in &mut out {
            for cond in &mut rule.conditions {
                cond.at = patterns.len();
                patterns.push(cond.regex.as_str().to_string());
            }
        }
        let set = RegexSetBuilder::new(&patterns).case_insensitive(true).build().expect("patterns already compiled");
        Profile { rules: out, set, invalid }
    }
}

/// The reason a rule does not compile, as saving it is refused; None for a rule that compiles. Its list of actions that is
/// no JSON is `rule_json`.
pub fn invalid_reason(rule: &Value) -> Option<String> {
    let error = compile_any(rule).err()?;
    let actions = rule.get("action_pattern").filter(|v| truthy(v)).map(py_str).unwrap_or_else(|| "[]".into());
    match serde_json::from_str::<Value>(&actions) {
        Err(json) if json.to_string() == error => Some(reason("rule_json", &[("detail", Param::Text(&error))])),
        _ => Some(error),
    }
}

/// The words of the constructor the engine understands: what a panel may offer.
pub fn vocabulary() -> Vec<(&'static str, Vec<&'static str>)> {
    let editor = ["assign_voice_folder", "assign_subtitle_folder", "assign_language", "assign_subtitle_type",
                  "assign_video_tag"];
    let mut actions = vec!["exclude"];
    actions.extend(EXTRACT_TYPES);
    actions.extend(ASSIGN_TYPES);
    vec![
        ("condition_types", CONDITION_TYPES.to_vec()), ("extract_types", EXTRACT_TYPES.to_vec()),
        ("assign_types", ASSIGN_TYPES.to_vec()), ("action_types", actions), ("block_types", BLOCK_TYPES.to_vec()),
        ("editor_actions", editor.to_vec()),
        ("editor_keys", editor.iter().map(|a| editor_key(a).unwrap()).collect()),
    ]
}

// --- a line read by a profile -------------------------------------------------------------------------------------

struct Look {
    quality: Regex,
    ahead: Regex,
    behind: Regex,
}

fn look() -> &'static Look {
    static LOOK: std::sync::OnceLock<Look> = std::sync::OnceLock::new();
    LOOK.get_or_init(|| Look {
        quality: RegexBuilder::new(r"\A[pkiрк](?:\W|\n?\z)").case_insensitive(true).build().unwrap(),
        ahead: RegexBuilder::new(&format!(r"\A{SPACE}*(?:[-–—]|по|to){SPACE}*(\d+)")).case_insensitive(true).build().unwrap(),
        behind: RegexBuilder::new(&format!(r"(\d+){SPACE}*(?:[-–—]|по|to){SPACE}*(?:\n?\z)")).case_insensitive(true).build()
            .unwrap(),
    })
}

/// A resolution or quality marker right at the start of the text («720p», «4k»).
fn quality_suffix(tail: &str) -> bool {
    look().quality.is_match(tail)
}

/// `^\s*(?:[-–—]|по|to)\s*(\d+)(?![pPkKiIрРкК\d])`: the end of its number and the number.
fn ahead(tail: &str) -> Option<(usize, i64)> {
    let caps = look().ahead.captures(tail)?;
    let group = caps.get(1)?;
    // The digits are as many as there are, so the look ahead reads only the next character.
    if let Some(next) = tail[group.end()..].chars().next() {
        let lower = next.to_lowercase().next().unwrap_or(next);
        if matches!(lower, 'p' | 'k' | 'i' | 'р' | 'к') || decimal(next).is_some() {
            return None;
        }
    }
    Some((group.end(), digits_value(group.as_str())?))
}

fn match_condition(title: &str, cond: &Condition, found: &regex::SetMatches) -> bool {
    if !found.matched(cond.at) {
        return false;
    }
    let Some(intervals) = &cond.range else { return true };
    for caps in cond.regex.captures_iter(title) {
        let Some(number) = caps.get(1) else { continue };
        if quality_suffix(&title[number.end()..]) {
            continue;
        }
        let Some(value) = digits_value(number.as_str()) else { continue };
        let (mut start, mut end) = (value, value);
        match ahead(&title[number.end()..]).filter(|(at, _)| !quality_suffix(&title[number.end() + at..])) {
            Some((_, after)) => {
                start = value.min(after);
                end = value.max(after);
            }
            None => {
                if let Some(before) = look().behind.captures(&title[..number.start()]).and_then(|c| c.get(1))
                    .and_then(|g| digits_value(g.as_str())) {
                    start = value.min(before);
                    end = value.max(before);
                }
            }
        }
        if intervals.iter().any(|(rs, re)| start.max(*rs) <= end.min(*re)) {
            return true;
        }
    }
    false
}

fn conditions_match(title: &str, conditions: &[Condition], found: &regex::SetMatches) -> bool {
    if conditions.is_empty() {
        return true;
    }
    let mut any = false;
    let mut current = true;
    for cond in conditions {
        let hit = match_condition(title, cond, found);
        current &= hit != cond.negate;
        if cond.or_after {
            any |= current;
            current = true;
        }
    }
    any || current
}

/// What the rules extracted, in the order a dict of Python keeps it.
#[derive(Default, Clone, Debug, PartialEq)]
pub struct Extracted(pub Vec<(std::borrow::Cow<'static, str>, Value)>);

impl Extracted {
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.0.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    fn set(&mut self, key: &'static str, value: Value) {
        match self.0.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = value,
            None => self.0.push((key.into(), value)),
        }
    }

    /// Its JSON, in its order.
    pub fn write_json(&self, out: &mut String) {
        out.push('{');
        for (i, (k, v)) in self.0.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push_str(&serde_json::to_string(k.as_ref()).unwrap());
            out.push(':');
            match v {
                Value::Number(n) => out.push_str(&n.to_string()),
                other => out.push_str(&other.to_string()),
            }
        }
        out.push('}');
    }

    pub fn pop(&mut self, key: &str) -> Option<Value> {
        let at = self.0.iter().position(|(k, _)| k == key)?;
        Some(self.0.remove(at).1)
    }

}

impl serde::Serialize for Extracted {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (k, v) in &self.0 {
            map.serialize_entry(k, v)?;
        }
        map.end()
    }
}

impl<'de> serde::Deserialize<'de> for Extracted {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Entries;
        impl<'de> serde::de::Visitor<'de> for Entries {
            type Value = Extracted;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("an object")
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut map: A) -> Result<Extracted, A::Error> {
                let mut out = Vec::new();
                while let Some((k, v)) = map.next_entry::<String, Value>()? {
                    out.push((k.into(), v));
                }
                Ok(Extracted(out))
            }
        }
        deserializer.deserialize_map(Entries)
    }
}

fn other_form(key: &str) -> &'static [&'static str] {
    if key == "episode" { &["start", "end"] } else { &["episode"] }
}

fn run_actions(title: &str, rule: &Rule, got: &mut Extracted, errors: &mut Vec<String>) -> &'static str {
    let mut produced = false;
    // The numbers this rule set, as bits: the first variant that works wins within a rule.
    let mut set: u8 = 0;
    let bit = |key: &str| -> u8 {
        match key {
            "episode" => 1,
            "start" => 2,
            "end" => 4,
            "season" => 8,
            _ => 16,
        }
    };
    let name = &rule.name;
    let bad = |errors: &mut Vec<String>, code: &str, kind: &str, value: &str| {
        errors.push(reason_with_rule(code, name, kind, value));
    };
    let put = |got: &mut Extracted, set: &mut u8, key: &'static str, value: i64| -> bool {
        if *set & bit(key) != 0 {
            return false;
        }
        if matches!(key, "episode" | "start" | "end") {
            if other_form(key).iter().any(|o| *set & bit(o) != 0) {
                return false;
            }
            for other in other_form(key) {
                got.pop(other);
            }
        }
        got.set(key, Value::from(value));
        *set |= bit(key);
        true
    };
    for action in &rule.actions {
        let kind = action.kind.as_str();
        match kind {
            "exclude" => return "exclude",
            "assign_voiceover" => {
                got.set("voiceover", action.raw.clone());
                produced = true;
            }
            "assign_quality" => {
                got.set("quality", action.raw.clone());
                produced = true;
            }
            "assign_resolution" => {
                got.set("resolution", action.raw.clone());
                produced = true;
            }
            "assign_voice_folder" | "assign_subtitle_folder" => {
                let (folder, language, folder_kind) = folder_value(Some(&action.raw));
                if folder.is_empty() || (!folder_kind.is_empty()
                    && (kind != "assign_subtitle_folder" || !SUBTITLE_TYPES.contains(&folder_kind.as_str()))) {
                    bad(errors, "rule_value_bad", kind, &or_empty(Some(&action.raw)));
                    continue;
                }
                got.set(editor_key(kind).unwrap(), Value::String(folder));
                if !language.is_empty() {
                    got.set("language", Value::String(language));
                }
                if !folder_kind.is_empty() {
                    got.set("subtitle_type", Value::String(folder_kind));
                }
                produced = true;
            }
            "assign_language" | "assign_subtitle_type" | "assign_video_tag" => {
                let value = or_empty(Some(&action.raw));
                let value = py_strip(&value);
                if value.is_empty() || (kind == "assign_subtitle_type" && !SUBTITLE_TYPES.contains(&value)) {
                    bad(errors, "rule_value_bad", kind, value);
                    continue;
                }
                got.set(editor_key(kind).unwrap(), Value::String(value.to_string()));
                produced = true;
            }
            "assign_range" => {
                got.pop("episode");
                if let Ok((start, end)) = assigned_range(&or_empty(Some(&action.raw))) {
                    got.set("start", Value::from(start));
                    got.set("end", Value::from(end));
                }
                produced = true;
            }
            "assign_episode" | "assign_season" | "assign_part" => {
                let Some(value) = int_of(&action.raw) else {
                    bad(errors, "rule_number_bad", kind, &py_str(&action.raw));
                    continue;
                };
                match kind {
                    "assign_episode" => {
                        got.set("episode", Value::from(value));
                        got.pop("start");
                        got.pop("end");
                    }
                    "assign_season" => got.set("season", Value::from(value)),
                    _ => got.set("part", Value::from(value)),
                }
                produced = true;
            }
            _ => {
                let Some(regex) = &action.regex else { continue };
                let Some(caps) = regex.captures(title) else { continue };
                let numbers: Vec<i64> = action.modifiers.iter().enumerate().map(|(i, modifier)| {
                    let value = caps.get(i + 1).and_then(|g| digits_value(g.as_str())).unwrap_or(0);
                    match modifier {
                        Some(m) if value + m >= 0 => value + m,
                        _ => value,
                    }
                }).collect();
                if kind == "extract_range" {
                    if numbers.len() > 1 && numbers[1] < numbers[0] {
                        // Swapping the bounds would hide a wrong template; the line stays without a range.
                        errors.push(reason("rule_range_reversed", &[
                            ("rule", Param::Json(name.clone())), ("start", Param::Int(numbers[0])),
                            ("end", Param::Int(numbers[1]))]));
                        continue;
                    }
                    if !numbers.is_empty() && put(got, &mut set, "start", numbers[0]) {
                        produced = true;
                    }
                    if numbers.len() > 1 {
                        put(got, &mut set, "end", numbers[1]);
                    }
                } else if !numbers.is_empty() {
                    let key = match kind {
                        "extract_single" => "episode",
                        "extract_season" => "season",
                        _ => "part",
                    };
                    if put(got, &mut set, key, numbers[0]) {
                        produced = true;
                    }
                }
            }
        }
    }
    if produced { "data" } else { "nothing" }
}

fn reason_with_rule(code: &str, rule: &Value, action: &str, value: &str) -> String {
    reason(code, &[("rule", Param::Json(rule.clone())), ("action", Param::Text(action)), ("value", Param::Text(value))])
}

/// What a profile made of a line, its events by the place of their rule: (rule, what it extracted that changed, stopped).
struct Lean {
    excluded: bool,
    extracted: Extracted,
    events: Vec<(usize, Option<Extracted>, bool)>,
    errors: Vec<String>,
}

impl Profile {
    fn run(&self, title: &str) -> Lean {
        let mut out = Lean { excluded: false, extracted: Extracted::default(), events: Vec::new(), errors: Vec::new() };
        let found = self.set.matches(title);
        for (order, rule) in self.rules.iter().enumerate() {
            if !conditions_match(title, &rule.conditions, &found) {
                continue;
            }
            let before = out.extracted.clone();
            match run_actions(title, rule, &mut out.extracted, &mut out.errors) {
                "exclude" => {
                    out.events.push((order, None, false));
                    out.excluded = true;
                    out.extracted = Extracted::default();
                    return out;
                }
                "data" => {
                    let changed = Extracted(out.extracted.0.iter().filter(|(k, v)| before.get(k) != Some(v)).cloned().collect());
                    // The rules after this one were not tried: said, so the test of a profile can show why.
                    let stopped = !rule.continue_after;
                    out.events.push((order, Some(changed), stopped));
                    if stopped {
                        break;
                    }
                }
                _ => {}
            }
        }
        out
    }

    fn events_json(&self, events: &[(usize, Option<Extracted>, bool)]) -> String {
        let mut out = String::from("[");
        for (i, (order, changed, stopped)) in events.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push_str(&self.rules[*order].head);
            match changed {
                None => out.push_str("exclude\""),
                Some(changed) => {
                    out.push_str("extract\",\"extracted\":");
                    changed.write_json(&mut out);
                    if *stopped {
                        out.push_str(",\"stopped\":true");
                    }
                }
            }
            out.push('}');
        }
        out.push(']');
        out
    }

    /// What the profile makes of one line: excluded, what it extracted, the rules that acted, the values it could not use
    /// — as JSON.
    pub fn process(&self, title: &str) -> String {
        let lean = self.run(title);
        let mut out = String::from("{\"excluded\":");
        out.push_str(if lean.excluded { "true" } else { "false" });
        out.push_str(",\"extracted\":");
        lean.extracted.write_json(&mut out);
        out.push_str(",\"events\":");
        out.push_str(&self.events_json(&lean.events));
        out.push_str(",\"errors\":");
        out.push_str(&serde_json::to_string(&lean.errors).unwrap());
        out.push('}');
        out
    }

    /// The answer for one line.
    pub fn answer(&self, title: &str, card: Option<&str>) -> Answer {
        let lean = self.run(title);
        let events = self.events_json(&lean.events);
        let errors = if lean.errors.is_empty() { "[]".to_string() } else { serde_json::to_string(&lean.errors).unwrap() };
        Answer::made(lean.excluded, lean.extracted, events, errors, card)
    }
}

/// What the rules made of a line as a caller gives it (a constructor that answers otherwise): its events as they are.
#[derive(Default, Clone, Debug, serde::Deserialize)]
#[serde(default)]
pub struct Outcome {
    pub excluded: bool,
    pub extracted: Extracted,
    pub events: Vec<Value>,
    pub errors: Vec<Value>,
}

// --- the answer of the constructor to the lines of a table -------------------------------------------------------

/// A season that neither the rules nor the card of the series gave is season 1.
const DEFAULT_SEASON: i64 = 1;

/// The season of a numbered line: the one the rules extracted, else the first number of the card's, else the default.
pub fn season_of(card: Option<&str>, extracted: Option<i64>) -> i64 {
    if let Some(found) = extracted {
        return found;
    }
    if let Some(card) = card.filter(|c| !c.is_empty()) {
        let start = card.char_indices().find(|(_, c)| decimal(*c).is_some());
        if let Some((at, _)) = start {
            let rest = &card[at..];
            let end = rest.char_indices().find(|(_, c)| decimal(*c).is_none()).map_or(rest.len(), |(i, _)| i);
            if let Some(value) = digits_value(&rest[..end]) {
                return value;
            }
        }
    }
    DEFAULT_SEASON
}

/// The answer for one line (RuleResult): an excluded line has no numbers.
#[derive(Default, Clone, Debug)]
pub struct Answer {
    pub excluded: bool,
    pub season: Option<i64>,
    pub episode: Option<i64>,
    pub start: Option<i64>,
    pub end: Option<i64>,
    pub part: Option<i64>,
    pub voiceover: Option<String>,
    // The JSON of the events and of the errors of the line.
    pub events: String,
    pub errors: String,
    pub extras: Extracted,
}

fn int_value(value: Option<Value>) -> Option<i64> {
    value.and_then(|v| int_of(&v))
}

impl Answer {
    pub fn of(outcome: Outcome, card: Option<&str>) -> Answer {
        let events = Value::from(outcome.events).to_string();
        let errors = Value::from(outcome.errors).to_string();
        Answer::made(outcome.excluded, outcome.extracted, events, errors, card)
    }

    fn made(excluded: bool, extracted: Extracted, events: String, errors: String, card: Option<&str>) -> Answer {
        if excluded {
            return Answer { excluded: true, events, errors, ..Default::default() };
        }
        let mut extracted = extracted;
        let season = season_of(card, int_value(extracted.pop("season")));
        let voiceover = match extracted.pop("voiceover") {
            None | Some(Value::Null) => None,
            Some(Value::String(s)) => Some(s),
            Some(other) => Some(py_str(&other)),
        };
        let (episode, start, end, part) = (int_value(extracted.pop("episode")), int_value(extracted.pop("start")),
                                           int_value(extracted.pop("end")), int_value(extracted.pop("part")));
        Answer { excluded: false, season: Some(season), episode, start, end, part, voiceover, events, errors,
                 extras: extracted }
    }

    /// The episodes a line names: a single episode wins over a range, a range without an end ends where it starts.
    pub fn span(&self) -> Option<(i64, i64)> {
        if self.excluded {
            return None;
        }
        if let Some(e) = self.episode {
            return Some((e, e));
        }
        self.start.map(|s| (s, self.end.unwrap_or(s)))
    }
}

/// A line the constructor is asked about: its key, its text, the player and the voice-over of the row it was made from.
pub struct Line<'a> {
    pub key: &'a str,
    pub text: &'a str,
    pub player: &'a str,
    pub voice: &'a str,
}

/// A single episode without a part is part 1 where its slot (player, voice-over, season, episode) has single episodes
/// with parts; a compilation neither makes a slot nor takes part 1.
pub fn settle_parts(lines: &[Line], answers: &mut [Answer]) {
    // The names of a table are few: each is folded once.
    let folded = std::cell::RefCell::new(std::collections::HashMap::<String, String>::new());
    let slot = |line: &Line, answer: &Answer, span: (i64, i64)| {
        let assigned = match &answer.voiceover {
            Some(v) if !py_strip(v).is_empty() => v.as_str(),
            _ => line.voice,
        };
        let key = folded.borrow_mut().entry(assigned.to_string()).or_insert_with(|| fold_name(assigned)).clone();
        (line.player.to_string(), key, answer.season, span.0)
    };
    let mut parted = HashSet::new();
    for (line, answer) in lines.iter().zip(answers.iter()) {
        if let Some(span) = answer.span() {
            if span.0 == span.1 && answer.part.is_some() {
                parted.insert(slot(line, answer, span));
            }
        }
    }
    if parted.is_empty() {
        return;
    }
    for (line, answer) in lines.iter().zip(answers.iter_mut()) {
        if let Some(span) = answer.span() {
            if span.0 == span.1 && answer.part.is_none() && parted.contains(&slot(line, answer, span)) {
                answer.part = Some(1);
            }
        }
    }
}
