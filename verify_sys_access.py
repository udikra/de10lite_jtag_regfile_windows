"""Check system access (sys_access.sv) on hardware against a model of system_stub.sv.

The stub's system address space is a 64 KiB RAM aliased every 64 KiB, whose
sa_ready is delayed at random. The test keeps a byte-for-byte model of it:

1. Fill all 64 KiB with data_store and read it back with data_load
   (4 buffer passes each way).
2. Random single sys_wr / sys_rd, including unaligned addresses (the stub
   ignores sa_addr[1:0]).
3. Every start offset 0..3 with lengths 1..9, then random unaligned
   data_store / data_load spans, most up to 3 KiB and some up to 40 KiB
   (several buffer passes), some wrapping past
   0xFFFFFFFF; after each store the whole RAM is compared with the model, so
   a read-modify-write that clobbers a neighbouring byte is caught.
4. Timing of single accesses and 64 KiB transfers.
"""

from random import Random, SystemRandom
from time import perf_counter

from regfile import close, data_load, data_store, sys_rd, sys_wr


RAM_BYTES = 64 * 1024
SINGLE_ITERATIONS = 200
SPAN_ITERATIONS = 60
TIMED_ITERATIONS = 100

rng = Random(SystemRandom().getrandbits(64))
model = bytearray(RAM_BYTES)


def model_store(address: int, data: bytes) -> None:
    for n, value in enumerate(data):
        model[(address + n) % RAM_BYTES] = value


def model_load(address: int, length: int) -> list:
    return [model[(address + n) % RAM_BYTES] for n in range(length)]


def model_word(address: int) -> int:
    return int.from_bytes(bytes(model_load(address & ~3, 4)), "little")


def check(name: str, got: list, wanted: list) -> None:
    if got != wanted:
        if len(got) != len(wanted):
            raise SystemExit(f"FAIL {name}: got {len(got)} bytes, expected {len(wanted)}")
        first = next(i for i, (g, w) in enumerate(zip(got, wanted)) if g != w)
        raise SystemExit(
            f"FAIL {name}: first mismatch at byte {first}: "
            f"expected 0x{wanted[first]:02X}, got 0x{got[first]:02X}"
        )


def random_address() -> int:
    if rng.random() < 0.1:
        return (rng.randrange(-4096, 0)) & 0xFFFFFFFF   # wraps past 0xFFFFFFFF
    return rng.randrange(RAM_BYTES * 4)                # aliases of the RAM


def random_length() -> int:
    if rng.random() < 0.3:
        return rng.randrange(1, 40 * 1024)
    return rng.randrange(1, 3 * 1024)


def store_and_check(address: int, data: bytes) -> None:
    data_store(address, len(data), list(data))
    model_store(address, data)
    check(f"data_store({address:#x}, {len(data)})", data_load(0, RAM_BYTES), list(model))


def main() -> None:
    try:
        model[:] = rng.randbytes(RAM_BYTES)
        data_store(0, RAM_BYTES, list(model))
        loaded = []
        data_load(0, RAM_BYTES, loaded)
        check("full fill", loaded, list(model))
        print(f"PASS: {RAM_BYTES // 1024} KiB data_store / data_load")

        for _ in range(SINGLE_ITERATIONS):
            address = random_address()
            if rng.random() < 0.5:
                value = rng.getrandbits(32)
                sys_wr(address, value)
                model_store(address & ~3, value.to_bytes(4, "little"))
            else:
                value = sys_rd(address)
                if value != model_word(address):
                    raise SystemExit(
                        f"FAIL sys_rd({address:#x}): expected "
                        f"0x{model_word(address):08X}, got 0x{value:08X}"
                    )
        check("after single accesses", data_load(0, RAM_BYTES), list(model))
        print(f"PASS: {SINGLE_ITERATIONS} random sys_wr / sys_rd")

        for offset in range(4):
            for length in range(1, 10):
                address = 0x100 + 16 * length + offset
                store_and_check(address, rng.randbytes(length))
                check(
                    f"data_load({address:#x}, {length})",
                    data_load(address, length),
                    model_load(address, length),
                )
        print("PASS: offsets 0..3 x lengths 1..9")

        for _ in range(SPAN_ITERATIONS):
            address = random_address()
            length = random_length()
            store_and_check(address, rng.randbytes(length))
            address = random_address()
            length = random_length()
            check(
                f"data_load({address:#x}, {length})",
                data_load(address, length),
                model_load(address, length),
            )
        print(f"PASS: {SPAN_ITERATIONS} random unaligned store / load spans")

        started = perf_counter()
        for n in range(TIMED_ITERATIONS):
            sys_wr(4 * n, n)
        wr_time = (perf_counter() - started) / TIMED_ITERATIONS
        started = perf_counter()
        for n in range(TIMED_ITERATIONS):
            if sys_rd(4 * n) != n:
                raise SystemExit(f"FAIL timed sys_rd({4 * n:#x})")
        rd_time = (perf_counter() - started) / TIMED_ITERATIONS

        data = rng.randbytes(RAM_BYTES)
        started = perf_counter()
        data_store(0, RAM_BYTES, data)
        store_time = perf_counter() - started
        started = perf_counter()
        got = data_load(0, RAM_BYTES)
        load_time = perf_counter() - started
        check("timed transfer", got, list(data))
    finally:
        close()

    print(f"sys_wr: {wr_time * 1000:.2f} ms, sys_rd: {rd_time * 1000:.2f} ms")
    for name, seconds in (("data_store", store_time), ("data_load", load_time)):
        print(
            f"{name}: {seconds * 1000:.1f} ms per {RAM_BYTES // 1024} KiB "
            f"({RAM_BYTES / seconds / 1024:.0f} KiB/s)"
        )


if __name__ == "__main__":
    main()
