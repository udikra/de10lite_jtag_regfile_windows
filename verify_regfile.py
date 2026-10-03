"""Write random values to all 16 registers and verify every readback."""

from random import SystemRandom

from regfile import close, read_reg, write_reg


def main() -> None:
    expected = [SystemRandom().getrandbits(32) for _ in range(16)]
    try:
        for address, value in enumerate(expected):
            write_reg(address, value)

        actual = [read_reg(address) for address in range(16)]
        mismatches = [
            (address, expected[address], actual[address])
            for address in range(16)
            if actual[address] != expected[address]
        ]
    finally:
        close()

    if mismatches:
        for address, wanted, got in mismatches:
            print(f"FAIL reg[{address}]: expected 0x{wanted:08X}, got 0x{got:08X}")
        raise SystemExit(1)

    print("PASS: all 16 registers matched their random 32-bit values")


if __name__ == "__main__":
    main()