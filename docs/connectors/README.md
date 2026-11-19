# Writing a connector

A connector discovers resources in a source and fetches their content. See
`src/webmind/ingestion/connectors/base.py`. It is read-only by interface (no write methods).

```python
class MyConnector(SourceConnector):
    type = "my_source"

    async def discover(self):                 # async generator
        self.discovery_complete = False
        async for item in my_api.list():
            yield ResourceRef(uri=item.id, change_hint=item.version)
        self.discovery_complete = True        # only if the listing was complete

    async def fetch(self, ref, previous=None):
        if previous and previous.change_hint == ref.change_hint:
            return FetchResult(status="not_modified", uri=ref.uri)
        data = await my_api.get(ref.uri)      # read-only credentials!
        return FetchResult(status="ok", uri=ref.uri, content=data, content_type="text/html",
                           public_url=..., change_hint=ref.change_hint)
```

Rules:

- `discovery_complete` may be `True` only if every resource was enumerated, because the engine
  treats unseen resources as deleted. If unsure, leave it `False`: the engine then confirms
  each missing resource with `confirm_missing` before deleting anything.
- Return `status="gone"` only for definitive absence (e.g. 404/410), never for transient errors
  (use `"error"`, which keeps the existing index).
- Enforce size limits and scope yourself; never follow redirects off the configured host.
- Content types understood by the parsers: HTML, Markdown, text, PDF, DOCX.
- Add a source config model in `core/config.py` (a member of the `SourceConfig` union) and
  register it in `build_connector` in `ingestion/sync.py`.
- Add tests: new/changed/deleted/unchanged, failures, truncated discovery.

Structured sources are configured as `datasets` rather than connectors, so they are
queried live through the validated query compiler instead of being copied into the index.
