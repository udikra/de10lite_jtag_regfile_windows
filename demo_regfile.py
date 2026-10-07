"""Demonstrate the package API: write JWSR registers 0..5, read SWJR 8..13.

With the placeholder system_stub.sv, SWJR register 8 + n holds ~JWSR register n
for n = 0..5 (JWSR 5..7 and SWJR 14..15 are the sys_access registers).
"""

from random import SystemRandom

from regfile import close, read_reg, write_reg


JWSR_COUNT = 8
STUB_COUNT = 6
MASK = 0xFFFFFFFF


def main() -> None:
    written = [SystemRandom().getrandbits(32) for _ in range(STUB_COUNT)]
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

    print("SWJR registers 8..13 hold the inverse of their JWSR register.")


if __name__ == "__main__":
    main()
