# Private Notetaker meetings (Calimero app)

Stores each meeting's transcript, notes and receipt, readable only by the people who were in it.
No audio ever reaches it: the notetaker deletes the audio on the user's device first.

Started from `meroctl app new` (kv-store template, calimero-sdk `1.0.0-rc.31`). Methods, access
rules and the wire format are in `../HANDOVER.md` and the doc comments in `src/lib.rs`.

```bash
rustup target add wasm32-wasip2
cargo test --lib          # unit tests on the SDK's mock host (no wasm needed)
meroctl local test .      # scenarios/meetings.json on the real engine
meroctl app build .       # app.component.wasm
meroctl app abi show app.component.wasm > res/abi.json
```

`res/abi.json` is not committed yet: it needs the component build.
