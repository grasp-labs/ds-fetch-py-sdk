# ds-fetch-py-sdk

Python SDK for [AI Commons](https://aic-project.com) Fetch: read-only SQL over your tenant's data
lake, plus natural-language questions through ds-tools. For the command line, see
[ds-fetch-cli](https://pypi.org/project/ds-fetch-cli/).

```bash
uv add ds-fetch-py-sdk            # or: pip install ds-fetch-py-sdk
uv add 'ds-fetch-py-sdk[pandas]'  # adds QueryResult.to_pandas()
```

## Quick start

```python
from ds_fetch_py_sdk import Fetch

with Fetch("dev") as fetch:  # signs in with the browser on first use
    for ds in fetch.datasets():
        print(ds.name, [c.name for c in ds.columns])

    result = fetch.query("""
        SELECT department, count(*) AS n
        FROM gold."5003105c-8a84-5f77-ab74-5e57003112b8"
        GROUP BY department
    """)
    print(result.records())  # [{'department': 'engineering', 'n': 1520}, ...]

    answer = fetch.ask("Headcount per department?", datasets=[ds.name])
    print(answer.sql, answer.rows)
```

## Environments

| Name | Fetch API | Identity server |
|------|-----------|-----------------|
| `local` | `http://localhost:8080/api/fetch-dev/v1` | `https://auth-dev.grasp-daas.com` |
| `dev` | `https://grasp-daas.com/api/fetch-dev/v1` | `https://auth-dev.grasp-daas.com` |
| `prod` (default) | `https://grasp-daas.com/api/fetch/v1` | `https://auth.grasp-daas.com` |

Pick one with `Fetch("local")` or `AIC_ENV=local`. Override single URLs with `AIC_FETCH_URL`,
`AIC_TOOLS_URL` and `AIC_ISSUER`, or pass your own `Environment(...)`. Every URL must use HTTPS,
except on `localhost`, so tokens and passwords never travel in clear text.
API docs: [prod](https://grasp-daas.com/api/fetch/v1/docs/) ·
[dev](https://grasp-daas.com/api/fetch-dev/v1/docs/) ·
[local](http://localhost:8080/api/fetch-dev/v1/docs/).

## Authentication

| Method | Code | Environment |
|--------|------|-------------|
| Bearer token | `Fetch(token="eyJ…")` | `AIC_TOKEN` |
| Client credentials | `Fetch(auth=ClientCredentials(id, secret))` | `AIC_CLIENT_ID`, `AIC_CLIENT_SECRET` |
| Email and password | `Fetch(auth=PasswordLogin(email, password))` | `AIC_EMAIL`, `AIC_PASSWORD`, `AIC_MFA_CODE` |
| Browser sign-in / SSO | `Fetch(auth=BrowserLogin())` | default |

Local and dev sign in at the dev identity server, prod at prod's. Without arguments the client
checks the environment variables in that order, then a cached session, then falls back to
browser sign-in.

`PasswordLogin` posts to the identity server's `/auth/login/`. For accounts with MFA, pass
`mfa_code=` as a code or as a callable that takes the number of digits and returns the code. It
refreshes with `/auth/token/refresh/` and caches the session like browser sign-in, but never the
password.

Every flow signs in at the environment's issuer, never at one a server names. OAuth flows read
its endpoints from RFC 8414 metadata, which must name that issuer and use HTTPS, and bind tokens
to Fetch with `resource` (RFC 8707). Browser sign-in uses authorization code + PKCE on
`http://127.0.0.1:8976/callback`, registers a public client on first use (RFC 7591), and offers
whatever SSO your tenant has configured. Sessions are cached in `~/.config/aic/credentials.json`,
replaced atomically as a mode-600 file. Pass `on_url=` to show the sign-in URL your own way.
Expired tokens are refreshed, and a 401 triggers one renewal and retry.

## Querying

- `query(sql, fresh=False)` runs one `SELECT` and returns a `QueryResult` with `columns`, `rows`,
  `records()`, `to_pandas()`, `truncated`, `elapsed_ms`, `cache` and `request_id`.
  `fresh=True` reads S3 as it is now, bypassing the index and cache.
- `datasets()` iterates every granted dataset across pages; `dataset(name)` returns one.
  `Dataset.ref` is the name quoted for SQL.
- `sql(question, datasets=...)` translates a question to SQL with ds-tools; `ask(...)` also runs it.
- `tools.list()`, `tools.get(id)` and `tools.invoke(id, input)` reach every ds-tools tool with the
  same sign-in.

Retries follow the API contract: connection failures, timeouts, 429, 500, 503 and 5xx responses
without a JSON body are retried up to 3 attempts with exponential backoff, jitter and `Retry-After`. Everything else raises at once.

## Errors

All errors derive from `AICError`.

| Exception | When |
|-----------|------|
| `AuthError` | Sign-in or token acquisition failed |
| `NetworkError` | The API could not be reached or timed out, after retries |
| `APIError` | The API returned an error; has `status`, `code`, `message`, `request_id`, `retry_after` |
| `QueryError` | The SQL was rejected (422); `details` lists each problem, e.g. `unknown_column` |

Pass `language="nb"` for Norwegian error messages.

## License

Apache 2.0
