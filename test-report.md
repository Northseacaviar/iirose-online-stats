# 单元测试报告

- 测试时间：2026-09-18 19:38
- pytest 版本：9.1.1（Python 3.14.7）
- 被测模块：`collector/anomaly`、`collector/handshake`、`collector/protocol`、`collector/sampler`、`collector/userlist`、`collector/ws_client`（集成）、`storage/db`、`run`（idle_watchdog）、`web/server`

## 结果汇总

| 项目 | 数量 |
|------|------|
| 测试总数 | 47 |
| ✅ 通过 | 47 |
| ❌ 失败 | 0 |
| ⏭️ 跳过 | 0 |

总耗时：12.80 秒

## 失败详情

本次测试全部通过，无失败项。

## 结论

- ✅ 全部通过（47/47）

## 补充说明

- 本次按单元测试技能流程检查发现 `collector/handshake.py` 原先没有任何测试，已新增 `app/tests/test_handshake.py`（8 个用例），覆盖：登录包字段、自定义房间、密码 md5 加密且不泄露明文、空凭证、Unicode 凭证、特殊字符、指纹格式与随机性。
- 其余 39 个用例为项目已有测试，本次一并执行验证。
