//! The forms the modules agree on (core/contracts): the target of a lever that points at a variant or at a voice-over
//! group, and the key of a compilation. Each form is made and read here only: a JSON array after a prefix, so that no
//! name, however odd, can be taken for a part of another, and one writing of it (a form read back is the form made of
//! what was read, or it is no form).

use serde_json::Value;

/// The names of the levers of the output: the one place of them (core/contracts takes them from here).
pub mod names {
    pub const REFERENCE_ORDER: &str = "reference.order";
    pub const REFERENCE_ALLOW: &str = "reference.allow";
    pub const VOICE_ORDER: &str = "voice.order";
    pub const VOICE_ALLOW: &str = "voice.allow";
    pub const VOICE_TAKE: &str = "voice.take";
    pub const COMPILATION_MEMBER: &str = "compilation.member";
    pub const COMPILATION_TAKE: &str = "compilation.take";
    pub const MARKUP_ORDER: &str = "markup.order";
    pub const MARKUP_ALLOW: &str = "markup.allow";
    pub const REFERENCE_SAME: &str = "reference.same";
    pub const MARKUP_SAME: &str = "markup.same";
    pub const SUBTITLES_ORDER: &str = "subtitles.order";
    pub const SUBTITLES_ALLOW: &str = "subtitles.allow";
    pub const ALL: &[(&str, &str)] = &[
        ("REFERENCE_ORDER", REFERENCE_ORDER), ("REFERENCE_ALLOW", REFERENCE_ALLOW), ("VOICE_ORDER", VOICE_ORDER),
        ("VOICE_ALLOW", VOICE_ALLOW), ("VOICE_TAKE", VOICE_TAKE), ("COMPILATION_MEMBER", COMPILATION_MEMBER),
        ("COMPILATION_TAKE", COMPILATION_TAKE), ("MARKUP_ORDER", MARKUP_ORDER), ("MARKUP_ALLOW", MARKUP_ALLOW),
        ("REFERENCE_SAME", REFERENCE_SAME), ("MARKUP_SAME", MARKUP_SAME), ("SUBTITLES_ORDER", SUBTITLES_ORDER),
        ("SUBTITLES_ALLOW", SUBTITLES_ALLOW),
    ];
}

const VARIANT: &str = "variant:";
const GROUP: &str = "group:";
const COMPILATION: &str = "compilation:";

/// A text as JSON writes it with `ensure_ascii=False`: the escapes of Python's `json.dumps`, the rest as it is.
pub fn json_text(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for each in text.chars() {
        match each {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            other if (other as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", other as u32)),
            other => out.push(other),
        }
    }
    out.push('"');
    out
}

/// An integer of JSON: a number with no fraction (a flag is no number).
pub fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) if number.is_i64() => number.as_i64(),
        _ => None,
    }
}

fn text(value: &Value) -> Option<&str> {
    value.as_str()
}

/// The target of a variant: (voice-over group, source id, player).
pub fn variant_target(group: &str, source_id: i64, player: &str) -> String {
    format!("{VARIANT}[{},{},{}]", json_text(group), source_id, json_text(player))
}

pub fn parse_variant_target(target: &str) -> Option<(String, i64, String)> {
    let found: Value = serde_json::from_str(target.strip_prefix(VARIANT)?).ok()?;
    let items = found.as_array()?;
    if items.len() != 3 {
        return None;
    }
    let (group, source_id, player) = (text(&items[0])?, integer(&items[1])?, text(&items[2])?);
    if group.is_empty() || player.is_empty() || variant_target(group, source_id, player) != target {
        return None;
    }
    Some((group.to_string(), source_id, player.to_string()))
}

/// The target of a voice-over group.
pub fn group_target(name: &str) -> String {
    format!("{GROUP}{name}")
}

pub fn parse_group_target(target: &str) -> Option<String> {
    let name = target.strip_prefix(GROUP)?;
    (!name.is_empty()).then(|| name.to_string())
}

/// The key of a compilation: (voice-over group, source id, player, season, start, end).
pub fn compilation_key(group: &str, source_id: i64, player: &str, season: i64, start: i64, end: i64) -> String {
    format!("{COMPILATION}[{},{},{},{},{},{}]", json_text(group), source_id, json_text(player), season, start, end)
}

pub type CompilationKey = (String, i64, String, i64, i64, i64);

pub fn parse_compilation_key(key: &str) -> Option<CompilationKey> {
    let found: Value = serde_json::from_str(key.strip_prefix(COMPILATION)?).ok()?;
    let items = found.as_array()?;
    if items.len() != 6 {
        return None;
    }
    let (group, player) = (text(&items[0])?, text(&items[2])?);
    let (source_id, season, start, end) = (integer(&items[1])?, integer(&items[3])?, integer(&items[4])?,
                                           integer(&items[5])?);
    if player.is_empty() || end <= start || compilation_key(group, source_id, player, season, start, end) != key {
        return None;
    }
    Some((group.to_string(), source_id, player.to_string(), season, start, end))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_form_read_back_is_the_form_made_of_what_was_read() {
        let target = variant_target("Студия \"А\"\n", 3, "vk");
        assert_eq!(target, "variant:[\"Студия \\\"А\\\"\\n\",3,\"vk\"]");
        assert_eq!(parse_variant_target(&target), Some(("Студия \"А\"\n".to_string(), 3, "vk".to_string())));
        assert_eq!(parse_variant_target("variant:[\"A\", 3, \"vk\"]"), None); // another writing of it is no form
        assert_eq!(parse_variant_target("variant:[\"A\",true,\"vk\"]"), None);
        assert_eq!(parse_variant_target("variant:[\"\",3,\"vk\"]"), None);
        assert_eq!(parse_group_target("group:"), None);
        assert_eq!(parse_group_target("group:Рутина"), Some("Рутина".to_string()));
        let key = compilation_key("", 1, "kodik", 1, 3, 5);
        assert_eq!(parse_compilation_key(&key), Some((String::new(), 1, "kodik".to_string(), 1, 3, 5)));
        assert_eq!(parse_compilation_key(&compilation_key("G", 1, "kodik", 1, 5, 5)), None);
        assert_eq!(json_text("\u{1f}\u{7f}\u{2028}"), "\"\\u001f\u{7f}\u{2028}\"");
    }
}
