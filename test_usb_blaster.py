import unittest
from unittest.mock import Mock, patch

from usb_blaster import (
    BUFFER_BYTES,
    BUFFER_WORDS,
    DONE_BIT,
    MAX10_IR_BITS,
    MAX10_USER0_IR,
    MAX10_USER1_IR,
    NOP_FRAME,
    OVERRUN_BIT,
    SA_CMD_REG,
    SA_STATUS_REG,
    UsbBlaster,
    UsbBlasterError,
    VIRTUAL_IR_BUF_LOAD,
    VIRTUAL_IR_BUF_STORE,
    VIRTUAL_IR_REG,
    VIRTUAL_IR_SELECTOR,
    decode_response,
    encode_request,
)


class RegisterFrameTests(unittest.TestCase):
    def test_virtual_jtag_selects_instance_zero(self):
        self.assertEqual(VIRTUAL_IR_SELECTOR, 0b10000)

    def test_write_frame_fields(self):
        address = 0xA
        data = 0xDEADBEEF
        frame = encode_request(address, True, data)

        self.assertEqual(frame & 0xF, address)
        self.assertEqual((frame >> 4) & 1, 1)
        self.assertEqual((frame >> 5) & 0xFFFFFFFF, data)
        self.assertEqual(frame >> 37, 0)

    def test_read_response_data_field(self):
        data = 0x89ABCDEF
        frame = (data << 5) | 0x15

        self.assertEqual(decode_response(frame), data)

    def test_rejects_invalid_address_or_data(self):
        with self.assertRaises(ValueError):
            encode_request(16, False)
        with self.assertRaises(ValueError):
            encode_request(0, True, 1 << 32)


class ByteShiftPackingTests(unittest.TestCase):
    def setUp(self):
        self.blaster = object.__new__(UsbBlaster)
        self.blaster._packet_size = 64
        self.blaster._select_user_dr = Mock()

    def test_read_header_is_not_padded_when_command_fits(self):
        frame = encode_request(3, True, 0x12345678)

        output, response_length = self.blaster._build_user_dr_scan(frame, True)

        self.assertEqual(output.index(0xC4), 7)
        self.assertEqual(response_length, 12)

    def test_header_moves_to_next_packet_instead_of_straddling(self):
        frame = encode_request(7, False)
        for offset in range(128):
            scan, _ = self.blaster._build_user_dr_scan(frame, True, starting_offset=offset)
            header = offset + scan.index(0xC4)
            self.assertLessEqual(header % 64 + 5, 64, offset)
            self.assertEqual(header, offset + 7 if (offset + 7) % 64 <= 59 else
                             (offset + 7) // 64 * 64 + 64)


class BridgeHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.blaster = object.__new__(UsbBlaster)
        self.blaster._build_user_dr_scan = Mock(return_value=(bytearray(), 0))
        self.blaster._exchange = Mock(return_value=b"")
        self.blaster._decode_user_dr_response = Mock()
        self.blaster._scan_user_dr = Mock()

    def test_read_capture_uses_nop_frame(self):
        self.blaster._decode_user_dr_response.return_value = DONE_BIT | (0x1234 << 5)

        self.assertEqual(self.blaster.read_reg(9), 0x1234)
        capture_frame = self.blaster._build_user_dr_scan.call_args_list[1].args[0]
        self.assertEqual(capture_frame, NOP_FRAME)

    def test_read_retries_until_done(self):
        self.blaster._decode_user_dr_response.return_value = 0
        self.blaster._scan_user_dr.side_effect = [0, DONE_BIT | (0xBEEF << 5)]

        self.assertEqual(self.blaster.read_reg(2), 0xBEEF)
        self.assertEqual(self.blaster._scan_user_dr.call_count, 2)
        self.blaster._scan_user_dr.assert_called_with(NOP_FRAME, read_tdo=True)

    def test_read_raises_when_bridge_never_completes(self):
        self.blaster._decode_user_dr_response.return_value = 0
        self.blaster._scan_user_dr.return_value = 0

        with self.assertRaises(UsbBlasterError):
            self.blaster.read_reg(0)

    def test_read_raises_on_overrun(self):
        self.blaster._decode_user_dr_response.return_value = DONE_BIT | OVERRUN_BIT

        with self.assertRaises(UsbBlasterError):
            self.blaster.read_reg(0)

    def test_write_rejects_system_written_addresses(self):
        with self.assertRaises(ValueError):
            self.blaster.write_reg(8, 0)
        self.blaster._scan_user_dr.assert_not_called()


def clock_edges(output):
    """Replay a USB-Blaster byte stream as (tms, tdi) per rising TCK edge."""
    edges = []
    tck = tms = 0
    index = 0
    while index < len(output):
        value = output[index]
        index += 1
        if value & 0x80:
            count = value & 0x3F
            for byte in output[index : index + count]:
                edges.extend((tms, (byte >> bit) & 1) for bit in range(8))
            index += count
            continue
        if value & 0x01 and not tck:
            edges.append(((value >> 1) & 1, (value >> 4) & 1))
        tck = value & 0x01
        tms = (value >> 1) & 1
    return edges


class BufferScanTests(unittest.TestCase):
    def setUp(self):
        self.blaster = object.__new__(UsbBlaster)
        self.blaster._packet_size = 64

    def shifted_bits(self, output):
        edges = clock_edges(output)
        self.assertEqual([tms for tms, _ in edges[:3]], [1, 0, 0])
        self.assertEqual([tms for tms, _ in edges[-2:]], [1, 0])
        shift = edges[3:-2]
        self.assertEqual([tms for tms, _ in shift[:-1]], [0] * (len(shift) - 1))
        self.assertEqual(shift[-1][0], 1)
        return [tdi for _, tdi in shift]

    def test_store_scan_shifts_data_lsb_first(self):
        data = bytes(range(256)) * 4
        output, response_length = self.blaster._build_buffer_scan(data, False)

        bits = self.shifted_bits(output)
        self.assertEqual(response_length, 0)
        self.assertEqual(len(bits), len(data) * 8)
        self.assertEqual(
            bytes(
                sum(bit << i for i, bit in enumerate(bits[n : n + 8]))
                for n in range(0, len(bits), 8)
            ),
            data,
        )

    def test_buffer_byte_shift_headers_are_packet_aligned(self):
        output, _ = self.blaster._build_buffer_scan(bytes(BUFFER_BYTES), True)
        headers = [i for i, value in enumerate(output) if value & 0x80]

        self.assertEqual(len(headers), -(-(BUFFER_BYTES - 1) // 62))
        self.assertTrue(all(i % 64 == 0 for i in headers))

    def test_load_response_fills_whole_packets_and_decodes(self):
        output, response_length = self.blaster._build_buffer_scan(bytes(8), True)
        response = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77])
        response += bytes([1, 0, 1, 0, 0, 0, 0, 1]) + bytes(response_length - 15)

        self.assertEqual(len(self.shifted_bits(output)), 64)
        self.assertEqual(response_length % 62, 0)
        self.assertEqual(
            self.blaster._decode_buffer_response(response, 8),
            bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x85]),
        )

    def test_store_pads_partial_word_and_selects_store_ir(self):
        self.blaster._selected_virtual_ir = VIRTUAL_IR_BUF_STORE
        self.blaster._exchange = Mock()
        self.blaster.store_buf([1, 2, 3, 4, 5])

        bits = self.shifted_bits(self.blaster._exchange.call_args.args[0])
        self.assertEqual(len(bits), 64)

    def test_load_selects_load_ir_in_the_same_exchange_and_trims_length(self):
        self.blaster._selected_virtual_ir = VIRTUAL_IR_REG
        self.blaster.user1_ir = MAX10_USER1_IR
        self.blaster.user0_ir = MAX10_USER0_IR
        self.blaster.virtual_ir_selector = VIRTUAL_IR_SELECTOR
        self.blaster.ir_bits = MAX10_IR_BITS
        self.blaster._exchange_overlapped = Mock(
            side_effect=lambda output, length: bytes([0xAB]) * length
        )

        self.assertEqual(self.blaster.load_buf(5), [0xAB] * 5)
        self.blaster._exchange_overlapped.assert_called_once()
        self.assertEqual(self.blaster._selected_virtual_ir, VIRTUAL_IR_BUF_LOAD)

    def test_rejects_oversized_transfers(self):
        with self.assertRaises(ValueError):
            self.blaster.store_buf(bytes(BUFFER_BYTES + 1))
        with self.assertRaises(ValueError):
            self.blaster.load_buf(BUFFER_BYTES + 1)
        with self.assertRaises(ValueError):
            self.blaster.store_buf([256])


class VirtualIrSelectionTests(unittest.TestCase):
    def test_reselects_only_when_virtual_ir_changes(self):
        blaster = object.__new__(UsbBlaster)
        blaster.ir_bits = 10
        blaster.user0_ir = 0x00C
        blaster.user1_ir = 0x00E
        blaster.virtual_ir_selector = VIRTUAL_IR_SELECTOR
        blaster._selected_virtual_ir = None
        blaster._exchange = Mock()

        for virtual_ir in (VIRTUAL_IR_REG, VIRTUAL_IR_REG, VIRTUAL_IR_BUF_LOAD,
                           VIRTUAL_IR_BUF_LOAD, VIRTUAL_IR_REG):
            blaster._select_user_dr(virtual_ir)
        self.assertEqual(blaster._exchange.call_count, 3)



class FakeEngine:
    """Register-level model of sys_access.sv on a sparse byte-addressed memory."""

    def __init__(self, busy_polls=0, batch_unfinished=False):
        self.batch_unfinished = batch_unfinished
        self.regs = [0] * 16
        self.buffer = bytearray(BUFFER_BYTES)
        self.memory = {}
        self.busy_polls = busy_polls
        self.commands = []

    def word(self, address):
        return int.from_bytes(
            bytes(self.memory.get(address + n, 0) for n in range(4)), "little"
        )

    def set_word(self, address, value, mask=0xF):
        for n in range(4):
            if mask >> n & 1:
                self.memory[address + n] = value >> (8 * n) & 0xFF

    def run(self, command):
        op, tag = command & 7, command >> 8 & 0xFF
        first_mask, last_mask = command >> 16 & 0xF, command >> 20 & 0xF
        address, data = self.regs[5], self.regs[6]
        self.commands.append((op, address, data, first_mask, last_mask))
        if op == 1:
            self.regs[14] = self.word(address)
        elif op == 2:
            self.set_word(address, data)
        elif op in (3, 4):
            base = address & ~3
            for k in range(data):
                mask = (first_mask if k == 0 else 0xF) & (
                    last_mask if k == data - 1 else 0xF
                )
                if op == 3:
                    value = int.from_bytes(self.buffer[4 * k : 4 * k + 4], "little")
                    self.set_word(base + 4 * k, value, mask)
                else:
                    self.buffer[4 * k : 4 * k + 4] = self.word(base + 4 * k).to_bytes(
                        4, "little"
                    )
        self.pending_status = (BUFFER_WORDS << 16) | (tag << 8)

    def transact(self, writes=(), reads=()):
        for address, data in writes:
            self.regs[address] = data
            if address == SA_CMD_REG:
                self.run(data)
                self.polls = self.busy_polls
        values = []
        for address in reads:
            if address == SA_STATUS_REG:
                if self.polls:
                    self.polls -= 1
                    values.append((BUFFER_WORDS << 16) | 1)
                    continue
                values.append(self.pending_status)
            else:
                values.append(self.regs[address])
        return values

    def store_buf(self, data):
        data = bytes(data)
        self.buffer[: len(data)] = data

    def load_buf(self, length):
        return list(self.buffer[:length])

    def _sa_batch(self, writes, reads, store=None, load_length=0):
        """Like UsbBlaster._sa_batch: load, store, then the register scans."""
        loaded = bytes(self.buffer[:load_length]) if load_length else None
        if store:
            self.store_buf(store)
        values = self.transact(writes, reads)
        if self.batch_unfinished:
            values = [None] * len(values)
        return values, loaded


class SysAccessTests(unittest.TestCase):
    def make(self, busy_polls=0, batch_unfinished=False):
        engine = FakeEngine(busy_polls, batch_unfinished)
        blaster = object.__new__(UsbBlaster)
        blaster._sa_tag = 7
        blaster._sa_batch = engine._sa_batch
        blaster.transact = engine.transact
        blaster.store_buf = engine.store_buf
        blaster.load_buf = engine.load_buf
        return blaster, engine

    def test_single_write_then_read(self):
        blaster, engine = self.make(busy_polls=2)
        blaster.sys_wr(0x1000, 0xDEADBEEF)
        self.assertEqual(engine.word(0x1000), 0xDEADBEEF)
        self.assertEqual(blaster.sys_rd(0x1000), 0xDEADBEEF)

    def test_store_load_all_offsets_and_lengths(self):
        blaster, engine = self.make()
        for offset in range(4):
            for length in (1, 2, 3, 4, 5, 7, 8, 9):
                address = 0x200 + offset
                engine.memory = {a: 0xEE for a in range(0x1F0, 0x220)}
                data = bytes(range(1, length + 1))
                blaster.data_store(address, data)
                self.assertEqual(blaster.data_load(address, length), list(data))
                untouched = [
                    a for a in range(0x1F0, 0x220)
                    if not address <= a < address + length
                ]
                self.assertTrue(all(engine.memory[a] == 0xEE for a in untouched))

    def test_long_transfer_splits_into_buffer_passes(self):
        blaster, engine = self.make()
        data = bytes(n * 7 & 0xFF for n in range(3 * BUFFER_BYTES + 10))
        blaster.data_store(0x3, data)
        blocks = [c for c in engine.commands if c[0] == 3]
        self.assertEqual([c[2] for c in blocks], [BUFFER_WORDS] * 3 + [4])
        self.assertEqual([c[1] for c in blocks], [n * BUFFER_BYTES for n in range(4)])
        self.assertEqual([c[3] for c in blocks], [0x8, 0xF, 0xF, 0xF])
        self.assertEqual([c[4] for c in blocks], [0xF, 0xF, 0xF, 0x1])
        self.assertEqual(blaster.data_load(0x3, len(data)), list(data))

    def test_block_load_waits_when_engine_busy(self):
        for busy_polls, batch_unfinished in ((2, False), (0, True)):
            blaster, engine = self.make(busy_polls, batch_unfinished)
            engine.memory = {a: a & 0xFF for a in range(0x400)}
            self.assertEqual(
                blaster.data_load(0x101, 600), [a & 0xFF for a in range(0x101, 0x359)]
            )

    def test_timeout_aborts(self):
        blaster, engine = self.make(busy_polls=10**9)
        with patch("usb_blaster.SA_TIMEOUT_S", 0.01):
            with self.assertRaises(UsbBlasterError):
                blaster.sys_rd(0)
        self.assertEqual(engine.commands[-1][0], 0)

    def test_rejects_missing_engine(self):
        blaster, engine = self.make()
        blaster._sa_batch = Mock(return_value=([0x12345678, 0], None))
        with self.assertRaises(UsbBlasterError):
            blaster.sys_rd(0)


class TransactTests(unittest.TestCase):
    def setUp(self):
        self.blaster = object.__new__(UsbBlaster)
        self.blaster._packet_size = 64
        self.blaster._selected_virtual_ir = VIRTUAL_IR_REG
        self.blaster._select_user_dr = Mock()

    def frames(self, *values):
        return b"".join(
            v.to_bytes(5, "little")[:4]
            + bytes((v >> bit) & 1 for bit in range(32, 40))
            for v in values
        )

    def test_batches_writes_and_reads_in_one_exchange(self):
        self.blaster._exchange = Mock(
            return_value=self.frames(DONE_BIT | (0x11 << 5), DONE_BIT | (0x22 << 5))
        )
        values = self.blaster.transact([(5, 1), (7, 2)], [15, 14])

        self.assertEqual(values, [0x11, 0x22])
        self.blaster._exchange.assert_called_once()
        self.assertEqual(self.blaster._exchange.call_args.args[1], 24)
        self.assertEqual(len(clock_edges(self.blaster._exchange.call_args.args[0])),
                         5 * (3 + 40 + 2))

    def test_falls_back_to_single_reads_when_not_done(self):
        self.blaster._exchange = Mock(
            return_value=self.frames(DONE_BIT | (0x11 << 5), 0, OVERRUN_BIT | DONE_BIT)
        )
        self.blaster.read_reg = Mock(return_value=0x33)
        self.assertEqual(self.blaster.transact([], [15, 14, 13]), [0x11, 0x33, 0x33])

    def test_rejects_write_to_system_register(self):
        with self.assertRaises(ValueError):
            self.blaster.transact([(8, 0)])



class SaBatchTests(TransactTests):
    """One exchange holding a buffer scan and the register scans."""

    def setUp(self):
        super().setUp()
        self.blaster.user1_ir = MAX10_USER1_IR
        self.blaster.user0_ir = MAX10_USER0_IR
        self.blaster.virtual_ir_selector = VIRTUAL_IR_SELECTOR
        self.blaster.ir_bits = MAX10_IR_BITS

    def assert_no_command_straddles(self, output):
        index = 0
        while index < len(output):
            value = output[index]
            if value & 0x80:
                count = value & 0x3F
                self.assertLessEqual(index % 64 + 1 + count, 64, index)
                index += count
            index += 1

    def test_store_then_command_in_one_exchange(self):
        self.blaster._exchange = Mock(return_value=self.frames(DONE_BIT | (0x77 << 5)))
        values, loaded = self.blaster._sa_batch([(7, 1)], [15], store=bytes(BUFFER_BYTES))

        self.assertEqual((values, loaded), ([0x77], None))
        output, length = self.blaster._exchange.call_args.args
        self.assertEqual(length, 12)
        self.assert_no_command_straddles(output)
        self.assertEqual(self.blaster._selected_virtual_ir, VIRTUAL_IR_REG)
        # 2 IR selects (store, back to register), the buffer, 3 register scans
        self.assertEqual(
            len(clock_edges(output)),
            2 * (2 * (4 + 10 + 2) + 3 + 5 + 2)
            + (3 + BUFFER_BYTES * 8 + 2)
            + 3 * (3 + 40 + 2),
        )

    def test_load_then_command_in_one_exchange(self):
        buffer_tdo = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77])
        buffer_tdo += bytes([1, 0, 1, 0, 0, 0, 0, 1])
        buffer_tdo += bytes(62 - len(buffer_tdo))   # the load pads to a whole packet

        def exchange(output, length):
            response = buffer_tdo + self.frames(DONE_BIT | (0x99 << 5))
            self.assertEqual(length, len(response))
            return response

        self.blaster._exchange_overlapped = Mock(side_effect=exchange)
        values, loaded = self.blaster._sa_batch([(7, 1)], [15], load_length=8)

        self.assertEqual(values, [0x99])
        self.assertEqual(loaded, bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x85]))
        output, _ = self.blaster._exchange_overlapped.call_args.args
        self.assert_no_command_straddles(output)
        self.assertEqual(self.blaster._selected_virtual_ir, VIRTUAL_IR_REG)

    def test_unfinished_capture_returns_none(self):
        self.blaster._exchange = Mock(return_value=self.frames(0, OVERRUN_BIT | DONE_BIT))
        values, _ = self.blaster._sa_batch([(7, 1)], [15, 14])
        self.assertEqual(values, [None, None])


if __name__ == "__main__":
    unittest.main()
