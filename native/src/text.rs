//! The forms of text the modules agree on, written as the Python of the service reads them: the folded name of a
//! voice-over, `str.strip`, the decimal digits of `int()`, the host of an address and a reason (`core/reasons.py`).

/// The first digit (zero) of every block of decimal digits Python's `int()` reads (Unicode 15.0, category Nd).
const DECIMAL_ZEROS: [u32; 68] = [
    0x30, 0x660, 0x6F0, 0x7C0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66, 0xDE6, 0xE50, 0xED0, 0xF20,
    0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90, 0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620, 0xA8D0,
    0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10, 0x104A0, 0x10D30, 0x11066, 0x110F0, 0x11136, 0x111D0, 0x112F0,
    0x11450, 0x114D0, 0x11650, 0x116C0, 0x11730, 0x118E0, 0x11950, 0x11C50, 0x11D50, 0x11DA0, 0x11F50, 0x16A60, 0x16AC0,
    0x16B50, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6, 0x1E140, 0x1E2F0, 0x1E4F0, 0x1E950, 0x1FBF0,
];

/// The value of a decimal digit of any script, as `int()` reads it.
pub fn decimal(c: char) -> Option<i64> {
    let code = c as u32;
    let at = DECIMAL_ZEROS.partition_point(|zero| *zero <= code);
    if at == 0 {
        return None;
    }
    let offset = code - DECIMAL_ZEROS[at - 1];
    (offset < 10).then_some(offset as i64)
}

/// `str.isspace()`: the white space of Unicode and the separators of files, groups, records and units.
pub fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn py_strip(text: &str) -> &str {
    text.trim_matches(py_space)
}

/// `int(text)` of a text: white space around, a sign, digits of one or several scripts with single underscores between
/// them; None where Python raises.
pub fn py_int(text: &str) -> Option<i64> {
    let text = py_strip(text);
    let (negative, digits) = match text.chars().next()? {
        '-' => (true, &text[1..]),
        '+' => (false, &text[1..]),
        _ => (false, text),
    };
    let mut value: i64 = 0;
    let mut seen = false;
    let mut underscore = false;
    for c in digits.chars() {
        if c == '_' {
            if !seen || underscore {
                return None;
            }
            underscore = true;
            continue;
        }
        let digit = decimal(c)?;
        value = value.checked_mul(10)?.checked_add(digit)?;
        seen = true;
        underscore = false;
    }
    if !seen || underscore {
        return None;
    }
    Some(if negative { -value } else { value })
}

/// The number a run of digits a pattern captured stands for (`int(match.group(i))`).
pub fn digits_value(text: &str) -> Option<i64> {
    let mut value: i64 = 0;
    for c in text.chars() {
        value = value.checked_mul(10)?.checked_add(decimal(c)?)?;
    }
    Some(value)
}

/// `str.isalnum()` of one character, as `\w` without `_` reads it.
fn word(c: char) -> bool {
    c.is_alphanumeric()
}

/// No case, no spaces, no punctuation (`core.voice_names.fold_name`).
pub fn fold_name(name: &str) -> String {
    caseless::default_case_fold_str(name).chars().filter(|c| word(*c)).collect()
}

/// The host of an address in lower case (`core.contracts.release_table.site_of`: `urlparse(...).hostname`).
pub fn site_of(url: &str) -> String {
    let url = py_strip(url.split('|').next().unwrap_or(""));
    let Some(at) = url.find(':') else { return String::new() };
    let scheme = &url[..at];
    if scheme.is_empty() || !scheme.chars().next().unwrap().is_ascii_alphabetic()
        || !scheme.chars().all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c)) {
        return String::new();
    }
    let Some(rest) = url[at + 1..].strip_prefix("//") else { return String::new() };
    let netloc = &rest[..rest.find(|c| c == '/' || c == '?' || c == '#').unwrap_or(rest.len())];
    let host = netloc.rsplit('@').next().unwrap_or("");
    let host = if let Some(inner) = host.strip_prefix('[') { inner.split(']').next().unwrap_or("") }
               else { host.split(':').next().unwrap_or("") };
    host.to_lowercase()
}

/// A value of a reason: a text or a number.
pub enum Param<'a> {
    Text(&'a str),
    Int(i64),
    Json(serde_json::Value),
}

/// `core.reasons.reason(code, **params)`: the compact JSON `{"code": …, params…}` in the order given.
pub fn reason(code: &str, params: &[(&str, Param)]) -> String {
    let mut out = String::from("{\"code\":");
    out.push_str(&serde_json::to_string(code).unwrap());
    for (name, value) in params {
        out.push(',');
        out.push_str(&serde_json::to_string(name).unwrap());
        out.push(':');
        match value {
            Param::Text(text) => out.push_str(&serde_json::to_string(text).unwrap()),
            Param::Int(number) => out.push_str(&number.to_string()),
            Param::Json(value) => out.push_str(&value.to_string()),
        }
    }
    out.push('}');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn int_reads_as_python_does() {
        assert_eq!(py_int(" 12 "), Some(12));
        assert_eq!(py_int("１２"), Some(12));
        assert_eq!(py_int("1_000"), Some(1000));
        assert_eq!(py_int("-3"), Some(-3));
        assert_eq!((py_int("1__0"), py_int("_1"), py_int("1.0"), py_int("")), (None, None, None, None));
    }

    #[test]
    fn a_reason_is_the_compact_json_of_python() {
        assert_eq!(reason("range_item", &[("part", Param::Text("а\"б")), ("n", Param::Int(2))]),
                   r#"{"code":"range_item","part":"а\"б","n":2}"#);
    }

    #[test]
    fn the_host_of_an_address() {
        assert_eq!(site_of("https://User@Ex.COM:8080/a?b"), "ex.com");
        assert_eq!(site_of("title://chines/12/ab"), "chines");
        assert_eq!(site_of("vk.com/x"), "");
    }
}
