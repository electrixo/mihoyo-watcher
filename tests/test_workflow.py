"""workflow 契约测试：锁住 watch.yml 的关键结构。

2026-11-27 起改为「单条密打 cron + 分槽去重」：
  - on.schedule 只有一条 `7,37 * * * *`（每 30 分钟一个点）；
  - watch job 响应它；checkin job 每轮都跑（靠幂等，不再按 cron 过滤）；
  - 推送时机的正确性由 src/watch_mihoyo.py 的槽位逻辑保证（见 test_watch_mihoyo.py）。

零第三方依赖：不引入 PyYAML，全部用正则做结构断言。
"""

import re
import unittest
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "watch.yml"
)

# 唯一一条 cron：每 30 分钟一个触发点。GitHub 会丢弃大部分点，实际兑现 4~5 次/天。
# 传播到多条点后，由脚本按北京时间分槽（早/午/晚）去重推送。
DENSE_CRON = "7,37 * * * *"
EXPECTED_CRONS = {DENSE_CRON}

TEST_MODULES = (
    "tests.test_watch_mihoyo",
    "tests.test_mihoyo_codes",
    "tests.test_mihoyo_comments",
    "tests.test_workflow",
)


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = WORKFLOW_PATH.read_bytes().decode("utf-8")
        # 工作区检出可能是 CRLF（core.autocrlf=true），先归一化成 LF 再做结构断言
        cls.text = raw.replace("\r\n", "\n")
        cls.on = cls._slab(cls.text, 0, "on")
        cls.jobs = cls._slab(cls.text, 0, "jobs")
        cls.watch = cls._slab(cls.jobs, 2, "watch")
        cls.checkin = cls._slab(cls.jobs, 2, "checkin")

    @staticmethod
    def _slab(text, indent, key):
        """截取第 indent 列缩进的 ``key:`` 到下一个同级键之间的片段。"""
        pad = " " * indent
        head = re.compile(r"^" + pad + re.escape(key) + r":", re.M).search(text)
        if head is None:
            raise AssertionError(f"watch.yml 里找不到 `{key}:`（缩进 {indent}）")
        rest = text[head.end():]
        tail = re.compile(r"^" + pad + r"\S", re.M).search(rest)
        return rest[: tail.start()] if tail else rest

    # ---- on.schedule ----------------------------------------------------

    def test_schedule_declares_exactly_one_dense_cron(self):
        crons = re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', self.on, re.M)
        self.assertEqual(crons, [DENSE_CRON],
                         f"on.schedule 应只有一条密打 cron，实际 {crons}")

    def test_schedule_comparisons_have_no_typos(self):
        literals = re.findall(r"github\.event\.schedule\s*==\s*'([^']+)'", self.text)
        self.assertTrue(literals, "没有任何 github.event.schedule 比对，job 将永远不会被调度")
        unknown = sorted(set(literals) - EXPECTED_CRONS)
        self.assertEqual(unknown, [], f"这些 cron 比对串与 on.schedule 对不上：{unknown}")

    def test_watch_job_responds_to_the_dense_cron(self):
        self.assertIn(DENSE_CRON, self.watch, "密打 cron 没有触发 watch job")

    # ---- job 分工 -------------------------------------------------------

    def test_checkin_job_is_opt_in(self):
        self.assertIn(
            "vars.CHECKIN_ENABLED == 'true'", self.checkin,
            "签到 job 必须在 CHECKIN_ENABLED=true 时才运行，避免未配置时天天告警",
        )

    def test_checkin_runs_every_round_by_idempotency(self):
        # 密打后签到每轮兑现都跑，已签会返回 -5003 算成功；不再按 cron 过滤
        self.assertNotIn(
            "github.event.schedule", self.checkin,
            "签到 job 不应再按 cron 过滤——它靠幂等承受高频运行",
        )
        self.assertIn("米游社签到", self.checkin)

    def test_codes_only_branch_removed(self):
        # 不再有「某条 cron 只抓码」的分支，所有兑现都跑完整流程
        self.assertNotIn("--codes-only", self.watch,
                         "密打后不应再有 --codes-only 的调度分支")

    def test_schedule_is_logged_for_diagnosis(self):
        self.assertIn("记录触发来源", self.watch)
        self.assertIn("schedule=${{ github.event.schedule }}", self.watch)

    # ---- 手动触发入口 ---------------------------------------------------

    def test_manual_dispatch_keeps_its_two_booleans(self):
        for name in ("dry_run", "test_push"):
            with self.subTest(input=name):
                self.assertIn(f"{name}:", self.on)
        self.assertEqual(
            self.on.count("type: boolean"), 2,
            "workflow_dispatch 应当只有 dry_run / test_push 两个 boolean 输入",
        )

    # ---- 步骤契约 -------------------------------------------------------

    def test_scripts_run_from_src(self):
        self.assertIn("python src/watch_mihoyo.py", self.watch)
        self.assertIn("python src/checkin.py", self.checkin)

    def test_unit_test_step_runs_every_module(self):
        match = re.search(r"python -m unittest ([^\n]+)", self.text)
        self.assertIsNotNone(match, "workflow 里没有运行 unittest 的步骤")
        command = match.group(1)
        for module in TEST_MODULES:
            with self.subTest(module=module):
                self.assertIn(module, command, f"CI 没有跑 {module}")

    def test_pipeline_failures_are_not_swallowed(self):
        self.assertIn("set -o pipefail", self.watch)
        self.assertIn("set -o pipefail", self.checkin)

    def test_state_commit_is_path_limited(self):
        # 只提交状态文件：避免把工作区里的杂物（__pycache__、临时目录）带进仓库
        self.assertIn("git add -A -- state/seen_news.json", self.watch)
        self.assertIn("git add -A -- state/checkin_state.json", self.checkin)

    def test_jobs_are_serialized_and_always_run_state_step(self):
        self.assertIn("needs: watch", self.checkin)
        self.assertIn("always()", self.checkin)

    def test_concurrency_and_timezone_are_pinned(self):
        self.assertIn("group: mihoyo-watch", self.text)
        self.assertIn("TZ: Asia/Shanghai", self.text)
        self.assertIn("contents: write", self.text)


if __name__ == "__main__":
    unittest.main()
