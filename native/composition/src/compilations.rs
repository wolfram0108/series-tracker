//! The compilations of a series and the one each episode stands in (modules/composition/compilations.py — the module
//! docstring there tells the rule), and the order of the voice-overs of every season (modules/composition/project.py
//! `season_orders`, `season_voices`).

use crate::settings::{resolve, Index, Setting};
use crate::Row;
use st_contracts::{compilation_key, json_text, names};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

/// The template of the label of a voice-over nobody named (the layout numbers them): the one place of it.
pub const UNKNOWN_VOICE_LABEL: &str = "Неизвестная озвучка {number}";

pub type Episode = (i64, i64);

/// A compilation: the best row of a key, and what the layout tells of it.
#[derive(Debug, Clone)]
pub struct Ref {
    pub key: String,
    pub source_id: i64,
    pub row_key: String,
    pub player: String,
    pub voice_group: String,
    pub height: Option<i64>,
    pub start: i64,
    pub end: i64,
    pub season: i64,
}

impl Ref {
    pub fn json(&self) -> String {
        format!("{{\"key\":{},\"source_id\":{},\"row_key\":{},\"player\":{},\"voice_group\":{},\"height\":{},\"start\":{},\"end\":{}}}",
                json_text(&self.key), self.source_id, json_text(&self.row_key), json_text(&self.player),
                json_text(&self.voice_group), self.height.map_or("null".to_string(), |each| each.to_string()), self.start,
                self.end)
    }
}

pub fn height(row: &Row) -> Option<i64> {
    row.heights.iter().copied().max()
}

pub fn is_compilation(row: &Row) -> bool {
    matches!((row.episode_start, row.episode_end), (Some(start), Some(end)) if end > start)
}

/// The row is a row of the output: the rules numbered it and a voice-over group took it (a row of subtitles needs none).
pub fn in_output(row: &Row) -> bool {
    !row.excluded && row.season.is_some() && row.episode_start.is_some()
        && (row.voice_group.is_some() || row.track == "subtitles")
}

/// The episodes a row covers: a compilation every episode of its range.
pub fn covered(row: &Row) -> Vec<Episode> {
    match (row.excluded, row.season, row.episode_start) {
        (false, Some(season), Some(start)) => {
            let end = row.episode_end.unwrap_or(start).max(start);
            (start..=end).map(|number| (season, number)).collect()
        }
        _ => Vec::new(),
    }
}

/// The (season, episode) pairs the layout covers.
pub fn layout_episodes(rows: &[&Row]) -> BTreeSet<Episode> {
    rows.iter().flat_map(|row| covered(row)).collect()
}

/// One compilation per key over the rows that play and are numbered, its video the highest row (the earlier on a tie),
/// in the order the keys first come.
pub fn refs(rows: &[&Row]) -> Vec<Ref> {
    let mut found: Vec<Ref> = Vec::new();
    let mut at: HashMap<String, usize> = HashMap::new();
    for row in rows {
        let (Some(season), Some(start), Some(end)) = (row.season, row.episode_start, row.episode_end) else { continue };
        if row.excluded || row.state != "playable" || !is_compilation(row) || row.voice_group.is_none() {
            continue;
        }
        let key = compilation_key(&row.voice_label, row.source_id, &row.player, season, start, end);
        let candidate = Ref { key: key.clone(), source_id: row.source_id, row_key: row.row_key.clone(),
                              player: row.player.clone(), voice_group: row.voice_label.clone(), height: height(row),
                              start, end, season };
        match at.get(&key) {
            None => {
                at.insert(key, found.len());
                found.push(candidate);
            }
            Some(&place) if candidate.height.unwrap_or(0) > found[place].height.unwrap_or(0) => found[place] = candidate,
            Some(_) => {}
        }
    }
    found
}

/// The compilation of every episode that stands in one, and the compilations of every season as a window shows them.
pub struct Outcome {
    pub refs: Vec<Ref>,
    pub members: HashMap<Episode, usize>,
    pub views: String,
    pub view_keys: Vec<(i64, String)>,
}

fn word_of(settings: &[Setting], slot: Episode) -> Option<String> {
    settings.iter().find(|each| each.lever == names::COMPILATION_MEMBER && each.season == Some(slot.0)
        && each.episode == Some(slot.1) && crate::levers::problem(&crate::levers::Record {
            lever: &each.lever, target: each.target.as_deref(), value: &each.value, season: each.season,
            episode: each.episode, compilation: each.compilation.as_deref() }).is_none())
        .and_then(|each| each.value.as_str().map(str::to_string))
}

pub fn resolve_compilations(rows: &[&Row], index: &Index, settings: &[Setting], source_order: &HashMap<i64, usize>)
                            -> Outcome {
    let refs = refs(rows);
    if refs.is_empty() {
        return Outcome { refs, members: HashMap::new(), views: "[]".into(), view_keys: Vec::new() };
    }
    let mut singles: HashMap<Episode, i64> = HashMap::new();
    for row in rows {
        let (Some(season), Some(start)) = (row.season, row.episode_start) else { continue };
        if row.excluded || row.state != "playable" || is_compilation(row) {
            continue;
        }
        let held = singles.entry((season, start)).or_insert(0);
        *held = (*held).max(height(row).unwrap_or(0)).max(1);
    }
    let takes: Vec<bool> = refs.iter().map(|each| {
        resolve(index, Some(each.season), None, Some(&each.key)).into_iter()
            .find(|lever| lever.lever == names::COMPILATION_TAKE).map_or(true, |lever| &*lever.value != "false")
    }).collect();
    let mut covering: Vec<(Episode, Vec<usize>)> = Vec::new();
    let mut at: HashMap<Episode, usize> = HashMap::new();
    for (place, each) in refs.iter().enumerate() {
        for episode in each.start..=each.end {
            let slot = (each.season, episode);
            let row = *at.entry(slot).or_insert_with(|| {
                covering.push((slot, Vec::new()));
                covering.len() - 1
            });
            covering[row].1.push(place);
        }
    }
    let better = |place: usize, slot: Episode| singles.get(&slot).is_some_and(|single| refs[place].height.unwrap_or(0) > *single);
    let rank = |place: usize| {
        let each = &refs[place];
        (each.end - each.start, -each.height.unwrap_or(0), source_order.get(&each.source_id).copied()
            .unwrap_or(source_order.len()), each.key.clone())
    };
    let mut members: HashMap<Episode, usize> = HashMap::new();
    let mut own: HashSet<Episode> = HashSet::new();
    for (slot, here) in &covering {
        let word = word_of(settings, *slot);
        let fit: Vec<usize> = here.iter().copied().filter(|place| takes[*place] && better(*place, *slot)).collect();
        let mut chosen = None;
        if let Some(word) = &word {
            own.insert(*slot);
            chosen = fit.iter().copied().find(|place| &refs[*place].key == word);
            if chosen.is_none() && word.is_empty() {
                continue;
            }
        }
        if chosen.is_none() {
            if word.as_deref().is_some_and(|word| !word.is_empty()) {
                own.remove(slot); // the word names a compilation that does not take the episode any more: the rule decides
            }
            chosen = fit.iter().copied().min_by_key(|place| rank(*place));
        }
        if let Some(place) = chosen {
            members.insert(*slot, place);
        }
    }
    let seasons: BTreeSet<i64> = refs.iter().map(|each| each.season).collect();
    let mut views = String::from("[");
    let mut view_keys = Vec::new();
    for season in seasons {
        let mut ordered: Vec<usize> = (0..refs.len()).filter(|place| refs[*place].season == season).collect();
        ordered.sort_by(|one, two| (refs[*one].start, refs[*one].end, &refs[*one].key)
            .cmp(&(refs[*two].start, refs[*two].end, &refs[*two].key)));
        for (index, place) in ordered.into_iter().enumerate() {
            let each = &refs[place];
            view_keys.push((season, each.key.clone()));
            if views.len() > 1 {
                views.push(',');
            }
            views.push_str(&format!("{{\"season\":{season},\"index\":{index},\"ref\":{},\"take\":{},\"episodes\":[",
                                    each.json(), takes[place]));
            for (at, episode) in (each.start..=each.end).enumerate() {
                let slot = (season, episode);
                let member = members.get(&slot).copied();
                if at > 0 {
                    views.push(',');
                }
                let mine = slot_is(&own, slot);
                if member.is_some_and(|member| refs[member].key == each.key) {
                    views.push_str(&format!("{{\"episode\":{episode},\"member\":true,\"absence\":null,\"elsewhere\":null,\"own\":{mine}}}"));
                    continue;
                }
                let absence = if !takes[place] { "off" } else if !singles.contains_key(&slot) { "no_single" }
                    else if !better(place, slot) { "not_better" } else if member.is_some() { "elsewhere" } else { "own" };
                let elsewhere = match (absence, member) {
                    ("elsewhere", Some(member)) => json_text(&refs[member].key),
                    _ => "null".into(),
                };
                views.push_str(&format!("{{\"episode\":{episode},\"member\":false,\"absence\":\"{absence}\",\"elsewhere\":{elsewhere},\"own\":{mine}}}"));
            }
            views.push_str("]}");
        }
    }
    views.push(']');
    Outcome { refs, members, views, view_keys }
}

fn slot_is(own: &HashSet<Episode>, slot: Episode) -> bool {
    own.contains(&slot)
}

fn unknown(label: &str) -> bool {
    let (head, tail) = UNKNOWN_VOICE_LABEL.split_once("{number}").unwrap();
    label.strip_prefix(head).and_then(|rest| rest.strip_suffix(tail))
        .is_some_and(|number| !number.is_empty() && number.chars().all(|each| each.is_ascii_digit()))
}

/// season -> the voice-over groups in the order of the tracks of the container: what was ordered keeps its place, a
/// group new to the season takes the end in the order of the layout (renames are the writer's: none here).
pub fn season_orders(previous: &BTreeMap<i64, Vec<String>>, rows: &[&Row]) -> BTreeMap<i64, Vec<String>> {
    let mut present: BTreeMap<i64, Vec<String>> = BTreeMap::new();
    for row in rows.iter().filter(|row| in_output(row)) {
        let labels = present.entry(row.season.unwrap()).or_default();
        if !labels.contains(&row.voice_label) {
            labels.push(row.voice_label.clone());
        }
    }
    let seasons: BTreeSet<i64> = previous.keys().chain(present.keys()).copied().collect();
    seasons.into_iter().map(|season| {
        let mut kept: Vec<String> = Vec::new();
        for label in previous.get(&season).into_iter().flatten() {
            if !kept.contains(label) && !unknown(label) {
                kept.push(label.clone());
            }
        }
        for label in present.get(&season).into_iter().flatten() {
            if !kept.contains(label) {
                kept.push(label.clone());
            }
        }
        (season, kept)
    }).collect()
}

/// `1–50, 52` of a set of episode numbers.
pub fn spans(numbers: &BTreeSet<i64>) -> String {
    let mut runs: Vec<(i64, i64)> = Vec::new();
    for &number in numbers {
        match runs.last_mut() {
            Some(last) if number == last.1 + 1 => last.1 = number,
            _ => runs.push((number, number)),
        }
    }
    runs.iter().map(|(first, last)| if first == last { first.to_string() } else { format!("{first}–{last}") })
        .collect::<Vec<_>>().join(", ")
}

/// The whole order of every season with the episodes each group's rows have now (the JSON of `voice_orders`).
pub fn season_voices(orders: &BTreeMap<i64, Vec<String>>, rows: &[&Row]) -> String {
    let mut episodes: HashMap<(i64, String), BTreeSet<i64>> = HashMap::new();
    for row in rows.iter().filter(|row| in_output(row)) {
        episodes.entry((row.season.unwrap(), row.voice_label.clone())).or_default()
            .extend(covered(row).into_iter().map(|(_, number)| number));
    }
    let empty = BTreeSet::new();
    let parts: Vec<String> = orders.iter().filter(|(_, labels)| !labels.is_empty()).map(|(season, labels)| {
        let voices: Vec<String> = labels.iter().map(|label| format!("{{\"label\":{},\"episodes\":{}}}", json_text(label),
            json_text(&spans(episodes.get(&(*season, label.clone())).unwrap_or(&empty))))).collect();
        format!("{{\"season\":{season},\"voices\":[{}]}}", voices.join(","))
    }).collect();
    format!("[{}]", parts.join(","))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_and_unknown_labels() {
        assert_eq!(spans(&[1, 2, 3, 5, 7, 8].into_iter().collect()), "1–3, 5, 7–8");
        assert!(unknown("Неизвестная озвучка 12") && !unknown("Неизвестная озвучка ") && !unknown("Рутина"));
    }
}
