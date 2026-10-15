# Vendored Blessed checkers

Exact copies of the upstream checkers from
[JonathanPorta/blessed-cicd](https://github.com/JonathanPorta/blessed-cicd),
taken at the commit in `COMMIT`. `VENDOR-SHA256SUMS.txt` lists the checksum of
each file, relative to this directory. Do not edit these files. To update them,
re-copy from upstream, then refresh `COMMIT` and the checksums.

```bash
(cd tools/blessed && shasum -a 256 -c VENDOR-SHA256SUMS.txt)
```

They are run through `make spec-check` and `make design-check`. Both report
only, unless run with `ARGS=--strict`, as CI does. Both need `yq` (mikefarah)
and `jq`.
