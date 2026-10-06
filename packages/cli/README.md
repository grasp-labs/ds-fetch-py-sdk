# ds-fetch-cli

`aic-fetch`: the command line for [AI Commons](https://aic-project.com) Fetch. Run SQL and
natural-language queries over your tenant's data lake from the terminal. Built on
[ds-fetch-py-sdk](https://pypi.org/project/ds-fetch-py-sdk/).

```bash
uv tool install ds-fetch-cli    # or: pipx install ds-fetch-cli
aic-fetch --install-completion  # optional: tab completion for your shell
```

## Usage

```bash
aic-fetch -e dev login                       # browser sign-in, including SSO
aic-fetch -e dev login --email me@aic.no     # email and password; prompts for MFA if enabled
aic-fetch -e dev datasets                    # what you can query, with files, rows and size
aic-fetch -e dev datasets -n gold.5003105c-8a84-5f77-ab74-5e57003112b8   # only these datasets
aic-fetch -e dev dataset gold.5003105c-8a84-5f77-ab74-5e57003112b8
aic-fetch -e dev query 'SELECT department, count(*) FROM gold."5003105c-8a84-5f77-ab74-5e57003112b8" GROUP BY 1'
aic-fetch -e dev query - < report.sql        # SQL from a file or pipe
aic-fetch -e dev validate - < report.sql     # check the SQL without reading any data
aic-fetch -e dev ask "Headcount per department?" -d gold.5003105c-8a84-5f77-ab74-5e57003112b8
aic-fetch -e dev ask "Headcount per department?" --sql-only
aic-fetch -e dev tools                       # ds-tools you can invoke
aic-fetch -e local health
aic-fetch -e dev logout
```


| Option        | Effect                                                                                                                                                      |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-e`, `--env` | `local`, `dev` or `prod`. Default: `$AIC_ENV`, then `prod`                                                                                                  |
| `--json`      | JSON on stdout for scripts, e.g. `aic-fetch --json query '…' | jq`                                                                                          |
| `--fresh`     | On `datasets`, `dataset`, `query`, `validate`, `ask`: read S3 directly, bypassing the index and cache. Default: `$AIC_FRESH`. Needed locally without `ds-fetch-indexer` |
| `--version`   | CLI and SDK versions                                                                                                                                        |


Tables go to stdout; progress, the generated SQL and row counts go to stderr, so output pipes
cleanly. Errors print one line and exit with code 1. Text from the service and the data lake is
shown literally, with control characters replaced, so data cannot drive your terminal.

## Credentials

The CLI uses the first of these it finds:

1. `AIC_TOKEN`: a platform JWT.
2. `AIC_CLIENT_ID` and `AIC_CLIENT_SECRET`: client credentials, for CI and machines.
3. `AIC_EMAIL` and `AIC_PASSWORD` (plus `AIC_MFA_CODE`): email and password sign-in.
4. The session from `aic-fetch login`, cached in `~/.config/aic/credentials.json`.

`local` and `dev` sign in at `https://auth-dev.grasp-daas.com`, `prod` at `https://auth.grasp-daas.com`.

`AIC_FETCH_URL`, `AIC_TOOLS_URL` and `AIC_ISSUER` override single URLs of the chosen environment.

## License

Apache 2.0