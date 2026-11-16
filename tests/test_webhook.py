import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import checkin


URL = 'https://hooks.example.test/services/synthetic-token'
COOKIE = 'gld:sess=synthetic-session; gld:sess.sig=synthetic-signature'


class AccountMaskTests(unittest.TestCase):
    def test_email_local_part_is_masked(self):
        self.assertEqual(checkin.mask_account('2152918099@qq.com'), '21***9@qq.com')
        self.assertEqual(checkin.mask_account('synthetic@example.test'), 'sy***c@example.test')

    def test_short_or_non_email_values_are_masked_safely(self):
        self.assertEqual(checkin.mask_account('abc@example.test'), 'a***@example.test')
        self.assertEqual(checkin.mask_account('ab@example.test'), 'a***@example.test')
        self.assertEqual(checkin.mask_account('123456'), '12***6')
        self.assertEqual(checkin.mask_account(''), '')


class WebhookRequestTests(unittest.TestCase):
    def setUp(self):
        logging = mock.patch('checkin.log')
        self.log = logging.start()
        self.addCleanup(logging.stop)

    def test_missing_or_blank_url_skips_request(self):
        with mock.patch('checkin.requests.request') as request:
            for url in (None, '', '  \n '):
                with self.subTest(url=url):
                    self.assertFalse(checkin.webhook_push('签到结果', '<p>成功</p>', url=url))
        request.assert_not_called()

    def test_default_json_body_contains_title_and_plain_text(self):
        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200

            self.assertTrue(checkin.webhook_push(
                '签到结果', '<p>成功</p><p>积分: <b>500</b></p>', url=URL,
            ))

        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs['method'], 'POST')
        self.assertEqual(kwargs['url'], URL)
        self.assertEqual(kwargs['timeout'], 10)
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')
        self.assertEqual(
            json.loads(kwargs['data'].decode('utf-8')),
            {'title': '签到结果', 'content': '成功\n积分: 500'},
        )

    def test_json_template_handles_quotes_and_newlines(self):
        body = json.dumps({
            'msgtype': 'markdown',
            'markdown': {'text': '$title\n$content_text'},
        }, ensure_ascii=False)
        content = '<p>第一行 "引号"</p><p>第二行 &amp; 转义</p>'

        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push(
                '标题 "x"', content, url=URL, body=body,
            ))

        payload = json.loads(request.call_args.kwargs['data'].decode('utf-8'))
        self.assertEqual(
            payload['markdown']['text'],
            '标题 "x"\n第一行 "引号"\n第二行 & 转义',
        )

    def test_custom_headers_are_parsed_and_content_type_is_preserved(self):
        headers = 'Authorization: Bearer synthetic\nX-Custom: 1\nContent-Type: text/plain; charset=utf-8'

        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push(
                '签到结果', '<p>成功</p>', url=URL, headers=headers, body='$title\n$content_text',
            ))

        request_headers = request.call_args.kwargs['headers']
        self.assertEqual(request_headers['Authorization'], 'Bearer synthetic')
        self.assertEqual(request_headers['X-Custom'], '1')
        self.assertEqual(request_headers['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(request.call_args.kwargs['data'], '签到结果\n成功'.encode('utf-8'))

    def test_json_headers_object_is_accepted(self):
        headers = json.dumps({'X-Token': 'synthetic'})

        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push(
                '签到结果', '<p>成功</p>', url=URL, headers=headers,
            ))

        self.assertEqual(request.call_args.kwargs['headers']['X-Token'], 'synthetic')
        self.assertEqual(request.call_args.kwargs['headers']['Content-Type'], 'application/json')

    def test_url_placeholders_are_percent_encoded(self):
        url = f'{URL}?title=$title&content=$content_text'

        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push('签到 结果', '<p>成功 &amp; 完成</p>', url=url))

        query = parse_qs(urlsplit(request.call_args.kwargs['url']).query)
        self.assertEqual(query['title'], ['签到 结果'])
        self.assertEqual(query['content'], ['成功 & 完成'])

    def test_form_body_is_url_encoded(self):
        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push(
                '签到结果', '<p>成功 &amp; 完成</p>', url=URL,
                content_type='application/x-www-form-urlencoded',
                body='title=$title&content=$content_text',
            ))

        body = parse_qs(request.call_args.kwargs['data'].decode('utf-8'))
        self.assertEqual(body['title'], ['签到结果'])
        self.assertEqual(body['content'], ['成功 & 完成'])

    def test_custom_method_is_used(self):
        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 204
            self.assertTrue(checkin.webhook_push('签到结果', '<p>成功</p>', url=URL, method='put'))

        self.assertEqual(request.call_args.kwargs['method'], 'PUT')

    def test_http_error_is_failure_without_exposing_url(self):
        with mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 403
            self.assertFalse(checkin.webhook_push('签到结果', '<p>成功</p>', url=URL))

        self.assertNotIn(URL, str(self.log.call_args_list))

    def test_connection_error_is_failure_without_exposing_url(self):
        with mock.patch('checkin.requests.request') as request:
            request.side_effect = checkin.requests.ConnectionError(f'failed: {URL}')
            self.assertFalse(checkin.webhook_push('签到结果', '<p>成功</p>', url=URL))

        self.assertNotIn(URL, str(self.log.call_args_list))

    def test_configured_environment_is_used(self):
        environment = {
            'GLADOS_WEBHOOK_URL': URL,
            'GLADOS_WEBHOOK_METHOD': 'patch',
            'GLADOS_WEBHOOK_CONTENT_TYPE': 'text/plain',
            'GLADOS_WEBHOOK_HEADERS': 'X-Env: yes',
            'GLADOS_WEBHOOK_BODY': '$title\n$content_text',
        }
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch('checkin.requests.request') as request:
            request.return_value.status_code = 200
            self.assertTrue(checkin.webhook_push('签到结果', '<p>成功</p>'))

        self.assertEqual(request.call_args.kwargs['method'], 'PATCH')
        self.assertEqual(request.call_args.kwargs['headers']['X-Env'], 'yes')
        self.assertEqual(
            request.call_args.kwargs['data'], '签到结果\n成功'.encode('utf-8'),
        )

    def test_qinglong_webhook_environment_is_not_used(self):
        with mock.patch.dict(os.environ, {
            'WEBHOOK_URL': URL,
            'WEBHOOK_METHOD': 'POST',
        }, clear=True), mock.patch('checkin.requests.request') as request:
            self.assertFalse(checkin.webhook_push('签到结果', '<p>成功</p>'))

        request.assert_not_called()


class WebhookMainTests(unittest.TestCase):
    def run_main(self, environment=None, failure=False, webhook_ok=True):
        client = mock.Mock()
        client.email = 'synthetic@example.test'
        client.points = '500'
        client.points_change = '+20'
        client.left_days = '99'
        client.exchange_info = '100分→10天<br>500分→100天'
        client.exchange_result = ''
        client.checkin.return_value = (
            {'code': -2, 'message': '没有权限'} if failure else
            {'code': 0, 'message': 'Checkin! Got 20 Points'}
        )
        client.req.return_value = {'code': 0, 'data': {'leftDays': '99'}}
        config = {
            'GLADOS_COOKIE': COOKIE,
            'GLADOS_WEBHOOK_URL': URL,
            'EXCHANGE_PLAN': 'off',
            'PUSH_LEVEL': 'all',
        }
        config.update(environment or {})
        with mock.patch.dict(os.environ, config, clear=True), \
                mock.patch('checkin.GLaDOS', return_value=client), \
                mock.patch('checkin.webhook_push', return_value=webhook_ok) as webhook, \
                mock.patch('checkin.pushplus') as pushplus, \
                mock.patch('checkin.serverchan_push') as serverchan, \
                mock.patch('checkin.telegram_push') as telegram, \
                mock.patch('checkin.auto_exchange', return_value=None):
            exit_code = checkin.main()
        return exit_code, webhook, pushplus, serverchan, telegram

    def test_webhook_only_configuration_sends_summary(self):
        exit_code, webhook, pushplus, serverchan, telegram = self.run_main()

        self.assertEqual(exit_code, 0)
        webhook.assert_called_once()
        title, body = webhook.call_args.args
        self.assertEqual(title, 'GLaDOS签到: 本次运行成功 1/1')
        self.assertEqual(body, 'sy***c@example.test|Checkin! Got 20 Points|500|99 天')
        pushplus.assert_not_called()
        serverchan.assert_not_called()
        telegram.assert_not_called()

    def test_all_configured_channels_receive_summary(self):
        _, webhook, pushplus, serverchan, telegram = self.run_main({
            'PUSHPLUS_TOKEN': 'synthetic-pushplus-token',
            'SERVERCHAN_SENDKEY': 'SCT123synthetic',
            'TELEGRAM_BOT_TOKEN': 'synthetic-telegram-token',
            'TELEGRAM_CHAT_ID': 'synthetic-chat-id',
        })

        webhook.assert_called_once()
        pushplus.assert_called_once()
        serverchan.assert_called_once()
        telegram.assert_called_once()
        self.assertEqual(webhook.call_args.args, serverchan.call_args.args[1:])
        self.assertEqual(webhook.call_args.args[0], pushplus.call_args.args[1])
        self.assertEqual(webhook.call_args.args[0], telegram.call_args.args[2])
        self.assertEqual(webhook.call_args.args[1], telegram.call_args.args[3])

    def test_fail_only_skips_success_without_exchange(self):
        exit_code, webhook, _, _, _ = self.run_main({'PUSH_LEVEL': 'fail_only'})

        self.assertEqual(exit_code, 0)
        webhook.assert_not_called()

    def test_fail_only_sends_failure(self):
        exit_code, webhook, _, _, _ = self.run_main({'PUSH_LEVEL': 'fail_only'}, failure=True)

        self.assertEqual(exit_code, 1)
        webhook.assert_called_once()
        self.assertEqual(webhook.call_args.args[0], 'GLaDOS签到: 本次运行失败 0/1')
        body = webhook.call_args.args[1]
        self.assertEqual(body.count('|'), 3)
        self.assertTrue(body.startswith('sy***c@example.test|'))
        self.assertTrue(body.endswith('|500|99 天'))

    def test_webhook_failure_does_not_change_checkin_result(self):
        exit_code, webhook, _, _, _ = self.run_main(webhook_ok=False)

        self.assertEqual(exit_code, 0)
        webhook.assert_called_once()

    def test_notify_status_only_sends_webhook_and_returns_nonzero_on_failure(self):
        client = mock.Mock()
        client.email = 'readonly@example.test'
        client.points = '15'
        client.points_change = '?'
        client.points_change_time = '未知'
        client.left_days = '688'
        client.exchange_info = '100分→10天'
        client.exchange_result = ''
        client.get_status.return_value = True
        client.get_points.return_value = True

        with mock.patch.dict(os.environ, {
            'GLADOS_COOKIE': COOKIE,
            'CHECKIN_NOTIFY_STATUS_ONLY': 'true',
            'GLADOS_WEBHOOK_URL': URL,
        }, clear=True), \
                mock.patch('checkin.GLaDOS', return_value=client), \
                mock.patch('checkin.log_user_agent_configuration', return_value=True), \
                mock.patch('checkin.webhook_push', return_value=False) as webhook, \
                mock.patch('checkin.log'):
            exit_code = checkin.main()

        self.assertEqual(exit_code, 1)
        webhook.assert_called_once()
        title, body = webhook.call_args.args
        self.assertIn('GLaDOS状态核对: 只读核对完成 1/1', title)
        self.assertIn('re***y@example.test', body)
        self.assertIn('未发起签到请求', body)
        self.assertNotIn('readonly@example.test', body)
        client.checkin.assert_not_called()
        client.exchange.assert_not_called()


if __name__ == '__main__':
    unittest.main()
