import copy
import unittest

from tools.bm1109_pc_logger.logger import check_crc, crc16_modbus, modbus_frame, validate_config


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

class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "poll_interval_s": 1.0,
            "pzem": {
                "enabled": True, "port": "COM5", "baudrate": 9600,
                "bytesize": 8, "parity": "N", "stopbits": 1,
                "timeout_s": 0.5, "slave_address": 248,
            },
            "ad800": {
                "enabled": True, "port": "COM6", "baudrate": 9600,
                "bytesize": 8, "parity": "E", "stopbits": 1,
                "timeout_s": 0.5, "slave_address": 1,
                "function": 3, "registers": [],
            },
        }

    def test_valid_config_allows_unmapped_ad800(self):
        validate_config(self.cfg)

    def test_unconfigured_ad800_profile_is_allowed(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"].update({
            "baudrate": None, "parity": None, "stopbits": None,
            "slave_address": None, "registers": [],
        })
        validate_config(cfg)

    def test_same_com_port_is_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"]["port"] = "com5"
        with self.assertRaisesRegex(ValueError, "same serial port"):
            validate_config(cfg)

    def test_ad800_write_function_is_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"]["function"] = 6
        with self.assertRaisesRegex(ValueError, "function must be 3 or 4"):
            validate_config(cfg)

    def test_nonfinite_poll_interval_is_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["poll_interval_s"] = float("nan")
        with self.assertRaisesRegex(ValueError, "poll_interval_s"):
            validate_config(cfg)

    def test_boolean_stopbits_is_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"]["stopbits"] = True
        with self.assertRaisesRegex(ValueError, "stopbits"):
            validate_config(cfg)

    def test_boolean_timeout_is_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"]["timeout_s"] = True
        with self.assertRaisesRegex(ValueError, "timeout_s"):
            validate_config(cfg)

    def test_duplicate_register_columns_are_rejected(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["ad800"]["registers"] = [
            {"name": "speed", "address": 10},
            {"name": "speed", "address": 11},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate or reserved"):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
