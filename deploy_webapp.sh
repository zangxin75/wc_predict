#!/usr/bin/env bash
# deploy_webapp.sh — WC2026 预测系统一键部署脚本
#
# 用法:
#   在目标服务器上以 root 或 sudo 用户执行:
#     bash <(curl -fsSL https://raw.githubusercontent.com/zangxin75/wc_predict/master/deploy_webapp.sh)
#
#   或在服务器上 clone 后本地执行:
#     git clone git@github.com:zangxin75/wc_predict.git /opt/wc_predict
#     cd /opt/wc_predict && bash deploy_webapp.sh
#
# 前提: Ubuntu/Debian 服务器,Python 3.10+,已安装 git
#
# 部署内容:
#   1. 安装系统依赖
#   2. 创建 Python venv + pip 安装
#   3. 创建 systemd 服务(uvicorn 守护)
#   4. 创建 nginx 反向代理配置
#   5. 启动服务
#
# 环境变量(可选):
#   DEPLOY_DIR=/opt/wc_predict          # 部署目录(默认 /opt/wc_predict)
#   DOMAIN=aijialidu.ticp.vip           # 域名(默认从提示输入)
#   APP_PORT=8000                        # uvicorn 端口(默认 8000)
#   SKIP_NGINUX=1                        # 跳过 nginx 配置
#   SKIP_SYSTEMD=1                       # 跳过 systemd 配置

set -euo pipefail

# ── 默认值 ──────────────────────────────────────────────
DEPLOY_DIR="${DEPLOY_DIR:-/opt/wc_predict}"
DOMAIN="${DOMAIN:-}"
APP_PORT="${APP_PORT:-8000}"
APP_USER="${APP_USER:-www-data}"
REPO_URL="git@github.com:zangxin75/wc_predict.git"

# ── 颜色 ────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }
step()  { echo -e "\n${YELLOW}━━━ $* ━━━${NC}"; }

# ── 前置检查 ────────────────────────────────────────────
step "环境检查"
command -v python3 >/dev/null 2>&1 || { err "需要 python3"; exit 1; }
command -v git >/dev/null 2>&1 || { err "需要 git"; exit 1; }
PYVER=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
info "Python $PYVER"
command -v nginx >/dev/null 2>&1 && HAS_NGINX=1 || HAS_NGINX=0

if [ "$EUID" -ne 0 ]; then
  warn "建议以 root 运行,否则 systemd/nginx 需要手动处理"
fi

if [ -z "$DOMAIN" ]; then
  read -rp "请输入域名(如 aijialidu.ticp.vip): " DOMAIN
fi
info "域名: $DOMAIN"

# ── 克隆/拉取代码 ───────────────────────────────────────
step "代码部署"
if [ -d "$DEPLOY_DIR/.git" ]; then
  info "目录已存在,执行 git pull"
  cd "$DEPLOY_DIR"
  git pull
else
  info "克隆仓库到 $DEPLOY_DIR"
  mkdir -p "$(dirname "$DEPLOY_DIR")"
  git clone "$REPO_URL" "$DEPLOY_DIR"
  cd "$DEPLOY_DIR"
fi

# ── Python 虚拟环境 ────────────────────────────────────
step "Python 虚拟环境"
if [ -d ".venv" ]; then
  info "虚拟环境已存在,跳过创建"
else
  python3 -m venv .venv
  info "虚拟环境已创建"
fi
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
info "依赖安装完成"

# ── .env 配置 ──────────────────────────────────────────
step "环境变量配置"
if [ ! -f ".env" ]; then
  cat > .env << 'ENVEOF'
# Stripe 生产密钥 — 替换为正式密钥!
# 测试密钥(用 4242 4242 4242 4242 测试卡):
STRIPE_SECRET_KEY=sk_test_YOUR_KEY_HERE
STRIPE_PUBLISHABLE_KEY=pk_test_YOUR_KEY_HERE
STRIPE_WEBHOOK_SECRET=whsec_YOUR_WEBHOOK_SECRET
ENVEOF
  info ".env 文件已创建(测试密钥),正式上线前请替换为 live 密钥!"
else
  info ".env 文件已存在,跳过"
fi

# ── 创建 data 目录 ─────────────────────────────────────
mkdir -p webapp/data

# ── Systemd 服务 ────────────────────────────────────────
if [ "${SKIP_SYSTEMD:-0}" != "1" ]; then
  step "Systemd 服务"
  cat > /etc/systemd/system/wc-predict.service << UNIT
[Unit]
Description=WC2026 Prediction Center
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$DEPLOY_DIR
Environment=PATH=$DEPLOY_DIR/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$DEPLOY_DIR/.venv/bin/uvicorn webapp.app:app --host 127.0.0.1 --port $APP_PORT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
  info "systemd 服务已创建: wc-predict.service"
  systemctl daemon-reload
  systemctl enable wc-predict
  info "服务已启用(开机自启)"
fi

# ── Nginx 配置 ──────────────────────────────────────────
if [ "${SKIP_NGINX:-0}" != "1" ] && [ "$HAS_NGINX" = "1" ]; then
  step "Nginx 配置"
  cat > /etc/nginx/sites-available/wc-predict << NGINX
server {
    listen 80;
    server_name $DOMAIN;

    # 后端 API
    location /wc/ {
        proxy_pass http://127.0.0.1:$APP_PORT/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket 支持(如需)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # 超时(后台抓取可能较慢)
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    # 静态资源缓存
    location /wc/static/ {
        proxy_pass http://127.0.0.1:$APP_PORT/static/;
        proxy_cache_valid 200 5m;
        expires 5m;
    }

    # 根路径重定向
    location / {
        return 301 /wc/;
    }
}
NGINX
  ln -sf /etc/nginx/sites-available/wc-predict /etc/nginx/sites-enabled/
  nginx -t && systemctl reload nginx
  info "nginx 配置已应用(路径 /wc/)"
else
  warn "跳过 nginx 配置(SKIP_NGINX=1 或未安装 nginx)"
fi

# ── 启动服务 ────────────────────────────────────────────
step "启动服务"
if [ "${SKIP_SYSTEMD:-0}" != "1" ]; then
  systemctl restart wc-predict
  sleep 3
  if systemctl is-active --quiet wc-predict; then
    info "wc-predict 服务运行中"
    systemctl status wc-predict --no-pager | head -10
  else
    err "服务启动失败,查看日志: journalctl -u wc-predict -n 50"
    exit 1
  fi
fi

# ── 完成 ────────────────────────────────────────────────
step "部署完成"
echo ""
echo -e "  访问地址:  ${GREEN}http://$DOMAIN/wc/${NC}"
echo -e "  API 状态:  curl http://$DOMAIN/wc/api/status"
echo -e "  服务管理:  systemctl restart|stop|status wc-predict"
echo -e "  查看日志:  journalctl -u wc-predict -f"
echo ""

if grep -q "sk_test" .env 2>/dev/null; then
  warn "!!! 当前使用测试密钥 !!!"
  echo "  正式上线前请替换 .env 中的密钥为 Stripe live 密钥:"
  echo "  - STRIPE_SECRET_KEY=sk_live_xxx"
  echo "  - STRIPE_PUBLISHABLE_KEY=pk_live_xxx"
  echo "  - 重新配置 Webhook Endpoint(https)"
fi
