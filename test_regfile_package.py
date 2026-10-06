import unittest
from unittest.mock import Mock, patch

import regfile


class RegfilePackageTests(unittest.TestCase):
    def setUp(self):
        self.original_device = regfile._device
        regfile._device = None

    def tearDown(self):
        regfile._device = self.original_device

    def test_module_functions_reuse_one_connection(self):
        device = Mock()
        device.read_reg.return_value = 0x12345678
        with patch.object(regfile, "UsbBlaster", return_value=device) as constructor:
            regfile.write_reg(3, 0x12345678)
            value = regfile.read_reg(3)

        constructor.assert_called_once_with()
        device.write_reg.assert_called_once_with(3, 0x12345678)
        device.read_reg.assert_called_once_with(3)
        self.assertEqual(value, 0x12345678)

    def test_buffer_functions_forward_to_device(self):
        device = Mock()
        device.load_buf.return_value = [1, 2, 3, 4]
        with patch.object(regfile, "UsbBlaster", return_value=device):
            regfile.store_buf([1, 2, 3, 4])
            data = regfile.load_buf()

        device.store_buf.assert_called_once_with([1, 2, 3, 4])
        device.load_buf.assert_called_once_with(regfile.BUFFER_BYTES)
        self.assertEqual(data, [1, 2, 3, 4])

    def test_close_releases_and_clears_connection(self):
        device = Mock()
        regfile._device = device

        regfile.close()

        device.close.assert_called_once_with()
        self.assertIsNone(regfile._device)


if __name__ == "__main__":
    unittest.main()