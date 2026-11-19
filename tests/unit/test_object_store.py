import pytest

from webmind.storage.object_store import ContentStore, LocalObjectStore, sha256_hex


def test_cas_roundtrip_and_dedup(tmp_path):
    cas = ContentStore(LocalObjectStore(tmp_path))
    data = b"hello world " * 1000
    first = cas.put(data)
    assert first.sha256 == sha256_hex(data)
    assert not first.deduplicated
    assert first.stored_size < first.size  # zstd compresses repetitive content
    second = cas.put(data)
    assert second.deduplicated
    assert cas.get(first.sha256) == data


def test_cas_detects_corruption(tmp_path):
    store = LocalObjectStore(tmp_path)
    cas = ContentStore(store)
    blob = cas.put(b"original")
    other = ContentStore(store)._cctx.compress(b"tampered")
    store.put(cas.key_for(blob.sha256), other)
    with pytest.raises(ValueError, match="integrity"):
        cas.get(blob.sha256)


@pytest.mark.security
@pytest.mark.parametrize("key", ["../escape", "/abs/path", "a/../../b", "", "a\\..\\b"])
def test_object_keys_cannot_escape_root(tmp_path, key):
    store = LocalObjectStore(tmp_path / "root")
    with pytest.raises(ValueError):
        store.put(key, b"x")
