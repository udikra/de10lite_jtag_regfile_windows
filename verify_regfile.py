"""Write random values to JWSR registers 0..5 and verify both bridge directions.

With the placeholder system_stub.sv, SWJR register 8 + n holds ~JWSR register n
for n = 0..5. JWSR 7 is left alone: writing it starts a sys_access command.
"""

from random import SystemRandom

from regfile import close, read_reg, write_reg


JWSR_COUNT = 8
STUB_COUNT = 6  # JWSR 0..5 mirrored by the stub into SWJR 8..13
MASK = 0xFFFFFFFF


def main() -> None:
    written = [SystemRandom().getrandbits(32) for _ in range(STUB_COUNT)]
    expected = {address: value for address, value in enumerate(written)}
    expected.update(
        {JWSR_COUNT + address: ~value & MASK for address, value in enumerate(written)}
    )
    try:
        for address, value in enumerate(written):
            write_reg(address, value)

        actual = {address: read_reg(address) for address in expected}
        mismatches = [
            (address, wanted, actual[address])
            for address, wanted in expected.items()
            if actual[address] != wanted
        ]
    finally:
        close()

    if mismatches:
        for address, wanted, got in mismatches:
            print(f"FAIL reg[{address}]: expected 0x{wanted:08X}, got 0x{got:08X}")
        raise SystemExit(1)

    print("PASS: JWSR 0..5 read back and SWJR 8..13 hold their inverses")


if __name__ == "__main__":
    main()
