# Independent static review: Mi 10 Pro HID Device MTU patch

Reviewed original `device/libbluetooth_qti.so`, SHA-256 `2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039`. No device, binary, or IDB mutation was performed. Analysis used Capstone ARM64 disassembly, ELF relocations, the embedded `.gnu_debugdata` symbol table, and the parent's saved decompilation output. Public source comparison is recorded separately in `work/mtu-source-audit.md`.

## Result

**No blocking static issue found in the proposed six-instruction patch.** The six sites coherently update the report bound, report-message allocation, diagnostic, both advertised HID channel MTUs, and the incoming peer-MTU cap/default. Static confidence is high for these specific code paths. Runtime Bluetooth loading, successful long-report transmission, Switch pairing, and amiibo functionality remain unverified by this review.

| Virtual address | Original instruction | Intended replacement | Independent validation |
|---|---|---|---|
| `0x311660` | `cmp w3, #65` | `cmp w3, #513` | Followed by `b.lo` into allocation; report length is unsigned 16-bit. Correctly permits 512 and rejects 513+. |
| `0x31168c` | `mov w4, #64` | `mov w4, #512` | Error-log parameter only. Correct accompanying diagnostic. |
| `0x3116a0` | `mov w0, #78` | `mov w0, #526` | Argument of `osi_malloc`; report data pointer is allocation+14, so exactly 512 bytes fit without moving preceding fields. |
| `0x556f84` | `mov w10, #64` | `mov w10, #512` | Register is used by halfword stores at `0x556fa8` and `0x556fcc`, updating both control and interrupt MTUs. |
| `0x557668` | `mov w8, #64` | `mov w8, #512` | Default value when peer omits MTU and cap value when it supplies a larger MTU. |
| `0x557674` | `cmp w9, #64` | `cmp w9, #512` | `csel w8,w9,w8,lo` preserves peer values below 512 and caps the rest at 512. |

## Send path and allocation checks

- BTIF `send_report` at `0x406024`, the only direct caller of `BTA_HdSendReport` found in `.text`, forwards the 16-bit report length at `0x4060cc` and payload pointer at `0x4060d8`; it has no 64-byte limit or payload copy. Its call is at `0x4060dc`.
- `BTA_HdSendReport` at `0x31161c` builds a report-message allocation with the 16-bit length at +12 and payload at +14. Copy at `0x3116e0` calls PLT entry `0x64f120`. The corresponding GOT relocation at `0x65d570` resolves to ordinary `memcpy`, **not `__memcpy_chk`**; no stale FORTIFY destination bound is present.
- Consumer `bta_hd_send_report_act` at `0x3109e8` loads the message length from +12 and passes the pointer at +14 directly to `HID_DevSendReport` (`0x310a60`). It creates no fixed 64-byte intermediate payload array.
- `HID_DevSendReport` at `0x55674c` validates channel/report type and passes length/pointer through to `hidd_conn_send_data` at `0x55680c`. No 64-byte size cap was found.
- `hidd_conn_send_data` at `0x55872c` allocates **4112 bytes** (`mov w0,#0x1010` at `0x5587dc`), uses a payload offset of 22 or 23 bytes, and calls the same ordinary `memcpy` at `0x55884c`. This allocation comfortably contains a 512-byte payload. It forwards the constructed buffer to `L2CA_DataWrite`; no second 64-byte cap or fixed 64-byte copy appears in this function.
- HID channel registration calls `L2CA_Register` at `0x558c44`, whose embedded symbol is `_Z13L2CA_RegistertP16tL2CAP_APPL_INFOb`. The inspected implementation and call sites use the old `(PSM, callbacks, bool)` interface, **not an additional MTU argument**. No registration-argument patch is missing.

## Duplicate/hidden-cap scope

The embedded symbol table identifies 110 HID-device/BTA-HD/BTIF-HD related symbols including CFI entries and storage functions. A scoped immediate scan for 64, 65, and 78 found exactly the proposed semantic sites; the other hits are stack/frame constants. Direct-branch scans over the library's `.text` section confirmed the above main caller/consumer chain. `bta_hd_send_report_act`'s CFI entry at `0x645fe0` and `hidd_l2cif_config_ind`'s CFI entry at `0x6489e8` are `bti c` plus branch thunks into the reviewed implementations, not duplicate payload-handling implementations.

This is a targeted code-path audit, not a proof over every possible execution of the entire Bluetooth stack. The JNI wrapper, Android framework, application behavior, controller transport, and peer negotiation can independently affect end-to-end behavior. A 512-byte application report plus HID/report-ID overhead can exceed a negotiated 512-byte L2CAP MTU; the intended amiibo reports are smaller, so do not advertise this as a guarantee for every maximum-size report.

## Required verification by module builder

Match the exact input SHA-256 and all original patch bytes; verify each output instruction; refuse unsupported device/library versions. Retain the bounds rejection above 512 and a rollback-capable overlay. Static patch validation should be reported separately from runtime/Switch validation.
