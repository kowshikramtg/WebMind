# webmind-client

A small Python client for a self-hosted [WebMind](../../README.md) server, plus a helper
for your backend to sign short-lived user tokens. Dependencies: `httpx`, `pyjwt`.

```bash
pip install ./sdk/python        # from a WebMind checkout
```

```python
import os
from webmind_client import WebMindClient, sign_user_token

# In your backend, after YOUR application has authenticated the user:
token = sign_user_token(
    os.environ["WEBMIND_JWT_SECRET"],  # same secret as security.authentication.jwt.secret
    user_id=current_user.id,
    roles=current_user.roles,  # e.g. ["employee"]
    attributes={"department": current_user.department},
)

with WebMindClient("http://localhost:8080", token=token) as wm:
    res = wm.chat("Retrieve the records from my department between January and June")
    print(res["answer"])
    for c in res["citations"]:
        print(c["index"], c["title"], c["url"])
```

`AsyncWebMindClient` offers the same methods for asyncio apps, plus `stream()` for
server-sent events. See [docs/integration.md](../../docs/integration.md) for complete
integration patterns.
