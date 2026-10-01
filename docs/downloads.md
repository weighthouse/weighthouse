# Downloads

## Download modes

The model page offers five modes:

- **Full repository** — every file in the selected revision.
- **SafeTensors** — safe weights plus the configuration and tokenizer files.
- **One GGUF** — a specific quantization chosen from the repository file list.
- **Metadata only** — configuration, tokenizer, and documentation files, no weights.
- **Custom** — comma-separated include and exclude patterns, forwarded to Hugging Face's `snapshot_download` filter.

Custom pattern examples:

```text
*.safetensors, *.json, tokenizer*        # weights plus config
*Q4_K_M.gguf, *.json, tokenizer*         # one quantization
*.bin, *.pt, *.pth                       # exclude legacy weights (exclude list)
```

## Gated and private models

1. Accept the repository's license or access terms on huggingface.co while signed in.
2. [Create a read-only user access token.](https://huggingface.co/settings/tokens)
3. Set it in `.env` as `HF_TOKEN=hf_your_read_token` and restart the service.

The token is read by the backend only. It is never returned by the API or sent to the browser.

## Revisions

Add a revision to a download to pin something other than `main` — a branch name, tag, or commit hash.

## Watch and updates

A download watches its repository by default; the watch toggle on the model page turns it off or back on. The Watched page lists every subscription and flags repositories whose upstream changed. The model page's Updates section checks the copy against the tracked revision and shows which files were added, changed, or removed upstream. Files are never updated without selecting them; upstream removals are reported but never applied to local files.

`WATCH_POLL_MINUTES` in `.env` sets how often watched repositories are checked (`120` by default).

## Cancel and resume

An active download can be cancelled. Already-transferred files and Hugging Face's local-directory metadata are kept, so starting the same repository again resumes instead of re-downloading. Interrupted downloads recover the same way after a restart.

## Performance and resources

The transfer runs in a separate worker process. Its memory use is bounded by design: the default transport streams files over HTTP with a small fixed buffer, so a 180 GB repository costs the same working set as a 1 GB one.

### Xet transport (opt-in)

Hugging Face serves many repositories from the Xet CAS. With `ENABLE_XET=1` in `.env`, files served that way are fetched by the `hf_xet` client; files served over plain HTTP are unaffected either way. Each job records the transport that ran (`http` or `xet`) in its metadata.

The Xet client is faster on high-bandwidth links but buffers downloads in memory. The working set grows with shard size and the number of parallel files, so expect multi-GB usage on large repositories with several workers. On memory-constrained hosts, leave it off.

With `ENABLE_XET=1`, each job's client log — the Xet client's own memory, CPU, and network report — is written to `<DATA_DIR>/xet/<job id>.log`. The path is recorded in the job metadata.

### Levers

- `MAX_CONCURRENT_DOWNLOADS` (default `2`, cap `8`) — jobs running at once.
- `DOWNLOAD_WORKERS_PER_JOB` (default `4`, cap `16`) — files transferred in parallel within a job.
- `DOWNLOAD_MAX_ATTEMPTS` (default `3`, cap `10`) — retries on transient network failures.
- `DOWNLOAD_RETRY_SECONDS` (default `2`, cap `30`) — pause between attempts.

Memory pressure scales with job concurrency and per-job parallelism. Lower them before abandoning a transfer.

### Reading a failed job

The job shows the transport that ran and, on failure, the worker's final output. A worker killed by SIGKILL was terminated by the operating system — typically an out-of-memory condition on the host or container. Lower the levers above, and with Xet transports consider `ENABLE_XET=0`.

## GGUF inspection

A repository with GGUF files gets a **GGUF** tab on the model page. It reads a bounded byte range of the selected file and shows metadata, tensor names, shapes, data types, quantization breakdown, and parameter count — without downloading the weights. Gated repos use the backend's `HF_TOKEN`.

## What downloads do not do

The application transfers files. It does not execute repository code, import model modules, or deserialize weights. Pickle-based formats only run code when another application loads them.
