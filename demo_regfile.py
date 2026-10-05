"""Demonstrate the package API: write the 8 JWSR registers, read the 8 SWJR ones.

With the placeholder system_stub.sv, SWJR register 8 + n holds ~JWSR register n.
"""

from random import SystemRandom

from regfile import close, read_reg, write_reg


JWSR_COUNT = 8
MASK = 0xFFFFFFFF


def main() -> None:
    written = [SystemRandom().getrandbits(32) for _ in range(JWSR_COUNT)]
    try:
        for address, value in enumerate(written):
            write_reg(address, value)

        for address, value in enumerate(written):
            swjr_address = JWSR_COUNT + address
            result = read_reg(swjr_address)
            wanted = ~value & MASK
            status = "OK" if result == wanted else "MISMATCH"
            print(
                f"write_reg({address}, 0x{value:08X})  ->  "
                f"read_reg({swjr_address:2}) = 0x{result:08X}  [~ {status}]"
            )
            if result != wanted:
                raise SystemExit(f"register {swjr_address} verification failed")
    finally:
        close()

    print("All 8 SWJR registers hold the inverse of their JWSR register.")


if __name__ == "__main__":
    main()
