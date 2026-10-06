import unittest
from unittest.mock import Mock

from usb_blaster import (
    BUFFER_BYTES,
    DONE_BIT,
    NOP_FRAME,
    OVERRUN_BIT,
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

    def test_read_header_is_packet_aligned_with_room_for_command(self):
        frame = encode_request(3, True, 0x12345678)

        output, response_length = self.blaster._build_user_dr_scan(frame, True)
        header = output.index(0xC4)

        self.assertEqual(header % 64, 0)
        self.assertEqual(response_length, 12)
        self.assertEqual(output[7:header], bytes([0x2C]) * (header - 7))

    def test_concatenated_scan_header_stays_packet_aligned(self):
        frame = encode_request(7, False)
        first_scan, first_response = self.blaster._build_user_dr_scan(frame, False)
        second_scan, second_response = self.blaster._build_user_dr_scan(
            frame, True, starting_offset=len(first_scan)
        )
        header = second_scan.index(0xC4)

        self.assertEqual(first_response, 0)
        self.assertEqual((len(first_scan) + header) % 64, 0)
        self.assertEqual(second_response, 12)


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

    def test_byte_shift_headers_are_packet_aligned(self):
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
        self.blaster._select_user_dr = Mock()
        self.blaster._exchange = Mock()
        self.blaster.store_buf([1, 2, 3, 4, 5])

        self.blaster._select_user_dr.assert_called_once_with(VIRTUAL_IR_BUF_STORE)
        bits = self.shifted_bits(self.blaster._exchange.call_args.args[0])
        self.assertEqual(len(bits), 64)

    def test_load_selects_load_ir_and_trims_length(self):
        self.blaster._select_user_dr = Mock()
        self.blaster._exchange_overlapped = Mock(
            side_effect=lambda output, length: bytes([0xAB]) * length
        )

        self.assertEqual(self.blaster.load_buf(5), [0xAB] * 5)
        self.blaster._select_user_dr.assert_called_once_with(VIRTUAL_IR_BUF_LOAD)

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


if __name__ == "__main__":
    unittest.main()