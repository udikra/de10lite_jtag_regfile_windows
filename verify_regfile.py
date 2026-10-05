"""Write random values to the 8 JWSR registers and verify both bridge directions.

With the placeholder system_stub.sv, SWJR register 8 + n holds ~JWSR register n.
"""

from random import SystemRandom

from regfile import close, read_reg, write_reg


JWSR_COUNT = 8
MASK = 0xFFFFFFFF


def main() -> None:
    written = [SystemRandom().getrandbits(32) for _ in range(JWSR_COUNT)]
    expected = written + [~value & MASK for value in written]
    try:
        for address, value in enumerate(written):
            write_reg(address, value)

        actual = [read_reg(address) for address in range(len(expected))]
        mismatches = [
            (address, expected[address], actual[address])
            for address in range(len(expected))
            if actual[address] != expected[address]
        ]
    finally:
        close()

    if mismatches:
        for address, wanted, got in mismatches:
            print(f"FAIL reg[{address}]: expected 0x{wanted:08X}, got 0x{got:08X}")
        raise SystemExit(1)

    print("PASS: 8 JWSR registers read back and 8 SWJR registers hold their inverses")


if __name__ == "__main__":
    main()
