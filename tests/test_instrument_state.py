"""計測器の本体: 状態に出す値の種類と信号源の出力（設計F §5.1・§5.2.1・設計L §1.1）。"""
import copy
import unittest

from tests.instrument_helpers import InstrumentCase


class ValueKindStateTest(InstrumentCase):
    def test_value_kind_from_frame(self):
        # GigaSt の掃引は実数（設計F §5.1）。値の無いトレースもプロファイルから実数
        self.connect()
        st = self.call("get_state")
        self.assertEqual([t["value_kind"] for t in st["traces"]], ["real"] * 4)
        st = self.single()
        self.assertEqual(st["traces"][0]["value_kind"], "real")

    def test_value_kind_falls_back_to_profile_then_frame_wins(self):
        # 値がまだ無いトレースはプロファイルの値の種類、値が来たらその掃引の値の種類
        self.inst.shutdown()
        self.profile = copy.deepcopy(self.profile)
        self.profile["value_kind"] = "complex"
        self.inst = self.make_instrument()
        st = self.call("get_state")
        self.assertEqual([t["value_kind"] for t in st["traces"]], ["complex"] * 4)
        self.connect()
        self.call("set_trace", number=2, action="fixed")  # 固定は掃引を取り込まないので値が無いまま
        st = self.single()
        self.assertEqual(st["traces"][0]["value_kind"], "real")  # 掃引から（シミュレータは実数）
        self.assertFalse(st["traces"][1]["has_values"])
        self.assertEqual(st["traces"][1]["value_kind"], "complex")  # 値が無いのでプロファイルから


class SourceStateTest(InstrumentCase):
    def run_and_wait(self):
        self.clear_events()
        self.call("run", mode="continuous")
        return self.wait_for(lambda k, p: k == "state" and p["run"]["sweep_count"] >= 1)

    def stop_and_wait(self):
        self.clear_events()
        self.call("stop")
        return self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped")

    def test_not_connected_is_none(self):
        self.call("set_mode", mode="sg", sg_hz=500_000_000)
        self.assertEqual(self.call("get_state")["source"], {"output": "none", "freq_hz": None})

    def test_sa_is_none_while_sweeping(self):
        self.connect()
        st = self.run_and_wait()
        self.assertEqual(st["source"], {"output": "none", "freq_hz": None})

    def test_sg_while_sweeping_then_stop(self):
        self.connect()
        self.call("set_mode", mode="sg", sg_hz=500_000_000)
        self.assertEqual(self.call("get_state")["source"]["output"], "none")  # 掃引していない
        st = self.run_and_wait()
        self.assertEqual(st["source"], {"output": "sg", "freq_hz": 500_000_000})
        st = self.stop_and_wait()
        self.assertEqual(st["source"], {"output": "none", "freq_hz": None})

    def test_tg_and_s11_while_sweeping(self):
        self.connect()
        for mode in ("tg", "s11"):
            self.call("set_mode", mode=mode)
            st = self.run_and_wait()
            self.assertEqual(st["source"], {"output": "tg", "freq_hz": None}, mode)
            self.stop_and_wait()

    def test_single_done_is_none(self):
        self.connect()
        self.call("set_mode", mode="tg")
        st = self.single()
        self.assertEqual(st["source"]["output"], "none")
        # 単発の途中は出している
        self.assertTrue(any(k == "state" and p["run"]["state"] == "single" and p["source"]["output"] == "tg"
                            for k, p in self.events))

    def test_device_without_tg_is_none(self):
        # 信号源を持たない機器（RTL-SDR のプロファイルは source.tg=false）では出していないと出す
        self.inst.shutdown()
        self.profile = copy.deepcopy(self.profile)
        self.profile["source"] = {"tg": False, "sg": None}
        self.inst = self.make_instrument()
        self.connect()
        self.call("set_mode", mode="tg")
        st = self.run_and_wait()
        self.assertEqual(st["source"]["output"], "none")


if __name__ == "__main__":
    unittest.main()
