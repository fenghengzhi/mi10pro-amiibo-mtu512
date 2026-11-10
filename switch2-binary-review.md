# Switch 2 compatibility changes for the exact cmi firmware

The v5 payload extends the existing six-instruction MTU patch with one Bond classification instruction, five incoming HID security-order instructions, and three native CoD encoder instructions. It changes 15 instructions / 38 bytes. The original SHA-256 remains `2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039`; combined payload SHA-256 is `d537a6ffcac736537a9a3defe2178dc1cd8d29bd588ff1a71e3fe648f4d93bbe`.

These changes retain the exact firmware/hash guards, ELF layout, independent tmpfs staging and read-only bind. They do not change authentication requirements, encryption checks, explicit user rejection, CFI targets, or HID virtual-cable host/state validation.

The Bond change is a native equivalent of Companion's persistence correction. Its generic hook scanner may continue to print “Bond target not found”; acceptance is determined by stored keys, BOND_BONDED and successful reconnect, not by that scanner message. Classic JustWorks requests on this exact firmware are promoted generally, not by a Nintendo device-name filter. The normal user-confirmation path is used.

The HID change enters the original pending-security path only for incoming PSM 0x0013 while a security operation is already busy. The original control callback executes before the native pending-request scheduler resubmits the interrupt request. One optional busy diagnostic log is omitted; no connection guard is removed.

## Verification and limits

Independent clang assembly and exact ELF/instruction checks pass. The combined payload passes the original 74 MTU behavior cases, 186 Bond cases, 19 HID-order cases and 83 CoD cases under ARM64 Unicorn, with explicitly modeled external calls. PAC instructions are skipped equally in both variants and shadow-stack instructions execute. These tests do not replace Android/Switch validation. The later exact-device loading, pairing, deferred HID ordering, bounded-session and saved-key reconnect results are recorded in [VALIDATION.zh-CN.md](VALIDATION.zh-CN.md) and [device-validation/switch2-v5/](device-validation/switch2-v5/).

Mode-Change hook recognition is not changed here. The earlier HCI trace showed a short connection followed by remote termination near power-mode transitions, but did not establish that transition as the cause. The static review therefore required session and reconnect tests; those tests were subsequently completed on the exact device, with their bounded results documented above. The reviewer notes below preserve the static-stage reasoning and acceptance criteria. They do not replace the later runtime evidence or establish unlimited stability. No game-level amiibo success is claimed.

## Bond analysis

```text
Exact-firmware Bond persistence patch review

Input SHA256: 2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039
Library: Mi10Pro cmi Android13 V816.0.9.0.TJACNXM libbluetooth_qti.so.
No device changes were made by this reviewer.

Proposed patch
File offset / ELF VA 0x3def9c (4059036):
  41 00 80 52  MOV W1,#2   ->   21 00 80 52  MOV W1,#1
Only one byte changes; instruction width and file layout unchanged.
This is the TEMPORARY branch of btif_dm_ssp_cfm_req_evt, inlined in
btif_dm_upstreams_evt at0x3dba20. The existing persistent branch0x3dd644
already sets W1=1. Both converge at0x3dd648.

Data-flow proof
-0x3dd654 stores W1 to pairing_cb.bond_type at0x68c5be.
-0x3dd658 passes same W1 to btm_set_bond_type_dev at0x4f04b0.
-The real setter stores argument at device-record+0x17c, preserving NONE=0
 when called from removal/disconnect elsewhere.
-Auth-complete key-type4 gate0x3dee0c tests pairing_cb.bond_type==1 before
 choosing storage path0x3dd9a4; type2 follows Temporary key.Not storing.
-The patch therefore corrects both state copies required by upstream Companion.

Security/interaction behavior
Authentication requirements, encryption, peer address, and SSP result handling
are untouched. The preexisting temporary auto-accept branch requires bond_type2,
so the patched branch now takes the normal consent/UI path0x3dd6ac.
The user must accept the pairing prompt. Native btif_dm_ssp_reply0x3e6b24
continues to pass explicit accept/reject through to BTA_DmConfirm (and retains
LE confirm/grant and unsupported-variant behavior).
Scope: this promotes Classic SSP JustWorks non-bonding classifications in this
exact firmware generally, not only devices named Nintendo Switch. This matches
the broad TEMPORARY->PERSISTENT purpose of the upstream Companion Bond hook.
It does not alter NONE removal calls or LE classification code.

Verification
The executable test_bond_binary.py emulates native ARM64 classification code,
its actual BTM setter, and native key persistence gate. It covers all6x6 local /
remote authreq values with JustWorks0/1 for stock and patched images, CoD keyboard
exemptions, failed CoD lookup, prior pairing state, missing BTM records, direct
setter values0/1/2, keytype8 behavior, native user accept/reject in Classic and LE,
and unsupported SSP reply variant. External property/log/list helpers are modeled;
PAC instructions are skipped identically; shadow-stack accesses execute.
See bond-test-results.json for exact case count and PASS result.

Runtime acceptance criteria defined during static review
- Native log says Storing link key with bond_type1 after successful SSP.
- Android reaches BOND_BONDED.
- A subsequent reconnect can reply with its existing Link Key.
Companion's generic scanner may still log Bond target not found. The fix is in
its equivalent exact-firmware classification site, not a new dynamic hook.
Static tests are not a claim of physical Android/Switch stability.

```

## HID order analysis

```text
Mi 10 Pro QTI HID callback-order repair — static review

Target: libbluetooth_qti.so, exact SHA-256
2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039
ELF addresses equal file offsets at the five proposed sites.

Observed race
The Switch sends control PSM0x11 first and interrupt PSM0x13 immediately after.
QTI's control request starts an asynchronous security/name operation. The later
interrupt request enters btm_sec_l2cap_access_req while another callback is in
progress. Its current security requirements are already satisfied, so the native
busy fast path immediately calls the interrupt upper-layer callback. HIDD has
not yet received its control callback and correctly rejects the interrupt.

Why not simply alter HIDD guards
hidd_l2cif_connect_ind at0x557074 validates allow_incoming, virtual-cable host,
control CID, state, and PSM. Letting an interrupt through state0 would create an
invalid connection and bypass ordering assumptions. No HIDD instruction changes
are proposed; its security and different-host guards remain intact.

Why raising the interrupt security level alone is insufficient
In btm_sec_l2cap_access_req at0x5017b4, the busy fast path checks current link
authentication/encryption flags. The recorded device sec_flags0xc0be already
satisfy authentication+encryption. Replacing interrupt no-security flags with
the control flags can still take the same immediate-success path while the
control callback remains pending.

Proposed scoped native deferral
Replace five instructions at0x501d00..0x501d10 (see proposed-patches.json).
This location is reached only when p_dev_rec->p_callback is nonnull OR global
pairing_state is non-idle. The patch checks saved is_originator and PSM:
  incoming && PSM ==0x0013: branch to native defer path0x502210
  otherwise: continue unchanged native busy security logic at0x501d2c
The defer path returns BTM_CMD_STARTED=1 and sets btm_cb.sec_req_pending.
All service/security requirements, successful callback arguments, authentication
checks, HIDD state checks, different-host checks, and CFI call targets remain.
One optional debug log, security_flags/sec_flags in the busy branch, is omitted
for all requests reaching this block. Earlier busy logging remains intact.
No new stack frame, allocation, callback signature, code cave, or thread is added.

Native resubmission proof
- New branch0x502210 writes byte0x7d0c81=1 at0x50221c and returns1.
- btm_sec_dev_rec_cback_event0x503784 clears the current callback pointer at
 0x5037e8, invokes the existing callback at0x503848, then calls
 btm_sec_check_pending_reqs at0x503860.
- btm_sec_check_pending_reqs0x4fdb68 waits for pairing_state=0. It reads the
 pending byte at0x4fdbac, clears it at0x4fdbbc, and invokes
 l2cu_resubmit_pending_sec_req(NULL) at0x4fdbc0.
- l2cu_resubmit_pending_sec_req0x573e1c iterates existing link/channel control
 blocks and invokes the native L2CAP state machine with SEC_RE_SEND_CMD.
- Other completion paths call the same scheduler: remote-name completion,
 encryption change, pairing-state transition, and security timeout cleanup.
Thus the original control callback runs before the interrupt is resubmitted;
its original security logic runs again rather than being bypassed.

Entry safety
IDA xrefs show only0x501cd0 and fallthrough0x501cfc enter the replaced block at
0x501d00. No external branch enters its middle. The original continuation at
0x501d2c is a direct-flow block with no added indirect branches or CFI changes.

Validation
test_hid_order_binary.py executes actual stock/patched AArch64 routines under Unicorn.
19 cases passed, covering:
- original fast callback vs patched native deferral for incoming PSM0x13;
- unchanged outgoing interrupt and incoming control/other PSM behavior;
- retry after callback clears and after global pairing becomes idle;
- actual btm_sec_dev_rec_cback_event + btm_sec_check_pending_reqs execution:
 original callback occurs before resubmission and pending flag clears;
- native HIDD control -> asynchronous security -> interrupt normal sequence;
- different host, disabled incoming, absent CTRL, pending CTRL, duplicate/invalid
 state, invalid PSM, and failed security remain rejected.
External API behavior is modeled, PAC instructions are skipped uniformly, and
SCS instructions execute. This is not proof of Android or Switch stability.

Combined-image invocation
  .venv/bin/python test_hid_order_binary.py --stock STOCK.so --patched COMBINED.so --output-dir RESULT_DIR
The test accepts the combined MTU/Bond/HID image and verifies that its five HID
instruction changes are present before executing that image directly.

Primary source correspondence
https://android.googlesource.com/platform/system/bt/+/dc17388efd1c85b32c0ae52b2545f272fb6185d4/stack/hid/hidd_conn.cc
Native symbols/decompilation were also recovered from this exact firmware's
.gnu_debugdata and analyzed independently through IDA + Capstone.

```

## CoD startup race and native encoding

The v4 test did not establish Switch connectivity. A subsequent Bluetooth startup wrote phone CoD `0x5a020c` before Companion installed its hook. V5 forces the native Write_Class_Of_Device payload from the first call, preserving command headers, allocation, routing and the caller buffer. This applies while the module is enabled, matching the controller-class behavior intended by Companion.

```text
Exact-firmware native CoD serializer fix

Target: Mi10Pro cmi V816.0.9.0.TJACNXM, libbluetooth_qti.so
Stock SHA256: 2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039
No adb or phone modifications performed by this reviewer.

Reason
HCI evidence from the installed test build showed Write_Class_Of_Device=0x5a020c
before Companion's delayed hook installation. An intercept-only runtime hook
cannot correct a write that already happened. This static edit ensures that even
the first native write encodes the desired0x002508.

Native function
btsnd_hcic_write_dev_class at0x551280 (PACIASP); SCS hook point0x551284.
It allocates0x294 bytes, stores BT_HDR length6/offset0, HCI opcode0x0c24,
parameter length3, and reverses its input array into the three wire bytes.
Final dispatch is btu_hcif_send_cmd at0x50ac1c, controller id0.

Three same-width instruction edits
0x5512bc (5575356): 680a4039 LDRB W8,[X19,#2] ->08018052 MOV W8,#8
0x5512c4 (5575364): 68064039 LDRB W8,[X19,#1] ->a8048052 MOV W8,#0x25
0x5512d0 (5575376): 68024039 LDRB W8,[X19]    ->08008052 MOV W8,#0
The stores, buffer/header construction, allocation, route and function prologue
are unchanged. No caller input buffer is modified. No other HCI constructor is
edited. Fixed outgoing parameter bytes08 25 00 mean CoD0x002508.

Companion interaction -- explicitly checked
These replacements remove the three-LDRB pointer fingerprint. The current official
verify_hooks.py recognizes the SAME function0x551284 as RAW after patch, instead
of POINTER before patch. This is intentional and safe here because all pointer
dereferences were removed: the native constructor now ignores its argument.
Companion's cod_hook_impl calls its original trampoline with raw0x082500; native
execution with that exact value, null, and an unmapped high value all produces
the same correct HCI buffer without any argument read/write.
Prologue/trampoline-copy instructions remain unchanged. The wrapper has no CFI
instrumentation per the current Makefile. Physical runtime hooking still must be
checked after installation; the emulator does not execute the runtime trampoline.
The hook's OLD CoD log may print transformed low pointer bits under raw
classification; that old-value log should not be treated as over-air CoD evidence.
The actual HCI Write_Class_Of_Device bytes are the acceptance criterion.

Allocation failure
Native osi_malloc at0x5ab4b4 calls malloc and CHECKs a non-null result. This native
constructor has no separate nullable-allocation recovery path. The patch leaves
allocator and allocation size unchanged. Tests model a nonreturning allocation
failure and confirm no command is emitted and input is untouched, rather than
inventing null-return handling not present in the original.

Verification
83 native ARM64 tests PASS. 39 inputCoDs for each stock/patched variant include
0x5a020c,0x180508,0x52020c,0x002508,zero,all-bits and seeded random values. Every
byte of the poison-filled0x294 output allocation is checked: only the original
header fields and intended3 parameter bytes change. Caller buffer, callee-saved
X19, ordinary stack, shadow stack, allocation canaries and controller0 route are
checked. Fatal allocation modeled in both variants. Three extra patched calls
use raw/null/unmapped arguments to validate Companion reclassification safety.
Mirrored Companion detection in the test is also corroborated by executing the
actual official scripts/verify_hooks.py against original and patched libraries.
See cod-test-results.json and test_cod_binary.py.

Runtime acceptance criteria defined during static review
The FIRST HCI Write_Class_Of_Device after Bluetooth start must be0x002508 with
CommandComplete status0, including when no Companionhook has yet fired. Subsequent
writes must remain0x002508. Then retest discovery/pairing and prior Bond/HID fixes.
This static evidence alone does not establish physical Switch stability or
previous v4 success. Later v5 runtime results are documented in VALIDATION.zh-CN.md.

```
