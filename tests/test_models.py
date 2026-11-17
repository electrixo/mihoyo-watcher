"""核心类型的单元测试：只验证数据语义，不碰网络和时间。"""

import unittest

from app.models import CodeHit, Post, RunResult, Slot, State


class PostTests(unittest.TestCase):
    def test_blob_is_title_and_text(self):
        self.assertEqual(Post("s", "标题", "正文").blob, "标题\n正文")

    def test_defaults_are_isolated(self):
        a, b = Post("s", "a"), Post("s", "b")
        a.preset_codes.append(("CODE", "国服", "奖励"))
        self.assertEqual(b.preset_codes, [])
        self.assertEqual(a.post_id, "")
        self.assertFalse(a.announce)
        self.assertEqual(a.sign_slug, "")


class SmallTypesTests(unittest.TestCase):
    def test_code_hit_is_frozen(self):
        hit = CodeHit("ABC123XYZ", "国服", 7, ("形态符合",))
        with self.assertRaises(AttributeError):
            hit.code = "X"

    def test_slot_equality(self):
        self.assertEqual(Slot("2026-11-17", "09:00"), Slot("2026-11-17", "09:00"))

    def test_run_result_defaults(self):
        r = RunResult()
        self.assertEqual((r.codes, r.announces, r.reminders), (0, 0, 0))
        self.assertFalse(r.pushed)
        self.assertFalse(r.deferred)
        self.assertFalse(r.seeded)
        self.assertEqual((r.sources_total, r.sources_failed), (0, 0))

    def test_state_defaults(self):
        s = State()
        self.assertEqual(s.codes, {})
        self.assertEqual(s.announced, {})
        self.assertEqual(s.sign_reminders, {})
        self.assertEqual(s.push_slots, {})
        self.assertEqual(s.last_heartbeat, 0)


if __name__ == "__main__":
    unittest.main()
