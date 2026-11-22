"""兑换码识别与搜索源的离线测试。"""

import unittest
from unittest import mock

import mihoyo_codes as c

NOW = 1700000000 + 3600


def payload(posts, retcode=0):
    return {"retcode": retcode, "message": "OK",
            "data": {"posts": [{"post": p} for p in posts]}}


def post(pid="p1", subject="星穹铁道兑换码", content="国服兑换码：STARRAIL9A8B7C",
         created=1700000000, game_id=6):
    return {"post_id": pid, "subject": subject, "content": content,
            "created_at": created, "game_id": game_id}


def codes(text, **kwargs):
    return [h.code for h in c.extract(text, **kwargs)]


class ExtractTests(unittest.TestCase):
    def test_high_confidence_code(self):
        hits = c.extract("国服兑换码：GENSHIN9AB8CD7E")
        self.assertEqual([h.code for h in hits], ["GENSHIN9AB8CD7E"])
        self.assertEqual(hits[0].region, "国服")
        self.assertGreaterEqual(hits[0].score, 6)

    def test_stopword_rejected(self):
        self.assertEqual(codes("兑换码 BILIBILI"), [])

    def test_enhancement_false_positive_is_fixed(self):
        # 纯字母且无已知前缀 → 丢弃（旧哨兵的已知误报）
        self.assertEqual(codes("礼包内含 Mystic Enhancement Ore"), [])

    def test_pure_letters_rejected(self):
        self.assertEqual(codes("兑换码 ABCDEFGH"), [])

    def test_prefix_without_context(self):
        self.assertIn("STARRAIL2026X", codes("STARRAIL2026X"))

    def test_region_attribution(self):
        text = "国服兑换码：ABC1234567\n国际服兑换码：XYZ7654321"
        hits = {h.code: h.region for h in c.extract(text)}
        self.assertEqual(hits, {"ABC1234567": "国服", "XYZ7654321": "国际服"})

    def test_detect_game(self):
        self.assertEqual(c.detect_game("星穹铁道 兑换码"), "崩坏：星穹铁道")
        self.assertEqual(c.detect_game("绝区零签到"), "绝区零")
        self.assertIsNone(c.detect_game("王者荣耀"))

    def test_marked_chinese_codes(self):
        text = ("国服兑换码（中文）： 坏灭肇始之时 天才少年米提亚登场 千星奇域一起拍拍拍"
                "   = UP角色&武器")
        hits = {h.code: h.region for h in c.extract_marked(text)}
        self.assertEqual(hits, {
            "坏灭肇始之时": "国服",
            "天才少年米提亚登场": "国服",
            "千星奇域一起拍拍拍": "国服",
        })

    def test_marked_international_codes(self):
        text = ("国服： 永远不会抛弃你 永远不会欺骗你 星神阿哈祝福你"
                "  国际服： nevergonnagive nevergonnamake aha")
        hits = {h.code: h.region for h in c.extract_marked(text)}
        self.assertEqual(hits.get("永远不会抛弃你"), "国服")
        self.assertEqual(hits.get("NEVERGONNAGIVE"), "国际服")
        self.assertEqual(hits.get("AHA"), "国际服")

    def test_marked_header_words_are_ignored(self):
        self.assertEqual(c.extract_marked("国服兑换码： 前瞻兑换码 有效期"), [])

    def test_bracketed_chinese_codes_with_sequence(self):
        # 「兑换码①：【...】」是省流/总结帖的常见写法，中文码形态无规律
        text = ("兑换码①：【永远不会抛弃你】 兑换码②：【永远不会欺骗你】 "
                "兑换码③：【星神阿哈祝福你】\n兑换码有效期至11月22日")
        hits = {h.code: h.region for h in c.extract_bracketed(text)}
        self.assertEqual(set(hits), {"永远不会抛弃你", "永远不会欺骗你", "星神阿哈祝福你"})
        self.assertTrue(all(r == "国服" for r in hits.values()))

    def test_bracketed_ascii_code(self):
        hits = {h.code for h in c.extract_bracketed("兑换码①：【SUNCHASER】")}
        self.assertEqual(hits, {"SUNCHASER"})

    def test_bracketed_ignores_noise_words(self):
        # 书名号内是说明性文字而非码时应跳过
        self.assertEqual(c.extract_bracketed("兑换码①：【有效期至11月22日】"), [])

    def test_find_codes_merges_bracketed(self):
        text = "省流版总结：兑换码①：【永远不会抛弃你】 ZXH68F7WYTN4"
        got = {h.code for h in c.find_codes(text)}
        self.assertIn("永远不会抛弃你", got)
        self.assertIn("ZXH68F7WYTN4", got)

    def test_summary_keywords_present(self):
        for kw in ("前瞻直播内容总结", "前瞻总结", "省流", "直播总结"):
            self.assertIn(kw, c.DEFAULT_KEYWORDS)
        # 单独的“总结”太宽泛，不应作为搜索词
        self.assertNotIn("总结", c.DEFAULT_KEYWORDS)

    def test_cn_list_comma_separated(self):
        # 「兑换码：」引导、顿号分隔、无任何括号标注的中文码（4.7 三种写法之一）
        text = "前瞻直播兑换码：永远不会抛弃你、永远不会欺骗你、星神阿哈祝福你"
        got = {h.code for h in c.extract_cn_list(text)}
        self.assertEqual(got, {"永远不会抛弃你", "永远不会欺骗你", "星神阿哈祝福你"})

    def test_cn_list_space_separated(self):
        text = "兑换码：坏灭肇始之时 天才少年米提亚登场 千星奇域一起拍拍拍"
        got = {h.code for h in c.extract_cn_list(text)}
        self.assertEqual(got, {"坏灭肇始之时", "天才少年米提亚登场", "千星奇域一起拍拍拍"})

    def test_cn_list_without_colon(self):
        # 「兑换码」后无冒号、后接中文码再接说明文字（4.7 总结帖真实写法）
        text = ("兑换码 永远不会抛弃你 永远不会欺骗你 星神阿哈祝福你 "
                "不方便直接复制的可以去评论区")
        got = {h.code for h in c.extract_cn_list(text)}
        self.assertEqual(got, {"永远不会抛弃你", "永远不会欺骗你", "星神阿哈祝福你"})

    def test_cn_list_ignores_instructional_text(self):
        # 引导词后是说明文字而非码时必须跳过，避免误报
        for text in ("兑换码：请在游戏内设置中兑换，注意有效期",
                     "兑换码：关注官方账号获取奖励",
                     "本活动兑换码有效期至11月22日"):
            self.assertEqual(c.extract_cn_list(text), [], text)

    def test_all_three_cn_formats_and_ascii(self):
        # 中英文都能抓：三种中文写法 + 英文码，统一从 find_codes 出口验证
        cn_cases = [
            "兑换码①：【永远不会抛弃你】 兑换码②：【永远不会欺骗你】",
            "国服兑换码（中文）： 永远不会抛弃你 星神阿哈祝福你",
            "前瞻直播兑换码：永远不会抛弃你、永远不会欺骗你、星神阿哈祝福你",
        ]
        for text in cn_cases:
            got = {h.code for h in c.find_codes(text)}
            self.assertIn("永远不会抛弃你", got, text)
        ascii_text = "省流总结：兑换码 ZXH68F7WYTN4 WANZHOU1202"
        ascii_got = {h.code for h in c.find_codes(ascii_text)}
        self.assertIn("ZXH68F7WYTN4", ascii_got)
        self.assertIn("WANZHOU1202", ascii_got)

    def test_ascii_code_without_context_not_false_positive(self):
        # 无兑换码语境的裸大写串不抓（评分制防误报，避免误判随机串）
        self.assertNotIn("ZXH68F7WYTN4",
                         {h.code for h in c.find_codes("省流总结：ZXH68F7WYTN4")})


class CollectCodesTests(unittest.TestCase):
    def run_collect(self, response, keywords=("兑换码",), **kwargs):
        if callable(response):
            http_mock = mock.patch.object(c, "http_json", side_effect=response)
        else:
            http_mock = mock.patch.object(c, "http_json", return_value=response)
        with http_mock as http, \
                mock.patch.object(c.time, "sleep", lambda *_: None):
            hits, failures, total = c.collect_codes(
                now=NOW, keywords=keywords, **kwargs
            )
        return hits, failures, total, http

    def test_hit_and_freshness(self):
        fresh = post("p1", created=NOW - 3600)
        stale = post("p2", created=NOW - 31 * 86400)
        hits, failures, total, _ = self.run_collect(payload([fresh, stale]))
        self.assertEqual((failures, total), (0, 1))
        self.assertEqual([h["code"] for h in hits], ["STARRAIL9A8B7C"])
        self.assertEqual(hits[0]["game"], "崩坏：星穹铁道")

    def test_excluded_game_is_dropped(self):
        p = post("p1", subject="崩坏3 兑换码", content="兑换码 GENSHIN9AB8CD7E")
        hits, _, _, _ = self.run_collect(payload([p]))
        self.assertEqual(hits, [])

    def test_duplicate_code_reported_once(self):
        a = post("p1")
        b = post("p2", subject="另一个帖子")
        hits, _, _, _ = self.run_collect(payload([a, b]))
        self.assertEqual(len(hits), 1)

    def test_nonzero_retcode_counts_as_failure(self):
        hits, failures, total, _ = self.run_collect({"retcode": 1034, "message": "captcha"})
        self.assertEqual((hits, failures, total), ([], 1, 1))

    def test_chinese_codes_in_search_result(self):
        p = post("p1", subject="【原神7.2】前瞻兑换码",
                 content="国服兑换码（中文）： 坏灭肇始之时 天才少年米提亚登场",
                 created=NOW - 3600, game_id=2)
        hits, _, _, _ = self.run_collect(payload([p]))
        self.assertEqual({h["code"] for h in hits},
                         {"坏灭肇始之时", "天才少年米提亚登场"})
        self.assertTrue(all(h["region"] == "国服" for h in hits))

    def test_keyword_failure_degrades(self):
        def router(url):
            if "bad" in url:
                raise RuntimeError("boom")
            return payload([post("p1")])

        hits, failures, total, _ = self.run_collect(router, keywords=("bad", "good"))
        self.assertEqual((failures, total), (1, 2))
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
