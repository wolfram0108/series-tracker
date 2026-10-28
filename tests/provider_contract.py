"""One set of checks that every video provider's `parse_release` must pass."""
from core.contracts import Release


def check_release(provider, release: dict) -> Release:
    """The release as the contract describes it; fails with the first thing a provider got wrong."""
    parsed = Release.model_validate(release)
    assert parsed.provider == provider.name
    assert parsed.source_type == "web_video"
    if not provider.has_voice_catalog:
        assert not parsed.sources and parsed.selected_source is None
        return parsed
    assert parsed.sources, "a provider with a voice catalog lists its sources"
    urls = [source.url for source in parsed.sources]
    assert len(set(urls)) == len(urls), "a source is named once"
    assert parsed.selected_source in urls
    for source in parsed.sources:
        assert source.count == len(source.episodes) == len(set(source.episodes))
        assert source.episodes == sorted(source.episodes)
        assert (source.first, source.last) == ((source.episodes[0], source.episodes[-1]) if source.episodes
                                               else (None, None))
        if source.links is not None:
            assert set(source.links) == {str(number) for number in source.episodes}
    return parsed
