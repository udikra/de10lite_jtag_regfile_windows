import unittest
from unittest.mock import Mock

from usb_blaster import (
    DONE_BIT,
    NOP_FRAME,
    OVERRUN_BIT,
    UsbBlaster,
    UsbBlasterError,
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


if __name__ == "__main__":
    unittest.main()