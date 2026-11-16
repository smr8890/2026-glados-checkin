import os
import unittest
from unittest import mock

import checkin


COOKIE = 'gld:sess=fake; gld:sess.sig=fake'


class AccountMaskTests(unittest.TestCase):
    def test_email_local_part_is_masked(self):
        self.assertEqual(checkin.mask_account('2152918099@qq.com'), '21***9@qq.com')
        self.assertEqual(checkin.mask_account('synthetic@example.test'), 'sy***c@example.test')

    def test_short_or_non_email_values_are_masked_safely(self):
        self.assertEqual(checkin.mask_account('abc@example.test'), 'a***@example.test')
        self.assertEqual(checkin.mask_account('ab@example.test'), 'a***@example.test')
        self.assertEqual(checkin.mask_account('123456'), '12***6')
        self.assertEqual(checkin.mask_account(''), '')


class NotifyStyleTests(unittest.TestCase):
    def test_default_style_is_compact(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(checkin.get_notify_style(), 'compact')

    def test_detailed_style_is_preserved(self):
        with mock.patch.dict(os.environ, {'NOTIFY_STYLE': 'Detailed'}, clear=True):
            self.assertEqual(checkin.get_notify_style(), 'detailed')

    def test_invalid_style_falls_back_to_compact_with_warning(self):
        with mock.patch.dict(os.environ, {'NOTIFY_STYLE': 'unknown'}, clear=True), \
                mock.patch('checkin.log') as log:
            self.assertEqual(checkin.get_notify_style(), 'compact')

        log.assert_called_once()


class CompactNotificationTests(unittest.TestCase):
    def run_main(self):
        client = mock.Mock()
        client.email = 'synthetic@example.test'
        client.points = '500'
        client.left_days = '99'
        client.exchange_result = ''
        client.checkin.return_value = {'code': 0, 'message': 'Checkin! Got 20 Points'}
        client.req.return_value = {'code': 0, 'data': {'leftDays': '99'}}
        config = {
            'GLADOS_COOKIE': COOKIE,
            'PUSHPLUS_TOKEN': 'fake-token',
            'EXCHANGE_PLAN': 'off',
            'PUSH_LEVEL': 'all',
        }
        with mock.patch.dict(os.environ, config, clear=True), \
                mock.patch('checkin.GLaDOS', return_value=client), \
                mock.patch('checkin.pushplus', return_value=True) as pushplus, \
                mock.patch('checkin.serverchan_push') as serverchan, \
                mock.patch('checkin.telegram_push') as telegram:
            exit_code = checkin.main()
        return exit_code, pushplus, serverchan, telegram

    def test_default_compact_layout_is_one_masked_line(self):
        exit_code, pushplus, serverchan, telegram = self.run_main()

        self.assertEqual(exit_code, 0)
        pushplus.assert_called_once()
        title, body = pushplus.call_args.args[1:]
        self.assertEqual(title, 'GLaDOS签到: 本次运行成功 1/1')
        self.assertEqual(body, 'sy***c@example.test|Checkin! Got 20 Points|500|99 天')
        self.assertNotIn('<div', body)
        serverchan.assert_not_called()
        telegram.assert_not_called()


if __name__ == '__main__':
    unittest.main()
