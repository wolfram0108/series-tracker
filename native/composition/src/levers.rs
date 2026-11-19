//! The forms of the levers of the output (modules/composition/levers.py declares the levers, one list): what a value of
//! each shape is, what a target of each kind is, and whether a record of a lever is one the registry takes. The only
//! place these are told apart; the module asks it here, for what it writes and for what it reads.

use serde_json::{Map, Value};
use st_contracts::{integer, parse_compilation_key, parse_group_target, parse_variant_target};
use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};

/// What a lever's value is about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Target {
    None,
    Group,
    Variant,
}

/// The shape of the value of a lever.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shape {
    Flag,
    VariantOrder,
    VariantSets,
    Compilation,
}

impl Target {
    pub fn of(name: &str) -> Option<Self> {
        Some(match name { "none" => Self::None, "group" => Self::Group, "variant" => Self::Variant, _ => return None })
    }
}

impl Shape {
    pub fn of(name: &str) -> Option<Self> {
        Some(match name {
            "flag" => Self::Flag, "variant_order" => Self::VariantOrder, "variant_sets" => Self::VariantSets,
            "compilation" => Self::Compilation, _ => return None,
        })
    }
}

/// A text with something in it but blanks: what Python's `str.strip` leaves of it (its blanks are those of `isspace`).
fn is_name(value: &Value) -> bool {
    value.as_str().is_some_and(|text| text.chars().any(|each| !(each.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&each))))
}

fn is_id(value: &Value) -> bool {
    integer(value).is_some()
}

/// A variant of an order or of a group of equals: exactly these fields, each of its kind. Without a target the variant
/// names its voice-over group too.
fn is_item(value: &Value, with_group: bool) -> bool {
    let Some(fields) = value.as_object() else { return false };
    let wanted: &[&str] = if with_group { &["group", "source_id", "player"] } else { &["source_id", "player"] };
    fields.len() == wanted.len() && wanted.iter().all(|name| fields.contains_key(*name))
        && (!with_group || is_name(&fields["group"])) && is_id(&fields["source_id"]) && is_name(&fields["player"])
}

fn key_of(item: &Map<String, Value>) -> Vec<(String, String)> {
    let mut found: Vec<(String, String)> = item.iter().map(|(name, value)| (name.clone(), value.to_string())).collect();
    found.sort();
    found
}

fn is_order(value: &Value, target: Target) -> bool {
    if target == Target::Variant {
        return false;
    }
    let Some(items) = value.as_array() else { return false };
    let mut seen = HashSet::new();
    items.iter().all(|each| is_item(each, target == Target::None) && seen.insert(key_of(each.as_object().unwrap())))
}

fn is_sets(value: &Value, target: Target) -> bool {
    if target != Target::None {
        return false;
    }
    let Some(groups) = value.as_array() else { return false };
    let mut seen = HashSet::new();
    groups.iter().all(|group| group.as_array().is_some_and(|items| items.len() >= 2 && items.iter().all(
        |item| is_item(item, true) && seen.insert(key_of(item.as_object().unwrap())))))
}

/// Whether a value is one of the values of a lever of this shape and target.
pub fn accepts(shape: Shape, target: Target, value: &Value) -> bool {
    match shape {
        Shape::Flag => value.is_boolean(),
        Shape::VariantOrder => is_order(value, target),
        Shape::VariantSets => is_sets(value, target),
        Shape::Compilation => value.as_str().is_some_and(|key| key.is_empty() || parse_compilation_key(key).is_some()),
    }
}

/// Whether `target` is the form a lever of this kind points at; a lever without a target takes none.
pub fn accepts_target(kind: Target, target: Option<&str>) -> bool {
    match (kind, target) {
        (Target::None, found) => found.is_none(),
        (_, None) => false,
        (Target::Variant, Some(found)) => parse_variant_target(found).is_some(),
        (Target::Group, Some(found)) => parse_group_target(found).is_some(),
    }
}

/// A lever as the core needs it: its name, its target, its shape, its default and the levels it may be set on (none:
/// all). The registry keeps the levers in the order of registration: the order every lever of a level is told in.
#[derive(Debug, Clone)]
pub struct Form {
    pub name: String,
    pub target: Target,
    pub shape: Shape,
    pub default: Value,
    pub levels: Option<Vec<String>>,
}

fn registry() -> &'static Mutex<Vec<Form>> {
    static FORMS: OnceLock<Mutex<Vec<Form>>> = OnceLock::new();
    FORMS.get_or_init(|| Mutex::new(Vec::new()))
}

pub fn register(form: Form) {
    let mut forms = registry().lock().unwrap();
    match forms.iter_mut().find(|each| each.name == form.name) {
        Some(held) => *held = form,
        None => forms.push(form),
    }
}

pub fn unregister(name: &str) {
    registry().lock().unwrap().retain(|each| each.name != name);
}

pub fn form_of(name: &str) -> Option<Form> {
    registry().lock().unwrap().iter().find(|each| each.name == name).cloned()
}

/// Every lever in the order of registration.
pub fn all() -> Vec<Form> {
    registry().lock().unwrap().clone()
}

/// A record of a lever: where it is set and what it says.
pub struct Record<'a> {
    pub lever: &'a str,
    pub target: Option<&'a str>,
    pub value: &'a Value,
    pub season: Option<i64>,
    pub episode: Option<i64>,
    pub compilation: Option<&'a str>,
}

impl Record<'_> {
    pub fn level(&self) -> &'static str {
        if self.episode.is_some() {
            "episode"
        } else if self.compilation.is_some() {
            "compilation"
        } else if self.season.is_some() {
            "season"
        } else {
            "series"
        }
    }
}

/// None when the lever, its target, its value and its level are in the registry's terms; else the code of the refusal
/// (`unknown_lever`, `bad_target`, `bad_value`, `bad_level`).
pub fn problem(record: &Record) -> Option<&'static str> {
    let Some(form) = form_of(record.lever) else { return Some("unknown_lever") };
    if !accepts_target(form.target, record.target) {
        return Some("bad_target");
    }
    if !accepts(form.shape, form.target, record.value) {
        return Some("bad_value");
    }
    if let Some(key) = record.compilation {
        match parse_compilation_key(key) {
            Some(found) if Some(found.3) == record.season => {}
            _ => return Some("bad_target"),
        }
    }
    match &form.levels {
        Some(levels) if !levels.iter().any(|each| each == record.level()) => Some("bad_level"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_value_is_of_its_shape_and_a_target_of_its_kind() {
        let item = json!({"group": "G", "source_id": 1, "player": "vk"});
        assert!(accepts(Shape::VariantOrder, Target::None, &json!([item.clone()])));
        assert!(!accepts(Shape::VariantOrder, Target::None, &json!([item.clone(), item.clone()])));
        assert!(!accepts(Shape::VariantOrder, Target::Group, &json!([item.clone()])));
        assert!(accepts(Shape::VariantOrder, Target::Group, &json!([{"source_id": 1, "player": "vk"}])));
        assert!(!accepts(Shape::VariantOrder, Target::None, &json!([{"group": " ", "source_id": 1, "player": "vk"}])));
        assert!(!accepts(Shape::VariantOrder, Target::None, &json!([{"group": "G", "source_id": true, "player": "vk"}])));
        assert!(accepts(Shape::VariantSets, Target::None, &json!([[item.clone(), {"group": "H", "source_id": 2, "player": "vk"}]])));
        assert!(!accepts(Shape::VariantSets, Target::None, &json!([[item.clone()]])));
        assert!(accepts(Shape::Flag, Target::Variant, &json!(false)) && !accepts(Shape::Flag, Target::Variant, &json!(0)));
        assert!(accepts(Shape::Compilation, Target::None, &json!("")));
        assert!(accepts_target(Target::None, None) && !accepts_target(Target::Group, None));
        assert!(accepts_target(Target::Group, Some("group:G")) && !accepts_target(Target::Variant, Some("group:G")));
    }
}
