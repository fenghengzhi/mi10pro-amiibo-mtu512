# HID Device MTU 64 -> 512: source audit

Device reported by parent: Xiaomi Mi 10 Pro (`cmi`), Android 13 / API 33, build `V816.0.9.0.TJACNXM`; active stack `/system_ext/lib64/libbluetooth_qti.so`, JNI wrapper `/system/lib64/libbluetooth_qti_jni.so`. This audit did not access or change the device.

## Reference and scope

Closest inspected public QTI reference: `LineageOS/android_vendor_qcom_opensource_system_bt`, branch `lineage-19.1`, commit `905972f25e36a52f844a2cdcb9ccd3f946b8bd43`. This is a source analogue, NOT established as the exact Xiaomi binary's source. All patch offsets, machine instructions, and sizes must be derived from the actual binary.

Whole source archive was searched for `HID_DEV_MTU_SIZE`, `BTA_HD_REPORT_LEN`, `tBTA_HD_SEND_REPORT`, `tBTA_HD_DATA`, and related allocation/buffer definitions. In this reference, `internal_include/bt_target.h:1338` defines `HID_DEV_MTU_SIZE 64` unless overridden by the device's build config.

Base URL: <https://github.com/LineageOS/android_vendor_qcom_opensource_system_bt/blob/905972f25e36a52f844a2cdcb9ccd3f946b8bd43/>

## Required semantic changes / binary checks

| Location | Reference behavior | Required audit |
| --- | --- | --- |
| `bta/hd/bta_hd_api.cc:175` `BTA_HdSendReport` | Rejects report `len > BTA_HD_REPORT_LEN` | Accept up to 512, preserve rejection above 512. Optimized compare may encode 65 rather than 64, depending on branch condition. |
| Same function, line 184 | `osi_malloc(sizeof(tBTA_HD_SEND_REPORT))` | Enlarge allocation consistently. Reference layout has data offset 14 and total size 78 (`0x4e`) at MTU 64, becoming 526 (`0x20e`) at MTU 512. Confirm actual layout and allocator constant. |
| Same function, line 191 | `memcpy(p_buf->data, p_report->p_data, p_report->len)` | Inspect FORTIFY: if emitted as `__memcpy_chk`, its destination bound could remain 64 even after changing the above instructions. Must update the bound to match the real allocation, or verify ordinary `memcpy` with no stale bound. Do not blindly replace all immediate 64 values. |
| Same function, line 179 | Error log prints max length | Change stale diagnostic 64 if present, for truthful diagnostics; not a functional requirement. |
| `stack/hid/hidd_conn.cc:352-355`, `hidd_l2cif_config_ind` | Sets `rem_mtu_size = min(peer mtu, HID_DEV_MTU_SIZE)`; absent MTU uses cap | Update comparison/cap to 512; preserve peer values below cap and absent-MTU path. Reference device send function does not read this field, but maintain consistent stack semantics. |
| Same file, `hidd_conn_reg`, line 760 | Initializes control-channel advertised MTU to 64 | Change to 512. |
| Same function, line 766 | Initializes interrupt-channel advertised MTU to 64 | Change to 512. Optimizer may reuse one immediate for both stores. |
| L2CAP registration | This old QTI reference uses `L2CA_Register` without an MTU argument | Actual newer/vendor binary might instead use an MTU-taking API; inspect both control and interrupt registration call arguments. |

## Layout and downstream path

`bta/hd/bta_hd_int.h:91-100`:

```c
#define BTA_HD_REPORT_LEN HID_DEV_MTU_SIZE
typedef struct {
  BT_HDR hdr;           // reference: 8 bytes, 2-byte alignment
  bool use_intr;        // +8
  uint8_t type;         // +9
  uint8_t id;           // +10
  uint16_t len;         // +12 (one padding byte at +11)
  uint8_t data[BTA_HD_REPORT_LEN]; // +14
} tBTA_HD_SEND_REPORT;
```

Because `data` is the last member, its growth does not move earlier fields. The union `tBTA_HD_DATA` includes this struct, but its registration member includes a >512-byte descriptor, and no `sizeof(tBTA_HD_DATA)` use was found in the source. Do not infer another union-allocation patch without finding one in the binary.

Call path in source: `btif_hd.cc` report creation -> `BTA_HdSendReport` allocation/copy -> `bta_sys_sendmsg` -> `bta_hd_send_report_act` (`bta/hd/bta_hd_act.cc:367-379`) -> `HID_DevSendReport` -> `hidd_conn_send_data` -> `L2CA_DataWrite`.

The consumer action forwards `len` and the pointer at +14. It does not copy the report into another 64-byte array in this source.

`hidd_conn_send_data` (`stack/hid/hidd_conn.cc:898-990`) allocates `HID_CONTROL_BUF_SIZE` or `HID_INTERRUPT_BUF_SIZE`. Both default to `BT_DEFAULT_BUFFER_SIZE`, which is 4112 (`4096+16`, `internal_include/bt_target.h:216`). It uses `BT_HDR` 8 bytes + `L2CAP_MIN_OFFSET` 13 bytes + HID header 1 byte + optional report ID 1 byte + payload. This easily accommodates a 512-byte payload. Confirm the Xiaomi binary's actual allocation value and any copy bound rather than assuming these defaults. The source function does not fragment large reports by `rem_mtu_size`.

HID report length and link MTU are not identical because of HID header/report-ID overhead; the 362-ish byte amiibo reports fit comfortably inside 512. The upstream macro change convention is still 512 for both report bound and link MTU. Do not describe this as a generic guarantee that every 512-byte report plus headers fits a peer MTU of exactly 512.

## Constraints for a safe version-specific module

- Identify every required site in the exact `libbluetooth_qti.so`; use stable input SHA-256 and original-byte checks. Reject any other firmware/library.
- A source macro change propagates through allocation, bounds and MTU negotiation. A machine-code patch must account for every corresponding emitted site, including inlining/duplication and FORTIFY constants.
- Leave unrelated HID Host, BLE GATT/ATT MTU, ACL buffer constants and controller properties alone.
- Unit/static checks can verify patch bytes and semantic control flow. Runtime acceptance of long reports and controller interoperability still require actual device/Switch validation; library loading alone cannot prove amiibo works.
- A rollback-capable systemless overlay is packaging, not evidence of functional correctness. No generic module was constructed during this audit.
