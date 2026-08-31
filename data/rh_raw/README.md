# Robinhood raw MCP payloads

Drop one JSON file per symbol/expiration here, then run
`python3 tools/rh_ingest.py` to normalise them into `../rh_chains/`.

```json
{
  "symbol": "MCD",
  "expiration": "2026-10-02",
  "spot": 263.54,
  "instruments": [ "<get_option_instruments -> data.instruments>" ],
  "quotes":      [ "<get_option_quotes  -> data.results[].quote>" ]
}
```

Only the put strikes inside the range reported by `./run.py chain-requests`
are needed — that is the 3–20% OTM band the optimizer searches.

Files in this directory and in `../rh_chains/` are gitignored: they are market
snapshots, not source.
