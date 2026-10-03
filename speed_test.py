"""Benchmark 1,000 random register write/read/verify pairs without progress output."""

from random import SystemRandom
from time import perf_counter

from regfile import close, read_reg, write_reg


ITERATIONS = 1000
REGISTER_COUNT = 16


def main() -> None:
    random_values = [SystemRandom().getrandbits(32) for _ in range(ITERATIONS)]
    verified = 0
    started = perf_counter()
    try:
        for index, expected in enumerate(random_values):
            address = index % REGISTER_COUNT
            write_reg(address, expected)
            actual = read_reg(address)
            if actual != expected:
                raise RuntimeError(
                    f"Register {address} mismatch at iteration {index}: "
                    f"expected 0x{expected:08X}, got 0x{actual:08X}"
                )
            verified += 1
    finally:
        close()

    elapsed = perf_counter() - started
    print(f"Verified {verified} random write/read pairs ({verified * 2} register operations).")
    print(f"Elapsed: {elapsed:.3f} s")
    print(f"Average per pair: {elapsed * 1000 / verified:.3f} ms")
    print(f"Throughput: {verified / elapsed:.1f} pairs/s")


if __name__ == "__main__":
    main()