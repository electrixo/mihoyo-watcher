"""配置加载与校验的单元测试（离线，不读本机环境变量）。"""

import json
import logging
import tempfile
import unittest
from pathlib import Path

from app.config import DEFAULTS, ENV_MAP, Config, load
from app.errors import ConfigError


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class TempRoot:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        self._tmp.cleanup()


def write_config(root, data):
    (root / "config.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class LoadTests(unittest.TestCase):
    def test_missing_config_uses_defaults(self):
        with TempRoot() as root:
            cfg = load(root, env={})
        self.assertEqual(cfg.get("push_schedule.slots"), ["09:00", "22:00"])
        self.assertEqual(cfg.get("notify.channel"), "pushplus")
        cfg.validate()

    def test_deep_merge_and_list_replace(self):
        with TempRoot() as root:
            write_config(root, {"miyoushe": {"keywords": ["礼包码"], "page_size": 5}})
            cfg = load(root, env={})
        self.assertEqual(cfg.get("miyoushe.keywords"), ["礼包码"])
        self.assertEqual(cfg.get("miyoushe.page_size"), 5)
        self.assertEqual(cfg.get("miyoushe.fresh_hours"), DEFAULTS["miyoushe"]["fresh_hours"])

    def test_bom_is_tolerated(self):
        with TempRoot() as root:
            (root / "config.json").write_text('\ufeff{"exclude_games": []}', encoding="utf-8")
            cfg = load(root, env={})
        self.assertEqual(cfg.get("exclude_games"), [])

    def test_invalid_json_raises_config_error(self):
        with TempRoot() as root:
            (root / "config.json").write_text("{oops", encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load(root, env={})
        self.assertIn("config.json", str(ctx.exception))

    def test_non_object_root_raises(self):
        with TempRoot() as root:
            (root / "config.json").write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(root, env={})

    def test_env_overrides_dotted_path(self):
        with TempRoot() as root:
            cfg = load(root, env={"PUSHPLUS_TOKEN": "tk", "NOTIFY_CHANNEL": "bark"})
        self.assertEqual(cfg.get("notify.pushplus.token"), "tk")
        self.assertEqual(cfg.get("notify.channel"), "bark")

    def test_empty_env_value_ignored(self):
        with TempRoot() as root:
            cfg = load(root, env={"PUSHPLUS_TOKEN": ""})
        self.assertEqual(cfg.get("notify.pushplus.token"), "")

    def test_env_map_keys(self):
        self.assertEqual(
            set(ENV_MAP),
            {
                "PUSHPLUS_TOKEN", "SERVERCHAN_SENDKEY", "WECOM_WEBHOOK", "BARK_URL",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEIBO_COOKIE",
                "TAPTAP_COOKIE", "NOTIFY_CHANNEL",
            },
        )

    def test_state_path_relative_to_root(self):
        with TempRoot() as root:
            cfg = load(root, env={})
        self.assertEqual(cfg.state_path(), root.resolve() / "state/seen.json")

    def test_does_not_mutate_defaults(self):
        with TempRoot() as root:
            load(root, env={}).get("taptap.apps")["原神"] = 1
        self.assertEqual(DEFAULTS["taptap"]["apps"]["原神"], 168332)


class GetTests(unittest.TestCase):
    def test_get_dotted_and_default(self):
        cfg = Config({"a": {"b": 1}}, ".")
        self.assertEqual(cfg.get("a.b"), 1)
        self.assertEqual(cfg.get("a.c", "x"), "x")
        self.assertEqual(Config({"a": 3}, ".").get("a.b", "fallback"), "fallback")

    def test_enabled(self):
        self.assertTrue(Config({"s": {"enable": True}}, ".").enabled("s"))
        self.assertFalse(Config({}, ".").enabled("missing"))


class ValidateTests(unittest.TestCase):
    def base(self):
        return Config(json.loads(json.dumps(DEFAULTS)), ".")

    def assert_field_error(self, cfg, field):
        with self.assertRaises(ConfigError) as ctx:
            cfg.validate()
        self.assertIn(field, str(ctx.exception))

    def test_default_is_valid(self):
        self.base().validate()

    def test_slot_format(self):
        for bad in ("09:00am", "9", "24:00", "09:60", "", None):
            cfg = self.base()
            cfg.data["push_schedule"]["slots"] = [bad]
            with self.subTest(bad=bad):
                self.assert_field_error(cfg, "push_schedule.slots")

    def test_slots_must_ascend(self):
        cfg = self.base()
        cfg.data["push_schedule"]["slots"] = ["22:00", "09:00"]
        self.assert_field_error(cfg, "push_schedule.slots")

    def test_slots_must_be_unique(self):
        cfg = self.base()
        cfg.data["push_schedule"]["slots"] = ["09:00", "9:00"]
        self.assert_field_error(cfg, "push_schedule.slots")

    def test_empty_slots_when_enabled(self):
        cfg = self.base()
        cfg.data["push_schedule"]["slots"] = []
        self.assert_field_error(cfg, "push_schedule.slots")

    def test_disabled_schedule_may_have_no_slots(self):
        cfg = self.base()
        cfg.data["push_schedule"]["enable"] = False
        cfg.data["push_schedule"]["slots"] = []
        cfg.validate()

    def test_tz_offset_range(self):
        for bad in (15, -13, "abc", None):
            cfg = self.base()
            cfg.data["push_schedule"]["tz_offset"] = bad
            with self.subTest(bad=bad):
                self.assert_field_error(cfg, "push_schedule.tz_offset")

    def test_taptap_apps_shape(self):
        for bad in ({"原神": "abc"}, {"原神": True}, {"原神": 0}, ["168332"], None):
            cfg = self.base()
            cfg.data["taptap"]["apps"] = bad
            with self.subTest(bad=bad):
                self.assert_field_error(cfg, "taptap.apps")

    def test_taptap_apps_accepts_numeric_string(self):
        cfg = self.base()
        cfg.data["taptap"]["apps"] = {"原神": "168332"}
        cfg.validate()

    def test_extract_min_score_range(self):
        for bad in (0, 11, "x"):
            cfg = self.base()
            cfg.data["extract"]["min_score"] = bad
            with self.subTest(bad=bad):
                self.assert_field_error(cfg, "extract.min_score")

    def test_extract_length_order(self):
        cfg = self.base()
        cfg.data["extract"]["min_length"] = 40
        self.assert_field_error(cfg, "extract.min_length")

    def test_notify_channel_known(self):
        cfg = self.base()
        cfg.data["notify"]["channel"] = "wechat-bot"
        self.assert_field_error(cfg, "notify.channel")

    def test_notify_channel_none_is_valid(self):
        cfg = self.base()
        cfg.data["notify"]["channel"] = "none"
        cfg.validate()

    def test_first_run_value(self):
        cfg = self.base()
        cfg.data["first_run"] = "always"
        self.assert_field_error(cfg, "first_run")


if __name__ == "__main__":
    unittest.main()
