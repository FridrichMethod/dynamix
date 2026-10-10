# Artist-family test fixtures

Small offline inputs for `tests/test_fetch_artists.py`. Shapes follow live responses observed on 2026-10-10 (pixiv `ajax/illust` and `/pages`, X `tweet-result`, SoundCloud `__sc_hydration`, Bandcamp JSON-LD, ArtStation project JSON); contents are trimmed and partly synthetic: the X post gains a second photo and a video, the ArtStation project and the pixiv caption are reconstructions from the research notes. No artwork bytes are stored; tests generate tiny images.
