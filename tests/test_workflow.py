"""workflow 契约测试：锁住 watch.yml 里被重复书写四次的 cron 字符串。

为什么需要它 —— 三条 cron 分别出现在：
  1. on.schedule（声明调度）
  2. watch   job 的 if（决定要不要跑福利日报）
  3. checkin job 的 if（决定要不要跑签到）
  4. 抓取步骤的 shell elif（决定要不要切到 --codes-only）

四处中漏改任何一处，job 都会**静默跳过**：不报错、不推送，GitHub 界面上
只显示该 job 被跳过。这类故障排查成本极高，因此在这里设护栏。

零第三方依赖：不引入 PyYAML，全部用正则做结构断言。
"""

import re
import unittest
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "watch.yml"
)

# 设计意图：当前只支持这三条调度
DAILY_DIGEST = "10 3 * * *"          # BJT 11:10  福利日报 + 签到主跑
CHECKIN_BACKUP = "10 6 * * *"        # BJT 14:10  签到兜底
FORWARD_LOOK = "7,37 14 * * 3,5,6"   # BJT 周三/五/六 22:07、22:37  只抓码
EXPECTED_CRONS = {DAILY_DIGEST, CHECKIN_BACKUP, FORWARD_LOOK}

TEST_MODULES = (
    "tests.test_watch_mihoyo",
    "tests.test_mihoyo_codes",
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

    def test_schedule_declares_exactly_the_three_expected_crons(self):
        crons = re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', self.on, re.M)
        self.assertEqual(len(crons), 3, f"on.schedule 应有且仅有 3 条 cron，实际 {crons}")
        self.assertEqual(set(crons), EXPECTED_CRONS)

    def test_schedule_comparisons_have_no_typos(self):
        literals = re.findall(r"github\.event\.schedule\s*==\s*'([^']+)'", self.text)
        self.assertTrue(literals, "没有任何 github.event.schedule 比对，job 将永远不会被调度")
        unknown = sorted(set(literals) - EXPECTED_CRONS)
        self.assertEqual(unknown, [], f"这些 cron 比对串与 on.schedule 对不上：{unknown}")

    def test_every_cron_is_consumed_by_some_job(self):
        for cron in sorted(EXPECTED_CRONS):
            with self.subTest(cron=cron):
                self.assertIn(
                    cron, self.watch + self.checkin,
                    f"cron `{cron}` 没有任何 job 会响应，等于白声明",
                )

    # ---- job 分工 -------------------------------------------------------

    def test_checkin_backup_must_not_trigger_the_digest_job(self):
        self.assertNotIn(
            CHECKIN_BACKUP, self.watch,
            "14:10 的签到兜底不应该触发 watch job（日报只在 11:10 与前瞻窗口跑）",
        )
        self.assertIn(CHECKIN_BACKUP, self.checkin)

    def test_watch_job_handles_digest_and_forward_look_only(self):
        self.assertIn(DAILY_DIGEST, self.watch)
        self.assertIn(FORWARD_LOOK, self.watch)
        self.assertNotIn(
            FORWARD_LOOK, self.checkin,
            "前瞻窗口只抓兑换码，不应该顺带跑签到",
        )

    def test_codes_only_elif_matches_the_forward_look_cron(self):
        match = re.search(
            r'elif\s*\[\s*"\$\{\{\s*github\.event\.schedule\s*\}\}"\s*=\s*"([^"]+)"\s*\]',
            self.watch,
        )
        self.assertIsNotNone(match, "抓取步骤里找不到区分 --codes-only 的 elif 分支")
        self.assertEqual(match.group(1), FORWARD_LOOK)
        branch = self.watch[match.end():]
        self.assertIn(
            "--codes-only", branch[:400],
            "elif 命中后必须切到 --codes-only，否则前瞻窗口会误推整份日报",
        )

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
        self.assertIn("git add -A -- seen_news.json", self.watch)
        self.assertIn("git add -A -- checkin_state.json", self.checkin)

    def test_jobs_are_serialized_and_always_run_state_step(self):
        self.assertIn("needs: watch", self.checkin)
        self.assertIn("always()", self.checkin)

    def test_concurrency_and_timezone_are_pinned(self):
        self.assertIn("group: mihoyo-watch", self.text)
        self.assertIn("TZ: Asia/Shanghai", self.text)
        self.assertIn("contents: write", self.text)


if __name__ == "__main__":
    unittest.main()
