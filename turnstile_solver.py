#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2captcha Cloudflare Turnstile 求解客户端（仅 GLaDOS 签到使用）。

配置（可选；不配置则完全保持原有逻辑）：
    TWOCAPTCHA_API_KEY=xxxx
    # TWOCAPTCHA_API_BASE=https://api.2captcha.com
    # TWOCAPTCHA_TIMEOUT=120

GLaDOS 在 /api/user/checkin/challenge 中下发 sitekey，页面以
action="checkin"、cData=nonce 渲染 standalone Turnstile 挂件，因此任务
固定为 TurnstileTaskProxyless：
    { websiteURL, websiteKey, action, data }
不需要 Cloudflare Challenge 页才用的 pagedata/chlPageData。

安全：API key 只随 HTTPS 请求体发送，不写日志；令牌与 key 均不落盘。
"""

import math
import os
import time

import requests

DEFAULT_API_BASE = 'https://api.2captcha.com'
DEFAULT_TIMEOUT = 120.0
POLL_INTERVAL = 5.0
# 2captcha 文档建议 createTask 后先等待约 10s 再取结果，避免无效轮询。
FIRST_POLL_DELAY = 10.0
REQUEST_TIMEOUT = 15.0

# 这些错误重试也不会自愈（key 无效、余额不足等），便于调用方直接放弃。
FATAL_ERROR_CODES = frozenset({
    'ERROR_WRONG_USER_KEY',
    'ERROR_KEY_DOES_NOT_EXIST',
    'ERROR_ZERO_BALANCE',
    'ERROR_IP_NOT_ALLOWED',
    'ERROR_ACCOUNT_SUSPENDED',
    'ERROR_BANNED',
})


def get_api_key():
    return os.environ.get('TWOCAPTCHA_API_KEY', '').strip()


def turnstile_solver_enabled():
    """是否配置了 2captcha；未配置时调用方应保持原有签到逻辑。"""
    return bool(get_api_key())


def _api_base(override=None):
    base = (override or os.environ.get('TWOCAPTCHA_API_BASE', '') or DEFAULT_API_BASE).strip()
    return base.rstrip('/')


def _env_seconds(name, default):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def _failure(detail, fatal=False):
    return {
        'request_failed': True,
        'reason': 'turnstile-solver-failed',
        'detail': detail,
        'fatal': bool(fatal),
    }


def _post(base, path, payload, timeout):
    """POST 一个 2captcha 接口；失败只返回固定错误标识，不回显响应正文。"""
    try:
        resp = requests.post(f'{base}/{path}', json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as error:
        return None, _failure('network-' + type(error).__name__)
    except ValueError:
        return None, _failure('invalid-json')
    if not isinstance(data, dict):
        return None, _failure('invalid-response')
    return data, None


def _api_error(data):
    error_id = data.get('errorId', 0)
    if error_id in (0, '0', None):
        return None
    code = str(data.get('errorCode') or 'UNKNOWN').strip() or 'UNKNOWN'
    # 错误码是固定枚举，不含 key/令牌，可安全用于日志与返回值。
    return code


def solve_turnstile(sitekey, page_url, action='checkin', cdata=None,
                    timeout=None, api_key=None, api_base=None,
                    poll_interval=POLL_INTERVAL, first_poll_delay=FIRST_POLL_DELAY):
    """提交 TurnstileTaskProxyless 并等待令牌。

    成功返回 {'token', 'user_agent', 'cost', 'task_id'}；
    失败返回 {'request_failed': True, 'reason': 'turnstile-solver-failed',
              'detail': <固定错误标识>, 'fatal': bool}。
    """
    key = (api_key or get_api_key()).strip()
    if not key:
        return _failure('missing-api-key', fatal=True)
    if not isinstance(sitekey, str) or not sitekey.strip():
        return _failure('missing-sitekey', fatal=True)
    if not isinstance(page_url, str) or not page_url.strip():
        return _failure('missing-page-url', fatal=True)
    if not isinstance(action, str) or not action.strip():
        return _failure('missing-action', fatal=True)
    if not isinstance(cdata, str) or not cdata:
        return _failure('missing-cdata', fatal=True)

    if timeout is None:
        timeout = _env_seconds('TWOCAPTCHA_TIMEOUT', DEFAULT_TIMEOUT)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    if not math.isfinite(timeout) or timeout <= 0:
        return _failure('timeout')

    deadline = time.monotonic() + timeout
    base = _api_base(api_base)
    task = {
        'type': 'TurnstileTaskProxyless',
        'websiteURL': page_url.strip(),
        'websiteKey': sitekey.strip(),
        'action': action.strip(),
        'data': cdata,
    }
    data, failure = _post(base, 'createTask', {'clientKey': key, 'task': task},
                          min(REQUEST_TIMEOUT, max(1.0, deadline - time.monotonic())))
    if failure:
        return failure
    error = _api_error(data)
    if error:
        return _failure(error, fatal=error in FATAL_ERROR_CODES)

    task_id = data.get('taskId')
    if task_id in (None, '', 0, '0'):
        return _failure('missing-task-id')

    delay = max(0.0, float(first_poll_delay))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _failure('timeout')
        time.sleep(min(delay, remaining))
        delay = max(0.0, float(poll_interval))

        data, failure = _post(
            base, 'getTaskResult', {'clientKey': key, 'taskId': task_id},
            min(REQUEST_TIMEOUT, max(1.0, deadline - time.monotonic())),
        )
        if failure:
            return failure
        error = _api_error(data)
        if error:
            return _failure(error, fatal=error in FATAL_ERROR_CODES)

        status = data.get('status')
        if status == 'ready':
            solution = data.get('solution')
            if not isinstance(solution, dict):
                return _failure('missing-solution')
            token = solution.get('token')
            if not isinstance(token, str) or not token:
                return _failure('empty-token')
            user_agent = solution.get('userAgent')
            return {
                'token': token,
                'user_agent': user_agent if isinstance(user_agent, str) else None,
                'cost': data.get('cost'),
                'task_id': task_id,
            }
        if status != 'processing':
            return _failure('unexpected-status')
