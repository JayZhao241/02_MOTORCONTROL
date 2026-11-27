import unittest
from unittest.mock import Mock
from desktop.bridge_transport import BridgeLink, BridgeTransport, PersistentBridgeLink


class BridgeTests(unittest.TestCase):
    def test_hal_frame_error_is_not_mislabelled_as_noise(self):
        text=BridgeLink.failure_message({'result':6,'hal_error':4,'stop_failed_mask':3})
        self.assertIn('FE 帧错误',text)
        self.assertNotIn('NE 噪声',text)
        self.assertIn('不能确认',text)
        self.assertIn('复位',text)

    def test_transport_reports_motor_function_without_retrying(self):
        link=Mock()
        link.wait.side_effect=[{}, RuntimeError('FE 帧错误'), {}]
        transport=BridgeTransport('probe','elf',link_factory=lambda *a:link)
        with self.assertRaisesRegex(RuntimeError,'ID 2 / 命令 0x3A'):
            transport.write(bytes.fromhex('02 3A 6B'))
        self.assertEqual(link.publish.call_count,2)
        transport.close()
        self.assertEqual(link.publish.call_args.args,(3,))

    def test_completion_during_swd_read_does_not_lose_rx_length(self):
        link=object.__new__(BridgeLink)
        link.base,link.size=0x20000000,200
        link.layout={"request":(link.base,4),"rx_len":(link.base+12,4),"result":(link.base+16,4),
                     "tx":(link.base+24,20),"rx":(link.base+44,128)}
        ready=bytearray(200)
        ready[12:16]=(4).to_bytes(4,"little")
        ready[44:48]=bytes.fromhex("01 3A 03 6B")
        torn=bytearray(ready); torn[12:16]=bytes(4)
        # CPU completes after the host read rx_len, but before it read result.
        link.read_memory=Mock(side_effect=[bytes(torn),bytes(ready[:24]),bytes(ready),bytes(ready[:24])])
        state=link.read()
        self.assertEqual(state["rx_len"],4)
        self.assertEqual(state["rx"][:4],bytes.fromhex("01 3A 03 6B"))

    def test_pending_mailbox_does_not_need_a_second_usb_read(self):
        link=object.__new__(BridgeLink); link.base,link.size=0x20000000,200
        link.layout={"request":(link.base,4),"rx_len":(link.base+12,4),"result":(link.base+16,4)}
        data=bytearray(200); data[16:20]=(1).to_bytes(4,"little")
        link.read_memory=Mock(return_value=bytes(data))
        self.assertEqual(link.read()["result"],1)
        link.read_memory.assert_called_once()


class PersistentBridgeTests(unittest.TestCase):
    def test_request_is_published_last_without_overwriting_feedback(self):
        link = object.__new__(PersistentBridgeLink)
        link.layout = {"request":(0x20000000,4), "arm":(0x20000004,4), "tx_len":(0x20000008,4),
                       "result":(0x20000010,4), "tx":(0x20000018,20)}
        link._memory = Mock()
        link.publish(2, bytes.fromhex("01 3A 6B"))
        writes = link._memory.write.call_args_list
        self.assertEqual(writes[-1].args,(0x20000000,b"\x02\x00\x00\x00"))
        self.assertEqual({c.args[0] for c in writes}, {v[0] for v in link.layout.values()})
        self.assertEqual(writes[0].args[1],bytes.fromhex("01 3A 6B"))

    def test_failed_firmware_check_closes_usb_without_acquiring_bus(self):
        link = Mock()
        link.verify_firmware.side_effect=RuntimeError("firmware mismatch")
        with self.assertRaisesRegex(RuntimeError,"mismatch"):
            BridgeTransport("probe","elf",link_factory=lambda *a:link)
        link.close_connection.assert_called_once()
        link.publish.assert_not_called()

    def test_live_memory_access_forbids_flash_writes(self):
        from desktop.stlink_memory import StLinkMemory
        memory = object.__new__(StLinkMemory)
        memory.memory = Mock()
        with self.assertRaises(ValueError):
            memory.write(0x08000000,b"data")
        memory.memory.write_memory_block8.assert_not_called()
