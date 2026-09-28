#!/usr/bin/env python3
"""Independent ELF layout, instruction and rejection checks for exact-build patch."""
import hashlib
import io
import json
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM
from elftools.elf.elffile import ELFFile
from patch_mtu import apply_patch, PATCHES, STOCK_SHA256

ROOT = Path(__file__).resolve().parent
original = (ROOT/'device/libbluetooth_qti.so').read_bytes()
patched = (ROOT/'patched/libbluetooth_qti.so').read_bytes()
before, after = ELFFile(io.BytesIO(original)), ELFFile(io.BytesIO(patched))
assert before.header == after.header
assert [s.header for s in before.iter_segments()] == [s.header for s in after.iter_segments()]
assert [s.header for s in before.iter_sections()] == [s.header for s in after.iter_sections()]
assert hashlib.sha256(original).hexdigest() == STOCK_SHA256
assert apply_patch(original)[0] == patched
decoder = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
decoder.detail = True
assembled = ELFFile(io.BytesIO((ROOT/'patch-instructions.o').read_bytes())).get_section_by_name('.text').data()
new_values = [513,512,526,512,512,512]
checked = []
for index,(offset,old,new,old_asm,new_asm,reason) in enumerate(PATCHES):
    seg = next(s for s in before.iter_segments() if s['p_type']=='PT_LOAD' and s['p_offset']<=offset<s['p_offset']+s['p_filesz'])
    va = seg['p_vaddr'] + offset - seg['p_offset']
    assert va == offset and seg['p_flags'] & 1
    a = next(decoder.disasm(original[offset:offset+4],va))
    b = next(decoder.disasm(patched[offset:offset+4],va))
    assert a.mnemonic == b.mnemonic
    assert a.operands[0].reg == b.operands[0].reg
    assert b.operands[-1].type == ARM64_OP_IMM and b.operands[-1].imm == new_values[index]
    assert bytes(b.bytes) == assembled[index*4:index*4+4]
    checked.append({'address':hex(va),'before':a.mnemonic+' '+a.op_str,'after':b.mnemonic+' '+b.op_str})

# Full-file fingerprint guard must catch changes both in and far outside patch sites.
rejected = []
for name,bad in [('already_patched',patched),('truncated',original[:-1]),
                 ('unrelated_build',original[:128]+bytes([original[128]^1])+original[129:]),
                 ('wrong_instruction',original[:PATCHES[0][0]]+bytes([original[PATCHES[0][0]]^1])+original[PATCHES[0][0]+1:])]:
    try:
        apply_patch(bad)
    except ValueError:
        rejected.append(name)
    else:
        raise AssertionError('Input guard unexpectedly accepted '+name)

result = {'status':'PASS','instruction_count':len(checked),'checks':checked,
          'elf_headers_segments_and_section_layout_unchanged':True,'matches_independent_clang_assembly':True,
          'input_guard_rejections':rejected,'original_sha256':hashlib.sha256(original).hexdigest(),
          'patched_sha256':hashlib.sha256(patched).hexdigest()}
(ROOT/'static-test-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
