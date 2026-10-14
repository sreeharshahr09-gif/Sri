# Tire Scout TLS repair and verification

Date: 13 October 2026 (India time).

## Root cause

The supplied `certs/corp-root.crt` was already PEM. It contained two
certificates: the intercepted `*.tavily.com` server certificate and the Apollo
forward-trust CA. Running `certutil -encode` on that PEM file encoded the entire
text again. Consequently, the final block in `corp-root.pem` and
`agent-ca-bundle.pem` decoded to another PEM document instead of DER certificate
bytes. This reproduced `[X509] PEM lib`. It was not a missing-newline problem.

## Repair

- Preserved the original browser export, malformed PEM, and malformed bundle in
  `certs/backups/20261012T202541621724Z/` (timestamp is UTC).
- Matched the corporate CA certificates against Windows' existing trusted ROOT
  store. Did not add trust to Windows or use the Tavily server certificate as a CA.
- Rebuilt `certs/corp-root.pem` from the actual corporate root and forward-trust
  CA, and rebuilt `certs/agent-ca-bundle.pem` byte-wise from certifi plus those CAs.
- The bundle contains 123 distinct CA certificates: 121 public and 2 corporate.
- The saved user `SSL_CERT_FILE` already points at the correct bundle path and
  remains unchanged. The verification process loaded that setting and the
  existing search-key variable into its environment without printing their secret
  values or saving secrets in files.
- Added `scripts/rebuild_ca_bundle.py` to reproduce the repair without extra
  dependencies. It requires the selected CAs to be present in Windows' server-auth
  trust store, validates CA status and dates, validates the complete bundle before
  replacement, backs up existing files, and writes canonical PEM bytes.

Corporate CA identities:

| Certificate | Windows thumbprint | Expiry (India time) |
|---|---|---|
| Apollo root CA | `831A93699724F7517DDCA71B4D25FFA80C59CE95` | 19 Nov 2026, 10:30:14 |
| Apollo forward-trust CA | `DBFCD20DE7B7B7D5652B9E71E28B7D863D316EBB` | 19 Nov 2026, 10:30:16 |

Bundle SHA-256:
`409c0f69cc81986191f8b002da8b8f9944bd12f91d2189c568e7697c4fd6df2a`.

The corporate certificates require renewal before 19 November 2026. After IT
deploys replacement CAs, confirm their Windows trust-store thumbprints and supply
them with repeated `--ca-thumbprint` arguments to the rebuild script. A static
bundle does not automatically follow Windows certificate rotation or certifi
package updates.

## Verification

| Check | Result |
|---|---|
| Python 3.12 `ssl.create_default_context(cafile=...)` | Pass, 123 CAs |
| Certificate enforcement | `CERT_REQUIRED`, hostname checking enabled |
| `httpx.get('https://api.tavily.com')` using `SSL_CERT_FILE` | HTTP 200 |
| `httpx.get('https://www.python.org')` using `SSL_CERT_FILE` | HTTP 200 |
| Local llama.cpp health endpoint | HTTP 200 |
| Configured Qwen structured-output capability | Pass, JSON-schema mode |
| Live search and source retrieval | 8 queries, 40 URLs; 33 fetched, no TLS failures |
| Offline regression suite | 95 passed, 1 skipped in 8.01 s |
| Production daily research | Pass: `ok: true`, `status: research_complete`, `send_state: preview` |

The test skip is the real-symlink test: Windows returned error 1314 because this
account lacks symlink creation privileges. Resolved-path escape rejection is
still tested without that privilege. The initial test attempt also encountered
the automation sandbox's inaccessible default temporary directory; the final
suite uses a new writable project-local `--basetemp`.

All three email gates remain false and the adapter remains `preview`.
No message was authorized for live sending. VPN state was not changed; the HTTPS
checks succeeded on the current connection. Alternate VPN states were not tested.

## Small Windows fix found during regression verification

`src/tire_scout/sandbox.py` used a string prefix ending in `/` to validate output
paths. On Windows, resolved paths use backslashes, so valid files were rejected
as traversal attempts. Replaced that expression with `Path.is_relative_to(root)`.
Valid nested artifacts and escapes to a sibling with a matching name prefix are
both covered. No Ollama or OpenAI-compatible provider code changed.

Test-only adjustments:

- The full-text HTML fixture produced 399 characters with installed trafilatura,
  one below the existing 400-character threshold. Added source-like fixture text
  so the full-text scenario exercises the intended branch with either extractor.
- Skip only Windows error 1314 in the actual-symlink test.
- Test resolved-path traversal without requiring symlink privileges.
- Keep the timeout test's temporary job under pytest's writable temporary root.

## Running again

In an Anaconda Prompt:

```bat
conda activate tire-scout
cd /d C:\Tyre-scout-agent
set "SSL_CERT_FILE=C:\Tyre-scout-agent\certs\agent-ca-bundle.pem"
python -c "import ssl; c=ssl.create_default_context(); print(len(c.get_ca_certs()))"
tire-scout daily --mode production
```

The existing `TIRE_SCOUT_SEARCH_KEY` must be inherited from the saved user
environment; an already-open application may have an older environment. The
local llama-server must be running. Email stays in preview under the existing
configuration.

To rebuild the bundle with the currently pinned corporate CAs:

```bat
python scripts\rebuild_ca_bundle.py
```

Do not Base64-encode a PEM file again. Never turn off certificate verification.

## Audit files

`data/audits/ssl-fix-2026-10-13/` contains the daily command output, final test
output, pre-run consistent SQLite backups, and source/configuration hashes.
The certificate backup directory contains `rebuild.json` with certificate
identities and bundle hashes. Original historical daily editions and imported
delivery records were preserved.

## Final production result

The production command exited with code 0. Job
`42edd19e631a47fb9afa4916053285cd` is recorded as `succeeded`, with no job error
and no remaining lock. It ran for 30.45 minutes, longer than the handoff's
5–20 minute estimate; the local model continued making progress throughout.

```json
{
  "ok": true,
  "status": "research_complete",
  "send_state": "preview",
  "edition_date": "2026-10-13",
  "included": 5
}
```

Results: 8 live searches, 40 unique URLs, 40 fetch attempts, 33 successfully
fetched sources, and 30 candidates after filtering/deduplication. The agent
included 5 items, recorded 10 exclusions, and quarantined 18 analyses. The CLI's
`coverage.fetched: 40` counts attempted documents, not successful retrievals.
Seven unavailable sources had HTTP 403/404 or redirect-limit errors; none had
TLS errors. The evidence checks were kept intact.

Saved report files:

- `data/reports/daily/daily-2026-10-13.html` (7,507 bytes)
- `data/reports/daily/daily-2026-10-13.json` (16,910 bytes)

The delivery journal records `state=preview`, `provider=preview`, and no
submission/confirmation timestamps. No live mail was sent. All pre-existing
delivery rows and historical daily editions match the consistent pre-run backup.
The complete configuration file is byte-for-byte unchanged. The only package
source change is the Windows sandbox path check described above; its original
file is retained as a hash-verified audit backup.

The final scan checked 438 workspace files and found no occurrence of the stored
Tavily key. Structured evidence is saved in
`data/audits/ssl-fix-2026-10-13/final-verification.json`; the exact command output
is in `daily-run.txt`, and the final regression result is in `pytest-final.txt`.
The agent emitted optional `fontTools` warnings while extracting some PDF fonts;
no dependency installation was needed to complete this run. Editorial review of
the generated research remains the next rollout step; scheduling and go-live
settings were not changed.
