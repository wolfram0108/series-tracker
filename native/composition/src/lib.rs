//! The layout of a series as the module composition keeps it: the rows of all its sources, each with the numbers the
//! constructor gave it. The table `composition_rows` is the module's own memory (Р-7): its SQL lives here, in the
//! module's part of the core, and nowhere else.

pub mod levers;
pub mod settings;

use rusqlite::{Connection, OpenFlags};

/// A row of the layout, one column of `composition_rows` per field (core/contracts/composition.py CompositionRow).
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub series_id: i64,
    pub source_id: i64,
    pub row_key: String,
    pub excluded: bool,
    pub exclusion: Option<String>,
    pub season: Option<i64>,
    pub episode_start: Option<i64>,
    pub episode_end: Option<i64>,
    pub part: Option<i64>,
    pub voice_group: Option<String>,
    pub voice_label: String,
    pub voice_name: String,
    pub voice_unknown: Option<i64>,
    pub player: String,
    pub player_label: String,
    pub group: String,
    pub text: String,
    pub heights: Vec<i64>,
    pub state: String,
    pub published: Option<String>,
    pub link: String,
    pub video: String,
    pub stale: bool,
    pub timeline: String,
    pub track: String,
    pub language: String,
    pub subtitle_type: String,
}

/// The columns in the order every reader of the table takes them.
pub const COLUMNS: &str = "series_id, source_id, row_key, excluded, exclusion, season, episode_start, episode_end, part, \
    voice_group, voice_label, voice_name, voice_unknown, player, player_label, grp, text, heights, state, published, \
    link, video, stale, timeline, track, language, subtitle_type";

/// `heights` is kept as a JSON list of integers: `[1080, 720]`.
pub fn heights_of(text: &str) -> Result<Vec<i64>, String> {
    let inner = text.trim().strip_prefix('[').and_then(|rest| rest.strip_suffix(']'))
        .ok_or_else(|| format!("heights are not a list: {text}"))?;
    inner.split(',').map(str::trim).filter(|each| !each.is_empty())
        .map(|each| each.parse::<i64>().map_err(|_| format!("a height is not a number: {each}")))
        .collect()
}

/// The rows of a series in the order of the layout, read from the base of the service through a connection that only
/// reads.
pub fn rows_of(path: &str, series_id: i64) -> Result<Vec<Row>, String> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|exc| exc.to_string())?;
    let mut query = conn.prepare_cached(&format!("SELECT {COLUMNS} FROM composition_rows WHERE series_id = ?1 ORDER BY seq"))
        .map_err(|exc| exc.to_string())?;
    let rows = query.query_map([series_id], |row| {
        let heights: String = row.get(17)?;
        Ok((Row {
            series_id: row.get(0)?, source_id: row.get(1)?, row_key: row.get(2)?, excluded: row.get::<_, i64>(3)? != 0,
            exclusion: row.get(4)?, season: row.get(5)?, episode_start: row.get(6)?, episode_end: row.get(7)?,
            part: row.get(8)?, voice_group: row.get(9)?, voice_label: row.get(10)?, voice_name: row.get(11)?,
            voice_unknown: row.get(12)?, player: row.get(13)?, player_label: row.get(14)?, group: row.get(15)?,
            text: row.get(16)?, heights: Vec::new(), state: row.get(18)?, published: row.get(19)?, link: row.get(20)?,
            video: row.get(21)?, stale: row.get::<_, i64>(22)? != 0, timeline: row.get(23)?, track: row.get(24)?,
            language: row.get(25)?, subtitle_type: row.get(26)?,
        }, heights))
    }).map_err(|exc| exc.to_string())?;
    rows.map(|each| {
        let (mut row, heights) = each.map_err(|exc| exc.to_string())?;
        row.heights = heights_of(&heights)?;
        Ok(row)
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heights_are_read_as_the_module_writes_them() {
        assert_eq!(heights_of("[]").unwrap(), Vec::<i64>::new());
        assert_eq!(heights_of("[1080, 720]").unwrap(), vec![1080, 720]);
        assert!(heights_of("1080").is_err());
    }

    #[test]
    fn rows_come_in_the_order_of_the_layout_with_every_column() {
        let dir = std::env::temp_dir().join(format!("st-composition-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("base.db");
        let _ = std::fs::remove_file(&path);
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch("CREATE TABLE composition_rows (series_id INTEGER, source_id INTEGER, row_key TEXT, seq INTEGER, \
            excluded INTEGER, exclusion TEXT, season INTEGER, episode_start INTEGER, episode_end INTEGER, part INTEGER, \
            voice_group TEXT, voice_label TEXT, voice_name TEXT, voice_unknown INTEGER, player TEXT, player_label TEXT, \
            grp TEXT, text TEXT, heights TEXT, state TEXT, published TEXT, link TEXT, video TEXT, stale INTEGER, \
            timeline TEXT, track TEXT, language TEXT, subtitle_type TEXT);
            INSERT INTO composition_rows VALUES (7, 1, 'b', 1, 0, NULL, 1, 2, 2, NULL, 'G', 'G', 'G', NULL, 'vk', 'VK', '', \
            'two', '[720]', 'playable', NULL, 'l', 'v', 0, '', 'sound', '', '');
            INSERT INTO composition_rows VALUES (7, 1, 'a', 0, 1, 'rule', NULL, NULL, NULL, NULL, NULL, '', '', 3, 'vk', '', '', \
            'one', '[]', 'absent', '2026', '', '', 1, 't', 'subtitles', 'ru', 'full');").unwrap();
        drop(conn);
        let rows = rows_of(path.to_str().unwrap(), 7).unwrap();
        assert_eq!(rows.iter().map(|row| row.row_key.as_str()).collect::<Vec<_>>(), vec!["a", "b"]);
        assert!(rows[0].excluded && rows[0].stale && rows[0].exclusion.as_deref() == Some("rule"));
        assert_eq!((rows[1].season, rows[1].episode_start, rows[1].heights.clone()), (Some(1), Some(2), vec![720]));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
