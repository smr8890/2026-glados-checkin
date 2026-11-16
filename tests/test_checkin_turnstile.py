"""签到流程接入 2captcha 的离线回归测试（不提交真实签到）。"""

import os
import unittest
from unittest import mock

import checkin

COOKIE = 'gld:sess=synthetic-private; gld:sess.sig=synthetic-signature'
NONCE = 'synthetic-nonce'
PROOF = '95d98116667d20076de5e31eeba57cb65dac0e9be21afb32263bac1178699533'
SITEKEY = '0x4AAAAAAA-synthetic-sitekey'
OK = {'code': 0, 'message': 'Checkin! Got 12 Points'}


def response(payload, status=200):
    return mock.Mock(status_code=status, **{'json.return_value': payload})


class TurnstileCheckinTests(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {'GLADOS_USER_AGENT': 'SyntheticBrowser/1.0'}, clear=True),
            mock.patch('requests.sessions.Session.request',
                       side_effect=AssertionError('Live HTTP disabled')),
            mock.patch('checkin.log'),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_solver_token_is_submitted_with_tsstate(self):
        with mock.patch('checkin.requests.post', return_value=response(OK)) as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 300000, 'turnstile': SITEKEY,
                })) as get, \
                mock.patch('checkin.turnstile_solver_enabled', return_value=True), \
                mock.patch('checkin.solve_turnstile',
                           return_value={'token': 'synthetic-token'}) as solve, \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 100, 102.7]):
            result = checkin.GLaDOS(COOKIE).checkin()

        self.assertTrue(checkin.is_normal_checkin_result(result))
        get.assert_called_once()
        solve.assert_called_once_with(
            sitekey=SITEKEY,
            page_url=checkin.DOMAINS[0] + checkin.CHECKIN_PAGE_PATH,
            action='checkin',
            cdata=NONCE,
            timeout=120.0,
        )
        self.assertEqual(post.call_args.kwargs['json'], {
            'token': 'glados.cloud',
            'nonce': NONCE,
            'proof': PROOF,
            'tsState': 'ok',
            'turnstile': 'synthetic-token',
        })

    def test_solver_failure_skips_post_and_reports_solver_label(self):
        with mock.patch('checkin.requests.post') as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 300000, 'turnstile': SITEKEY,
                })), \
                mock.patch('checkin.turnstile_solver_enabled', return_value=True), \
                mock.patch('checkin.solve_turnstile', return_value={
                    'request_failed': True,
                    'reason': 'turnstile-solver-failed',
                    'detail': 'ERROR_ZERO_BALANCE',
                    'fatal': True,
                }), \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 100, 102.7]):
            result = checkin.GLaDOS(COOKIE).checkin()

        post.assert_not_called()
        self.assertTrue(result['request_failed'])
        self.assertTrue(result['solver_failed'])
        # 复用 turnstile 语义，让 checkin_with_retry 转入浏览器兜底
        self.assertTrue(checkin.is_turnstile_checkin_result(result))
        self.assertIn('2captcha', checkin.summarize_checkin_failure(result))
        self.assertNotIn('ERROR_ZERO_BALANCE', checkin.summarize_checkin_failure(result))

    def test_solver_exception_is_contained(self):
        with mock.patch('checkin.requests.post') as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 300000, 'turnstile': SITEKEY,
                })), \
                mock.patch('checkin.turnstile_solver_enabled', return_value=True), \
                mock.patch('checkin.solve_turnstile', side_effect=RuntimeError('boom')), \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 100, 102.7]):
            result = checkin.GLaDOS(COOKIE).checkin()

        post.assert_not_called()
        self.assertEqual(result['detail'], 'RuntimeError')
        self.assertTrue(result['solver_failed'])

    def test_solver_disabled_keeps_legacy_payload(self):
        with mock.patch('checkin.requests.post', return_value=response(OK)) as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 300000, 'turnstile': SITEKEY,
                })), \
                mock.patch('checkin.turnstile_solver_enabled', return_value=False), \
                mock.patch('checkin.solve_turnstile') as solve, \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 102.7]):
            checkin.GLaDOS(COOKIE).checkin()

        solve.assert_not_called()
        self.assertEqual(post.call_args.kwargs['json'], {
            'token': 'glados.cloud',
            'nonce': NONCE,
            'proof': PROOF,
        })

    def test_expired_challenge_after_solve_is_not_submitted(self):
        with mock.patch('checkin.requests.post') as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 3000, 'turnstile': SITEKEY,
                })), \
                mock.patch('checkin.turnstile_solver_enabled', return_value=True), \
                mock.patch('checkin.solve_turnstile') as solve, \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 100, 104]):
            result = checkin.GLaDOS(COOKIE).checkin()

        # TTL 3s 扣除 2.7s 等待与 15s 安全边界后无求解预算
        solve.assert_not_called()
        post.assert_not_called()
        self.assertEqual(result['reason'], 'checkin-challenge-expired')

    def test_no_sitekey_never_calls_solver(self):
        with mock.patch('checkin.requests.post', return_value=response(OK)) as post, \
                mock.patch('checkin.requests.get', return_value=response({
                    'code': 0, 'nonce': NONCE, 'ttl': 300000,
                })), \
                mock.patch('checkin.turnstile_solver_enabled', return_value=True), \
                mock.patch('checkin.solve_turnstile') as solve, \
                mock.patch('checkin.time.sleep'), \
                mock.patch('checkin.time.monotonic', side_effect=[100, 102.7]):
            checkin.GLaDOS(COOKIE).checkin()

        solve.assert_not_called()
        self.assertEqual(post.call_args.kwargs['json'], {
            'token': 'glados.cloud',
            'nonce': NONCE,
            'proof': PROOF,
        })

    def test_solver_failure_triggers_browser_fallback_chain(self):
        client = checkin.GLaDOS(COOKIE)
        solver_failure = {
            'request_failed': True,
            'reason': 'turnstile',
            'solver_failed': True,
            'detail': 'ERROR_ZERO_BALANCE',
        }
        client.checkin = mock.Mock(return_value=solver_failure)
        with mock.patch.dict(os.environ, {
            'GITHUB_ACTIONS': 'false',
            'GLADOS_BROWSER_CHECKIN': '1',
            'GLADOS_USER_AGENT': 'SyntheticBrowser/1.0',
        }), mock.patch('browser_checkin.browser_checkin', return_value=dict(OK)) as bc, \
                mock.patch('checkin.log'):
            result, success = checkin.checkin_with_retry(client, 1, 0)

        self.assertTrue(success)
        self.assertEqual(result, OK)
        bc.assert_called_once()


if __name__ == '__main__':
    unittest.main()
