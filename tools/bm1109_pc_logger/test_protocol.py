import unittest

from tools.bm1109_pc_logger.logger import check_crc, crc16_modbus, modbus_frame


class ModbusProtocolTests(unittest.TestCase):
    def test_modbus_crc_known_vector(self):
        # Modbus RTU request: slave 1, function 3, start 0, quantity 1.
        payload = bytes.fromhex("01 03 00 00 00 01")
        self.assertEqual(crc16_modbus(payload), 0x0A84)

    def test_frame_appends_crc_low_byte_first(self):
        payload = bytes.fromhex("01 03 00 00 00 01")
        self.assertEqual(modbus_frame(payload), bytes.fromhex("01 03 00 00 00 01 84 0A"))

    def test_crc_checker_accepts_valid_frame(self):
        check_crc(modbus_frame(bytes.fromhex("01 04 00 00 00 01")))

    def test_crc_checker_rejects_corrupted_frame(self):
        frame = bytearray(modbus_frame(bytes.fromhex("01 04 00 00 00 01")))
        frame[3] ^= 0x01
        with self.assertRaisesRegex(ValueError, "CRC mismatch"):
            check_crc(bytes(frame))


if __name__ == "__main__":
    unittest.main()
