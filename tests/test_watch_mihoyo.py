"""米游社福利监测（P2）的离线测试：解压、解析、过滤、去重、首轮 seed、dry-run、推送失败。"""

import gzip
import json
import os
import tempfile
import unittest
import urllib.parse
import zlib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import watch_mihoyo as w

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def deflate(raw):
    c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    return c.compress(raw) + c.flush()


def item(pid, typ=1, title="福利活动", summary="", game="原神"):
    return {
        "post_id": pid, "title": title, "summary": summary, "game": game,
        "type": w.TYPES[typ], "type_id": typ,
        "url": f"https://example.invalid/{pid}", "created_at": 0,
    }


def hit(code="STARRAIL9A8B7C"):
    return {
        "code": code, "region": "国服", "score": 7, "game": "崩坏：星穹铁道",
        "title": "兑换码帖", "url": "https://example.invalid/c",
        "source": "米游社搜索(兑换码)",
    }


class DecodeBodyTests(unittest.TestCase):
    def test_gzip(self):
        self.assertEqual(w.decode_body(gzip.compress("米游社".encode()), "gzip"), "米游社")

    def test_deflate(self):
        self.assertEqual(w.decode_body(deflate("米游社".encode()), "deflate"), "米游社")

    def test_plain(self):
        self.assertEqual(w.decode_body("米游社".encode(), ""), "米游社")


class NormalizeTests(unittest.TestCase):
    def test_fields_and_url(self):
        raw = fixture("news_type1.json")["data"]["list"][0]
        n = w.normalize(raw, "6", 1)
        self.assertEqual(n["post_id"], "1001")
        self.assertEqual(n["title"], "【福利】版本更新补给")
        self.assertEqual(n["summary"], "登录即可领取原石×100")
        self.assertEqual(n["game"], "崩坏：星穹铁道")
        self.assertEqual(n["type"], "公告")
        self.assertEqual(n["url"], "https://www.miyoushe.com/sr/article/1001")

    def test_missing_post_id_is_dropped(self):
        self.assertIsNone(w.normalize({"post": {"subject": "x"}}, "6", 1))


class WantedTests(unittest.TestCase):
    def setUp(self):
        self.items = {}
        for name, gid, typ in (
            ("news_type1.json", "6", 1),
            ("news_type2.json", "6", 2),
            ("news_type3.json", "6", 3),
        ):
            for raw in fixture(name)["data"]["list"]:
                item_ = w.normalize(raw, gid, typ)
                self.items[item_["post_id"]] = item_

    def test_welfare_notice_is_wanted(self):
        self.assertTrue(w.wanted(self.items["1001"]))

    def test_ban_notice_is_filtered(self):
        self.assertFalse(w.wanted(self.items["1002"]))

    def test_activity_always_wanted(self):
        self.assertTrue(w.wanted(self.items["2001"]))

    def test_plain_news_filtered(self):
        self.assertFalse(w.wanted(self.items["3001"]))

    def test_news_with_keyword_wanted(self):
        self.assertTrue(w.wanted(self.items["3002"]))


def router(url):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    typ = int(query["type"][0])
    return fixture({1: "news_type1.json", 2: "news_type2.json", 3: "news_type3.json"}[typ])


class CollectTests(unittest.TestCase):
    def test_nine_requests_and_dedup(self):
        with mock.patch.object(w, "http_json", side_effect=router), \
                mock.patch.object(w.time, "sleep", lambda *_: None):
            items, failures, total = w.collect(page_size=3)
        self.assertEqual((failures, total), (0, 9))
        self.assertEqual(len(items), 5)

    def test_one_type_failure_degrades(self):
        def failing(url):
            if "type=3" in url:
                raise RuntimeError("boom")
            return router(url)

        with mock.patch.object(w, "http_json", side_effect=failing), \
                mock.patch.object(w.time, "sleep", lambda *_: None):
            items, failures, total = w.collect(page_size=3)
        self.assertEqual((failures, total), (3, 9))
        self.assertEqual(len(items), 3)

    def test_nonzero_retcode_raises(self):
        with mock.patch.object(w, "http_json", return_value={"retcode": 1034, "message": "captcha"}):
            with self.assertRaises(RuntimeError):
                w.fetch_list("6", 1)


class SelectNewTests(unittest.TestCase):
    def test_unwanted_new_item_is_recorded_but_not_pushed(self):
        items = [item("a"), item("b", title="封禁公示")]
        new_items, push_items = w.select_new(items, {"posts": {}, "codes": {}})
        self.assertEqual([i["post_id"] for i in new_items], ["a", "b"])
        self.assertEqual([i["post_id"] for i in push_items], ["a"])

    def test_seen_items_are_skipped(self):
        new_items, push_items = w.select_new(
            [item("a")], {"posts": {"a": "2026-11-17"}, "codes": {}}
        )
        self.assertEqual((new_items, push_items), ([], []))

    def test_new_code_selection(self):
        seen = {"posts": {}, "codes": {"STARRAIL9A8B7C": {"first_seen": "2026-11-17"}}}
        self.assertEqual(w.select_new_codes([hit()], seen), [])
        self.assertEqual(len(w.select_new_codes([hit("GENSHIN9AB8CD7E")], seen)), 1)


class StateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "seen.json")
        patcher = mock.patch.object(w, "STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_state_is_first_run(self):
        seen, first = w.load_seen()
        self.assertEqual((seen, first), ({"posts": {}, "codes": {}}, True))

    def test_save_then_load(self):
        data = {"posts": {"a": "2026-11-17"}, "codes": {}}
        self.assertTrue(w.save_seen(data))
        self.assertFalse(w.save_seen(data))
        seen, first = w.load_seen()
        self.assertEqual(seen, data)
        self.assertFalse(first)

    def test_flat_legacy_format_is_migrated(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"a": "2026-11-17"}, f)
        seen, first = w.load_seen()
        self.assertEqual(seen, {"posts": {"a": "2026-11-17"}, "codes": {}})
        self.assertFalse(first)

    def test_prune_old(self):
        seen = {
            "posts": {"old": "2020-01-01", "new": "2026-11-17"},
            "codes": {"OLD1": {"first_seen": "2020-01-01"},
                      "NEW1": {"first_seen": "2026-11-17"}},
        }
        self.assertEqual(w.prune_seen(seen), 2)
        self.assertEqual(set(seen["posts"]), {"new"})
        self.assertEqual(set(seen["codes"]), {"NEW1"})


class MainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "seen.json")
        state_patch = mock.patch.object(w, "STATE_PATH", self.path)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.pushed = []
        push_patch = mock.patch.object(
            w, "push", lambda title, body: self.pushed.append((title, body)) or True
        )
        push_patch.start()
        self.addCleanup(push_patch.stop)
        self.out = StringIO()
        self.addCleanup(self.out.close)

    def run_main(self, items, failures=0, total=9, argv=None, codes=None):
        codes = codes or ([], 0, 0)
        with mock.patch.object(w, "collect", return_value=(items, failures, total)), \
                mock.patch.object(w.mihoyo_codes, "collect_codes", return_value=codes), \
                redirect_stdout(self.out):
            return w.main(argv or [])

    def test_first_run_seeds_without_push(self):
        code = self.run_main([item("1001")])
        self.assertEqual(code, 0)
        self.assertEqual(self.pushed, [])
        self.assertIn("登记", self.out.getvalue())
        seen, first = w.load_seen()
        self.assertFalse(first)
        self.assertIn("1001", seen["posts"])

    def test_new_welfare_is_pushed(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        code = self.run_main([item("1001"), item("2001", title="签到活动开启")])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("签到活动开启", self.pushed[0][1])

    def test_dry_run_does_not_push_or_save(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        code = self.run_main([item("1001"), item("2001")], argv=["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pushed, [])
        seen, _ = w.load_seen()
        self.assertNotIn("2001", seen["posts"])

    def test_all_requests_failed_returns_1(self):
        self.run_main([item("1001")])
        code = self.run_main([], failures=9, total=9)
        self.assertEqual(code, 1)

    def test_push_failure_keeps_state(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        with mock.patch.object(w, "push", lambda title, body: False):
            code = self.run_main([item("1001"), item("2001")])
        self.assertEqual(code, 1)
        seen, _ = w.load_seen()
        self.assertNotIn("2001", seen["posts"])

    def test_codes_are_in_digest_and_recorded(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        code = self.run_main([item("1001")], codes=([hit()], 0, 5))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pushed), 1)
        body = self.pushed[0][1]
        self.assertIn("STARRAIL9A8B7C", body)
        self.assertIn("兑换码", body)
        seen, _ = w.load_seen()
        self.assertIn("STARRAIL9A8B7C", seen["codes"])

    def test_same_code_not_pushed_twice(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        self.run_main([item("1001")], codes=([hit()], 0, 5))
        self.out = StringIO()
        code = self.run_main([item("1001")], codes=([hit()], 0, 5))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pushed), 1)

    def test_codes_only_skips_news_and_pushes_codes(self):
        self.run_main([item("1001")])  # 建立 seen
        self.out = StringIO()
        with mock.patch.object(w, "collect", side_effect=AssertionError("资讯不应被抓取")), \
                mock.patch.object(w.mihoyo_codes, "collect_codes", return_value=([hit()], 0, 5)), \
                redirect_stdout(self.out):
            code = w.main(["--codes-only"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("STARRAIL9A8B7C", self.pushed[0][1])
        self.assertIn("兑换码", self.pushed[0][0])

    def test_codes_only_silent_without_hits(self):
        self.run_main([item("1001")])
        self.out = StringIO()
        with mock.patch.object(w, "collect", side_effect=AssertionError("资讯不应被抓取")), \
                mock.patch.object(w.mihoyo_codes, "collect_codes", return_value=([], 0, 5)), \
                redirect_stdout(self.out):
            code = w.main(["--codes-only"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pushed, [])
        self.assertIn("没有新的兑换码", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
