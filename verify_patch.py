#!/usr/bin/env python3
"""Independent ELF layout, instruction and rejection checks for exact-build patch."""
import hashlib
import io
import json
import re
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM
from elftools.elf.elffile import ELFFile
from patch_mtu import apply_patch, PATCHES, STOCK_SHA256


def normalized_assembly(text):
    """Compare operands without depending on decimal/hex or spacing choices."""
    fields = text.strip().lower().split(None, 1)
    mnemonic = fields[0]
    operands = fields[1].replace('#', '') if len(fields) == 2 else ''
    operands = re.sub(
        r'(?<![\w.])-?(?:0x[0-9a-f]+|[0-9]+)(?![\w.])',
        lambda match: str(int(match.group(), 16 if '0x' in match.group() else 10)),
        operands,
    )
    return mnemonic, ''.join(operands.split())


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
assembly = ELFFile(io.BytesIO((ROOT/'patch-instructions.o').read_bytes()))
assembly_text_index = assembly.get_section_index('.text')
assert assembly_text_index is not None, 'Independent assembly is missing .text'
assembled = assembly.get_section(assembly_text_index).data()
assert len(assembled) == 4 * len(PATCHES), 'Independent assembly must contain one instruction per patch'
for section in assembly.iter_sections():
    if section['sh_type'] in ('SHT_REL', 'SHT_RELA') and section['sh_info'] == assembly_text_index:
        assert section.num_relocations() == 0, 'Independent assembly has unresolved .text relocations'
# Keep the original MTU operand checks even when other instruction edits are added.
mtu_values = {0x311660: 513, 0x31168c: 512, 0x3116a0: 526,
              0x556f84: 512, 0x557668: 512, 0x557674: 512}
patch_offsets = [entry[0] for entry in PATCHES]
assert len(set(patch_offsets)) == len(patch_offsets), 'Duplicate instruction patch'
assert set(mtu_values) <= set(patch_offsets), 'An original MTU patch is missing'
checked = []
for index,(offset,old,new,old_asm,new_asm,reason) in enumerate(PATCHES):
    assert offset >= 0 and offset % 4 == 0, f'Unaligned instruction at {offset:#x}'
    assert len(bytes.fromhex(old)) == len(bytes.fromhex(new)) == 4
    assert original[offset:offset+4] == bytes.fromhex(old), f'Original bytes differ at {offset:#x}'
    assert patched[offset:offset+4] == bytes.fromhex(new), f'Patched bytes differ at {offset:#x}'
    seg = next(s for s in before.iter_segments() if s['p_type']=='PT_LOAD' and s['p_offset']<=offset and offset+4<=s['p_offset']+s['p_filesz'])
    va = seg['p_vaddr'] + offset - seg['p_offset']
    assert va == offset and seg['p_flags'] & 1
    old_decoded = list(decoder.disasm(original[offset:offset+4],va))
    new_decoded = list(decoder.disasm(patched[offset:offset+4],va))
    assert len(old_decoded) == len(new_decoded) == 1, f'Invalid instruction at {va:#x}'
    a, b = old_decoded[0], new_decoded[0]
    assert a.size == b.size == 4
    assert normalized_assembly(a.mnemonic+' '+a.op_str) == normalized_assembly(old_asm), f'Original assembly differs at {va:#x}'
    assert normalized_assembly(b.mnemonic+' '+b.op_str) == normalized_assembly(new_asm), f'Patched assembly differs at {va:#x}'
    if offset in mtu_values:
        assert a.mnemonic == b.mnemonic
        assert a.operands[0].reg == b.operands[0].reg
        assert b.operands[-1].type == ARM64_OP_IMM and b.operands[-1].imm == mtu_values[offset]
    # The consolidated .text is not linked at the patch's VA. Decode each
    # independently assembled entry at its real VA to check branch targets.
    assembled_bytes = assembled[index*4:index*4+4]
    assembled_decoded = list(decoder.disasm(assembled_bytes, va))
    assert len(assembled_decoded) == 1 and assembled_decoded[0].size == 4
    c = assembled_decoded[0]
    assert normalized_assembly(c.mnemonic+' '+c.op_str) == normalized_assembly(new_asm), f'Independent assembly operands differ at {va:#x}'
    assert bytes(b.bytes) == assembled_bytes, f'Independent assembly differs at {va:#x}'
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
