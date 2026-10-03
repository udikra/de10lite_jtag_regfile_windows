"""Demonstrate the package API with random values in all 16 registers."""

from random import SystemRandom

from regfile import close, read_reg, write_reg


def main() -> None:
    expected = [SystemRandom().getrandbits(32) for _ in range(16)]
    try:
        for address, value in enumerate(expected):
            write_reg(address, value)
            print(f"write_reg({address}, 0x{value:08X})")

        for address, wanted in enumerate(expected):
            value = read_reg(address)
            status = "OK" if value == wanted else "MISMATCH"
            print(f"read_reg({address}) -> 0x{value:08X} [{status}]")
            if value != wanted:
                raise SystemExit(f"register {address} verification failed")
    finally:
        close()

    print("All 16 registers verified.")


if __name__ == "__main__":
    main()