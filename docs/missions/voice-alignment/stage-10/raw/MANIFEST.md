# Опись сырья этапа 10

> Дата: 2026-11-01. Состояние: актуален. Каталог `raw/` вне git: в ответах есть подписанные ссылки потоков и токен
> страницы плеера VK. Файлы не правятся; новое сырьё — новыми файлами.

| Файл | Что | Как снято |
|---|---|---|
| `run1/index.jsonl` | опись запросов прогона: файл, адрес, статус, размер, время | 2026-11-01, httpx, User-Agent и заголовки — из кода плееров приложения |
| `run1/ruchime__release.html`, `ruchime__ALL_EPS.json` | страница релиза «Практикуя ци сто тысяч лет» на `1.ruchime.org`; список серий `ALL_EPS` (390 строк) | то же |
| `run1/ruchime__episode-{1,390}.html` | страницы серий 1 и 390 с кнопками плееров | то же |
| `run1/ruchime__episode-*__vk_embed.html`, `__vk_video_get.json` | страница плеера VK с обычным User-Agent (без токена и файлов) | то же |
| `run1/ruchime__episode-*__allvideo_embed.html` | ответ плеера AllVideo | то же |
| `run1/dunhuatv__title.html` | страница тайтла того же сериала на `dunhuatv.ru` | то же |
| `run1/dunhuatv__cvh_playlist.json` | плейлист CVH (607 строк) | `cdnvideohub.playlist_request` |
| `run1/dunhuatv__cvh_video__*.json` | видео CVH трёх озвучек: длительность и лестница потоков | `GET …/player/sv/video/<vkId>` |
| `run1/dunhuatv__kodik_default.html`, `dunhuatv__kodik_voice{0,1,2}.html` | iframe Kodik по каждой озвучке: списки озвучек, сезонов, серий | `KodikPlayer.open_page` |
| `run1/dunhuatv__kodik_ftor.json` | ответ `ftor` на первую серию первой озвучки | `KodikPlayer.fetch` |
| `run1/vk__{46,52}__resolve.json`, `__video.search.json` | канал VK: `utils.resolveScreenName`, `video.search` с `owner_id` (пустой ответ) | VK API 5.199, токен приложения |
| `run2_vk/index.jsonl`, `vk__{46,52}__video.get.json`, `vk__52__video.search_noowner.json` | `video.get` по каналу; `video.search` без `owner_id` | то же |
| `run3_players/ruchime__vk_embed_agent0.html`, `ruchime__vk_video_get_agent0.json` | страница плеера VK с агентом приложения (с токеном) и ответ `video.get` | `players/vk.py::AGENTS`, `vk_files.call_form` |
| `run3_players/vibix__{praktikuia-ci-sto-tysiac-let,asura-bezumnyi-korol-2}__api_envelope.json`, `__api_decoded.json` | каталог Vibix: ответ API как пришёл и расшифрованный | браузер firefox (Playwright), `capture_with_browser`, ключ `torrent_cache/vibix-key.json` (не изменён) |
| `run4_kodik_durations.jsonl` | длительность потоков пунктов Kodik «1 серия», «108 серия», «109-110 серия», «1-3 серия», «49-55 серия» | `KodikPlayer.fetch` + `ffprobe` потока 360p |
| `run5_cvh_durations.jsonl` | длительность видео CVH серий 108–110 озвучек AniMy и AniStar | `GET …/player/sv/video/<vkId>` |
