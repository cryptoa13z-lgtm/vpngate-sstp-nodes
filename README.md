# VPNGate SSTP 公共节点

本项目每 30 分钟读取 VPNGate 公开 API，筛选 TCP/SSTP 节点，并发布到 GitHub Pages。

## 节点格式

`public/nodes.txt` 每行格式：

```text
入口:端口#名称$sstp://vpn:vpn@VPNGate主机:端口
```

默认入口是 `edgetunnel.cryptoa13z.workers.dev:443`。入口必须走 Cloudflare Worker；不能把 VPNGate 的 IP:端口放在行首，否则会绕过 Worker。当前不使用外部优选 IP 池。`vpn:vpn` 是 VPNGate SSTP 公共服务约定，不是本项目密钥。

## 检测与发布

GitHub Actions 使用 `https://check-sstp.cryptoa13z.workers.dev/check`，每次最多检查 25 个候选，并发 2，单请求最多 25 秒；只有检测 API 返回 JSON 且 `success` 严格为 `true` 的节点才会发布。全失败时任务失败，不覆盖为空的列表。

本地运行默认不调用检测器，会输出状态 `unverified`，仅适合格式检查和开发测试。mock 测试不发送真实检测请求：

```sh
python3 -m unittest -v test_vpngate_public.py
```

workflow 使用官方 `upload-pages-artifact` 与 `deploy-pages` 发布 `public/`，权限为 `contents: read`、`pages: write`、`id-token: write`，不需要提交或推送生成文件。Pages 的仓库设置需由维护者完成。

每 30 分钟一次，理论上最多约 1200 次 Worker 检测请求/天。实际数量受候选数和失败情况影响。这不是可用性保证：VPNGate 节点可能失效，也不保证住宅、速度、稳定性、匿名性或隐私。

## 安全边界

`ADMIN`、`KEY`、`UUID` 和订阅 token 不写入仓库、Pages、节点列表或日志。本项目不生成含 UUID 的 VLESS 链接。

## 本地运行

```sh
cd /workspace/ipnode/repo-safe
MAX_NODES=25 HTTP_TIMEOUT=30 python3 vpngate_public.py
```

本地默认无需 secret、环境变量或凭据。可选环境变量包括 `VPNGATE_API`、`MAX_NODES`、`HTTP_TIMEOUT`、`PUBLIC_DIR`、`NODE_ENTRY`、`CHECK_WORKER` 和 `CHECK_REQUIRED`。启用 `CHECK_WORKER` 后，只有 `success: true` 才会发布；没有检测器时状态为 `unverified`。脚本在没有解析到节点或检测全失败时拒绝覆盖已有输出。

## 文件

- `vpngate_public.py`：抓取、解析、可选检测和输出。
- `public/data.json`：节点数据与状态。
- `public/nodes.txt`：客户端使用的节点行。
- `.github/workflows/refresh.yml`：30 分钟定时任务和 Pages 部署。
