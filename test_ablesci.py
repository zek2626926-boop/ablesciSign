import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).with_name('ablesci.py')
with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'exists', return_value=False):
    spec = importlib.util.spec_from_file_location('ablesci_under_test', SCRIPT)
    ablesci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ablesci)


class AbleSciTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.notifications = []
        notify = types.ModuleType('sendNotify')
        notify.send = lambda title, content: self.notifications.append(content)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.dict(sys.modules, {'sendNotify': notify}))
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(patch.object(ablesci.time, 'sleep'))
        self.sessions = self.stack.enter_context(patch.object(ablesci.requests, 'Session'))

    def session(self, login_code=0, sign_code=0, sign_message='签到成功'):
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        session.post.return_value.json.return_value = {'code': login_code, 'msg': '登录结果'}

        def get(url, **kwargs):
            response = Mock(status_code=200, text='<html></html>')
            if url.endswith('/site/login'):
                response.text = '<meta name="csrf-token" content="fixture-token">'
            elif url.endswith('/user/sign'):
                response.json.return_value = {'code': sign_code, 'msg': sign_message, 'data': {}}
            return response

        session.get.side_effect = get
        return session

    def test_csrf_accepts_legacy_input_and_current_meta(self):
        automator = ablesci.AbleSciAuto('fixture@example.com', 'fixture-password')
        for html, expected in [
            ('<input name="_csrf" value="legacy">', 'legacy'),
            ('<meta name="csrf-token" content="current">', 'current'),
            ('<input name="_csrf" value=""><meta name="csrf-token" content="current">', 'current'),
            ('<html></html>', ''),
        ]:
            with self.subTest(html=html):
                automator.session.get.return_value = Mock(status_code=200, text=html)
                self.assertEqual(automator.get_csrf_token(), expected)

    def test_main_reports_login_sign_failure_success_and_already_signed(self):
        for login_code, sign_code, message, expected in [
            (1, 0, '签到成功', 1),
            (0, 1, '签到拒绝', 1),
            (0, 0, '签到成功', 0),
            (0, 1, '您今天已于 07:40 签到', 0),
        ]:
            with self.subTest(login=login_code, sign=sign_code, message=message):
                self.sessions.return_value = self.session(login_code, sign_code, message)
                os.environ['ABLESCI_ACCOUNTS'] = 'fixture@example.com:fixture-password'
                before = len(self.notifications)
                self.assertEqual(ablesci.main(), expected)
                self.assertEqual(len(self.notifications), before + 1)

    def test_main_processes_remaining_accounts_after_failure(self):
        self.sessions.side_effect = [self.session(login_code=1), self.session()]
        os.environ['ABLESCI_ACCOUNTS'] = 'first@example.com:first-secret\nsecond@example.com:second-secret'
        self.assertEqual(ablesci.main(), 1)
        self.assertIn('完成第 2/2 个账号处理', self.output.getvalue())
        self.assertIn('签到成功', self.notifications[0])

    def test_main_without_accounts_returns_failure(self):
        self.assertEqual(ablesci.main(), 1)
        self.assertIn('未找到有效的账号配置', self.notifications[0])

    def test_repeated_run_resets_success_and_preserves_log_return(self):
        self.sessions.return_value = self.session()
        automator = ablesci.AbleSciAuto('fixture@example.com', 'fixture-password')
        self.assertIs(getattr(automator, 'success', None), False)
        self.assertIn('签到成功', automator.run())
        self.assertIs(automator.success, True)
        automator.session.post.return_value.json.return_value = {'code': 1, 'msg': '登录失败'}
        self.assertIn('登录失败', automator.run())
        self.assertIs(automator.success, False)

    def test_account_parser_does_not_print_credentials(self):
        os.environ['ABLESCI_ACCOUNTS'] = 'hidden@example.com:hidden-secret\ninvalid-secret\n:empty-secret\nempty@example.com:'
        self.assertEqual(ablesci.get_accounts(), [('hidden@example.com', 'hidden-secret')])
        for secret in ['hidden@example.com', 'hidden-secret', 'invalid-secret', 'empty-secret', 'empty@example.com']:
            self.assertNotIn(secret, self.output.getvalue())

    def test_env_loading_does_not_print_account_values(self):
        for content in ['ABLESCI_ACCOUNTS=env@example.com:env-secret', 'env@example.com:env-secret']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / '.env').write_text(content, encoding='utf-8')
                with patch.object(ablesci, '__file__', str(Path(directory) / 'ablesci.py')):
                    os.environ.pop('ABLESCI_ACCOUNTS', None)
                    ablesci.load_env_file()
                    self.assertEqual(ablesci.get_accounts(), [('env@example.com', 'env-secret')])
                self.assertNotIn('env-secret', self.output.getvalue())
                self.assertNotIn('env@example.com', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
