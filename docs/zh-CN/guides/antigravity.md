# 复用本地 agy 登录接入 Antigravity

首版支持 **Windows 本机 CLI＋Web、单账号**。先通过官方 `agy` 登录。
KT 不实现 OAuth 登录，不保存 refresh token，也不会替用户退出 Google 账号。

```powershell
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-3-flash
```

- `status` 仅查看本地凭据状态，不联网。
- `refresh` 在必要时运行 `agy --output-format json models`，由 agy 自己续期。
- `models` 联网获取账号可用模型，不产生推理。
- Web 设置中的 `google-antigravity` 行提供同样的状态、刷新与模型发现操作，遵循管理员权限配置。

内置 `gemini-3-flash`、`claude-sonnet-4-6` 两个预设，不自动改变默认模型。
也可复制预设，填写发现接口返回的模型 ID。内置上下文 120,000、输出 8,192
是保守的 KT 运行配置，不代表账号实际额度或模型上限。

## 凭据与会话

仅检查 Windows 凭据项 `gemini:antigravity` 和已知 agy 后备文件。
若两处同时存在凭据则明确报错，请先在 agy 中解决来源冲突。
只接受 consumer Bearer 凭据；access token 仅驻留内存，不进入日志。
KT 通过有超时限制的 agy 子进程刷新，跨进程锁及进程内共享任务避免重复刷新。

请求固定发往 `daily-cloudcode-pa.googleapis.com`，使用探针验证过的 agy 1.2.8
协议配置，不跟随重定向。项目发现与凭据代次绑定；账号切换期间会重新核验。
收到任何文本、思考、工具调用或签名后，不自动重发该推理。

会话保存原始签名片段，同时绑定模型、项目与当前消息内容。
同模型的工具往返、事件回放与恢复会话已通过离线完整 agent 工作流验证。
编辑消息后旧片段失效；跨模型可以保留普通文本，无法安全复用的工具签名历史
会提示新建或压缩会话。切换到 OpenAI 时不会外发 Google 内部状态字段。

## 当前边界

暂不支持远程节点、多账号、macOS/Linux 凭据源、自定义 API 地址、媒体生成及
显式 reasoning effort/extra_body 覆盖。思考使用模型默认值；支持内联图片，
其他不支持的内容或工具 schema 会明确报错。发现列表不保证每个模型支持所有模态。

此前在线探针已验证 agy 续期、项目与模型发现、Gemini 3 Flash 带签名工具往返、
Claude Sonnet 4.6 文本。本次实现验证使用离线 HTTP 响应及真实 Terrarium 工具执行、
持久化与恢复，没有追加在线推理。Claude 工具及思考组合尚未实测。

该可选接入仍受账号服务条款与限制约束，由使用者决定是否启用。
详见[开发方案](../dev/research/google-antigravity-oauth-development-plan-2026-09-23.md)
及[探针记录](../dev/research/antigravity-agy-probe-results-2026-09-23.md)。
