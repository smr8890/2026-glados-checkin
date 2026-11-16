"""2captcha Turnstile 求解客户端的离线回归测试（不发起真实请求）。"""

import os
import unittest
from unittest import mock

import turnstile_solver

SITEKEY = '0x4AAAAAAA-synthetic-sitekey'
PAGE_URL = 'https://glados.cloud/console/checkin'
NONCE = 'synthetic-nonce'


def response(payload, status=200):
    result = mock.Mock(status_code=status)
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


class SolverDisabledTests(unittest.TestCase):
    def test_not_enabled_without_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(turnstile_solver.turnstile_solver_enabled())

    def test_enabled_with_api_key(self):
        with mock.patch.dict(os.environ, {'TWOCAPTCHA_API_KEY': 'synthetic-key'}):
            self.assertTrue(turnstile_solver.turnstile_solver_enabled())

    @mock.patch('turnstile_solver.requests.post')
    def test_missing_key_fails_before_network(self, post):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = turnstile_solver.solve_turnstile(
                SITEKEY, PAGE_URL, action='checkin', cdata=NONCE)
        self.assertTrue(result['request_failed'])
        self.assertEqual(result['detail'], 'missing-api-key')
        self.assertTrue(result['fatal'])
        post.assert_not_called()


class SolverRequestTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {
            'TWOCAPTCHA_API_KEY': 'synthetic-key',
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)

    @mock.patch('turnstile_solver.time.sleep')
    @mock.patch('turnstile_solver.requests.post')
    def test_standalone_turnstile_task_and_polling(self, post, sleep):
        post.side_effect = [
            response({'errorId': 0, 'taskId': 42}),
            response({'errorId': 0, 'status': 'processing'}),
            response({
                'errorId': 0,
                'status': 'ready',
                'solution': {'token': 'synthetic-token', 'userAgent': 'Solver/1.0'},
                'cost': '0.00145',
            }),
        ]

        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE,
            timeout=60, poll_interval=0, first_poll_delay=0)

        self.assertEqual(result['token'], 'synthetic-token')
        self.assertEqual(result['user_agent'], 'Solver/1.0')
        self.assertEqual(result['cost'], '0.00145')
        self.assertEqual(result['task_id'], 42)

        create = post.call_args_list[0]
        self.assertEqual(create.args[0], 'https://api.2captcha.com/createTask')
        self.assertEqual(create.kwargs['json'], {
            'clientKey': 'synthetic-key',
            'task': {
                'type': 'TurnstileTaskProxyless',
                'websiteURL': PAGE_URL,
                'websiteKey': SITEKEY,
                'action': 'checkin',
                'data': NONCE,
            },
        })
        poll = post.call_args_list[1]
        self.assertEqual(poll.args[0], 'https://api.2captcha.com/getTaskResult')
        self.assertEqual(poll.kwargs['json'], {'clientKey': 'synthetic-key', 'taskId': 42})

    @mock.patch('turnstile_solver.time.sleep')
    @mock.patch('turnstile_solver.requests.post')
    def test_api_base_override_is_normalized(self, post, sleep):
        post.side_effect = [
            response({'errorId': 0, 'taskId': 7}),
            response({'errorId': 0, 'status': 'ready', 'solution': {'token': 'tok'}}),
        ]
        with mock.patch.dict(os.environ, {'TWOCAPTCHA_API_BASE': 'https://api.2captcha.cn/'}):
            result = turnstile_solver.solve_turnstile(
                SITEKEY, PAGE_URL, action='checkin', cdata=NONCE,
                timeout=60, poll_interval=0, first_poll_delay=0)
        self.assertEqual(result['token'], 'tok')
        self.assertEqual(post.call_args_list[0].args[0], 'https://api.2captcha.cn/createTask')

    @mock.patch('turnstile_solver.requests.post')
    def test_zero_balance_is_fatal(self, post):
        post.return_value = response({
            'errorId': 1,
            'errorCode': 'ERROR_ZERO_BALANCE',
            'errorDescription': 'Account has no balance',
        })
        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE, timeout=60)
        self.assertTrue(result['request_failed'])
        self.assertEqual(result['detail'], 'ERROR_ZERO_BALANCE')
        self.assertTrue(result['fatal'])
        post.assert_called_once()

    @mock.patch('turnstile_solver.requests.post')
    def test_unsolvable_is_retryable(self, post):
        post.return_value = response({'errorId': 1, 'errorCode': 'ERROR_CAPTCHA_UNSOLVABLE'})
        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE, timeout=60)
        self.assertEqual(result['detail'], 'ERROR_CAPTCHA_UNSOLVABLE')
        self.assertFalse(result['fatal'])

    @mock.patch('turnstile_solver.time.sleep')
    @mock.patch('turnstile_solver.requests.post')
    def test_ready_without_token_is_failure(self, post, sleep):
        post.side_effect = [
            response({'errorId': 0, 'taskId': 42}),
            response({'errorId': 0, 'status': 'ready', 'solution': {}}),
        ]
        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE,
            timeout=60, poll_interval=0, first_poll_delay=0)
        self.assertEqual(result['detail'], 'empty-token')

    @mock.patch('turnstile_solver.requests.post')
    def test_non_positive_timeout_skips_network(self, post):
        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE, timeout=0)
        self.assertEqual(result['detail'], 'timeout')
        post.assert_not_called()

    @mock.patch('turnstile_solver.requests.post')
    def test_network_error_does_not_echo_details(self, post):
        post.side_effect = turnstile_solver.requests.Timeout('synthetic detail')
        result = turnstile_solver.solve_turnstile(
            SITEKEY, PAGE_URL, action='checkin', cdata=NONCE, timeout=60)
        self.assertEqual(result['detail'], 'network-Timeout')
        self.assertNotIn('synthetic detail', str(result))

    def test_required_task_fields_are_validated(self):
        cases = (
            ({'sitekey': ''}, 'missing-sitekey'),
            ({'page_url': ''}, 'missing-page-url'),
            ({'action': ''}, 'missing-action'),
            ({'cdata': ''}, 'missing-cdata'),
        )
        for overrides, detail in cases:
            kwargs = {
                'sitekey': SITEKEY,
                'page_url': PAGE_URL,
                'action': 'checkin',
                'cdata': NONCE,
                'api_key': 'synthetic-key',
                'timeout': 60,
            }
            kwargs.update(overrides)
            with self.subTest(detail=detail):
                result = turnstile_solver.solve_turnstile(**kwargs)
                self.assertEqual(result['detail'], detail)
                self.assertTrue(result['fatal'])


if __name__ == '__main__':
    unittest.main()
