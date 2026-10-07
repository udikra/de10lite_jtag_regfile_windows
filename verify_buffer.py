"""Check the 16 KiB JTAG buffer on hardware and time full-buffer transfers.

1. Store/load random data of several lengths, interleaved with register access.
2. Time repeated full-buffer stores and loads.

The buffer's system port belongs to sys_access.sv; verify_sys_access.py
checks it.
"""

from random import SystemRandom
from time import perf_counter

from regfile import BUFFER_BYTES, close, load_buf, read_reg, store_buf, write_reg


ITERATIONS = 100
LENGTHS = (4, 5, 64, 248, 251, 1000, BUFFER_BYTES)


def random_bytes(length: int) -> list:
    return list(SystemRandom().randbytes(length))


def check(name: str, got: list, wanted: list) -> None:
    if got != wanted:
        first = next(i for i, (g, w) in enumerate(zip(got, wanted)) if g != w)
        raise SystemExit(
            f"FAIL {name}: first mismatch at byte {first}: "
            f"expected 0x{wanted[first]:02X}, got 0x{got[first]:02X}"
        )


def main() -> None:
    try:
        full = random_bytes(BUFFER_BYTES)
        store_buf(full)
        for length in LENGTHS:
            data = random_bytes(length)
            store_buf(data)
            write_reg(0, length)
            stored = data + [0] * (-length % 4)
            full[: len(stored)] = stored
            check(f"load_buf({length})", load_buf(length), data)
            check(f"read_reg after {length}-byte store", [read_reg(0)], [length])
            check("full load", load_buf(), full)
        print(f"PASS: store/load of {', '.join(map(str, LENGTHS))} bytes")

        data = random_bytes(BUFFER_BYTES)
        started = perf_counter()
        for _ in range(ITERATIONS):
            store_buf(data)
        store_time = (perf_counter() - started) / ITERATIONS
        started = perf_counter()
        for _ in range(ITERATIONS):
            got = load_buf()
        load_time = (perf_counter() - started) / ITERATIONS
        check("timed load", got, data)
    finally:
        close()

    for name, seconds in (("store_buf", store_time), ("load_buf", load_time)):
        print(
            f"{name}: {seconds * 1000:.2f} ms per {BUFFER_BYTES} bytes "
            f"({BUFFER_BYTES / seconds / 1024:.0f} KiB/s, "
            f"{BUFFER_BYTES * 8 / seconds / 1e6:.2f} Mbit/s)"
        )


if __name__ == "__main__":
    main()
