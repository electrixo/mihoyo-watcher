"""评论区取码模块离线测试：mock 网络，不依赖真实接口。"""

import unittest
from unittest import mock

import mihoyo_comments as cc


class Ds2Tests(unittest.TestCase):
    def test_ds2_format(self):
        ds = cc.gen_ds2("post_id=1&size=20")
        parts = ds.split(",")
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].isdigit())          # 时间戳
        self.assertTrue(100001 <= int(parts[1]) <= 200000)  # 随机数范围
        self.assertEqual(len(parts[2]), 32)          # md5 十六进制长度

    def test_ds2_query_sorted(self):
        # q 必须按 key 排序，排序与否签名不同
        with mock.patch.object(cc.random, "randint", return_value=123456), \
                mock.patch.object(cc.time, "time", return_value=1700000000):
            a = cc.gen_ds2("post_id=1&size=20")
            b = cc.gen_ds2("size=20&post_id=1")
        self.assertEqual(a, b)


class ExtractTextsTests(unittest.TestCase):
    def test_recursive_extract(self):
        data = {
            "comments": [
                {"content": "兑换码 ABCDEFGH", "sub_comments": [
                    {"content": "code XYZ12345"}]},
                {"content": "普通评论"},
            ]
        }
        texts = cc.extract_comment_texts(data)
        self.assertIn("兑换码 ABCDEFGH", texts)
        self.assertIn("code XYZ12345", texts)
        self.assertIn("普通评论", texts)

    def test_non_dict_returns_empty(self):
        self.assertEqual(cc.extract_comment_texts(None), [])
        self.assertEqual(cc.extract_comment_texts("x"), [])


class FetchFallbackTests(unittest.TestCase):
    def test_tries_all_paths_then_none(self):
        # 所有路径都失败（异常）时返回 None
        with mock.patch.object(cc, "http_json", side_effect=RuntimeError("404")) as hj, \
                mock.patch.object(cc.time, "sleep", lambda *_: None):
            result = cc.fetch_comment_list("123")
        self.assertIsNone(result)
        self.assertEqual(hj.call_count, len(cc.COMMENT_PATHS))

    def test_stops_at_first_success(self):
        # 第一条路径就成功时不再尝试后续
        def router(url, headers=None):
            if cc.COMMENT_PATHS[0] in url:
                return {"retcode": 0, "data": {"comments": []}}
            return {"retcode": -1, "message": "should not reach"}
        with mock.patch.object(cc, "http_json", side_effect=router) as hj:
            result = cc.fetch_comment_list("123")
        self.assertEqual(result, {"comments": []})
        self.assertEqual(hj.call_count, 1)


class CollectCodesTests(unittest.TestCase):
    def test_collect_from_comments(self):
        # mock 评论接口返回含两种码的评论
        replies = {"retcode": 0, "data": {"comments": [
            {"content": "兑换码 GENSHIN9AB8CD7E"},
            {"content": "国服兑换码（中文）： 坏灭肇始之时"},
            {"content": "普通闲聊内容"},
        ]}}
        with mock.patch.object(cc, "fetch_comment_list", return_value=replies["data"]), \
                mock.patch.object(cc.time, "sleep", lambda *_: None):
            hits = cc.collect_comment_codes(post_ids=["p1"])
        codes = {h["code"] for h in hits}
        self.assertIn("GENSHIN9AB8CD7E", codes)
        self.assertIn("坏灭肇始之时", codes)
        self.assertTrue(all(h["source"] == "米游社评论区" for h in hits))

    def test_degrades_after_consecutive_failures(self):
        # 连续失败达阈值即停止，不对每个帖子都发请求
        with mock.patch.object(cc, "fetch_comment_list", return_value=None) as fl, \
                mock.patch.object(cc, "time") as tm:
            tm.sleep = lambda *_: None
            hits = cc.collect_comment_codes(post_ids=["p1", "p2", "p3", "p4", "p5"])
        self.assertEqual(hits, [])
        self.assertEqual(fl.call_count, cc.MAX_CONSECUTIVE_FAIL)


if __name__ == "__main__":
    unittest.main()
