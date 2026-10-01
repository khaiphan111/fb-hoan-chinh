#!/bin/bash
# deploy_money_test.sh - Tu dong test luong tien moi khi co deploy moi
#
# Cach hoat dong:
# 1. Lay commit SHA moi nhat cua main tu GitHub API
# 2. So sanh voi SHA da test (luu trong .last_deploy_test_sha)
# 3. Neu co commit moi:
#    - Tao git worktree tam thoi (khong anh huong bot dang chay)
#    - Chay test_money_paths.py voi code moi (chi du lieu test, tu don sach)
#    - PASS: cap nhat SHA, ghi log
#    - FAIL: gui Telegram bao admin, ghi log
# 4. Don sach worktree
#
# Chay bang cron moi 5 phut. Khong commit/push code.

set -u
REPO_DIR="/home/hatch/workspace/fb-hoan-chinh"
SHA_FILE="$REPO_DIR/.last_deploy_test_sha"
LOG_FILE="$REPO_DIR/deploy_test.log"
ENV_FILE="$REPO_DIR/botcheckv2/backend/.env"
WORKTREE_BASE="/tmp/deploy-test"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

tg_notify() {
    # $1 = noi dung tin nhan
    local token chat_id
    token=$(grep -E "^ADMIN_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
    chat_id=$(grep -E "^ADMIN_TG_ID=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
    [ -z "${token:-}" ] || [ -z "${chat_id:-}" ] && { log "WARN: thieu ADMIN_BOT_TOKEN/ADMIN_TG_ID, khong gui duoc Telegram"; return 1; }
    curl -s -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat_id}" --data-urlencode "text=$1" -d "parse_mode=HTML" >/dev/null 2>&1
}

# 1. Lay commit moi nhat tu GitHub
log "Kiem tra deploy moi..."
LATEST_SHA=$(curl -s -m 15 "https://api.github.com/repos/khaiphan111/fb-hoan-chinh/commits/main" | grep -o '"sha": "[a-f0-9]*"' | head -1 | cut -d'"' -f4)

if [ -z "$LATEST_SHA" ]; then
    log "WARN: khong lay duoc commit tu GitHub API, bo qua lan nay"
    exit 0
fi

LAST_SHA=""
[ -f "$SHA_FILE" ] && LAST_SHA=$(cat "$SHA_FILE" | tr -d ' \n\r')

if [ "$LATEST_SHA" = "$LAST_SHA" ]; then
    # Khong co deploy moi, im lang
    exit 0
fi

log "Phat hien deploy moi: ${LAST_SHA:0:7} -> ${LATEST_SHA:0:7}"

# 2. Fetch code moi (khong doi working tree)
cd "$REPO_DIR" || { log "ERROR: khong vao duoc $REPO_DIR"; exit 1; }
git fetch origin main 2>&1 | head -3

# 3. Tao worktree tam thoi
WORKTREE_DIR="$WORKTREE_BASE-${LATEST_SHA:0:8}"
# Don worktree cu neu con sot
git worktree remove --force "$WORKTREE_DIR" 2>/dev/null
rm -rf "$WORKTREE_DIR" 2>/dev/null

if ! git worktree add --detach "$WORKTREE_DIR" "$LATEST_SHA" 2>&1 | head -3; then
    log "ERROR: khong tao duoc worktree cho $LATEST_SHA"
    exit 1
fi

# Copy test file vao worktree (file nay chua commit, nam o repo chinh)
# Neu file da duoc commit trong tuong lai thi buoc nay thua nhung vo hai
if [ ! -f "$WORKTREE_DIR/test_money_paths.py" ] && [ -f "$REPO_DIR/test_money_paths.py" ]; then
    cp "$REPO_DIR/test_money_paths.py" "$WORKTREE_DIR/test_money_paths.py"
fi

# 4. Chay test voi code moi
# Dung tunnel Supabase san co (127.0.0.1:5433), chi du lieu test, tu don sach
log "Chay test luong tien voi commit ${LATEST_SHA:0:7}..."
TEST_OUTPUT=$(cd "$WORKTREE_DIR" && \
    TEST_BACKEND_PATH="$WORKTREE_DIR/botcheckv2/backend" \
    TEST_ENV_PATH="$ENV_FILE" \
    /home/hatch/fbvenv/bin/python test_money_paths.py 2>&1)
TEST_EXIT=$?

# 5. Xu ly ket qua
if [ $TEST_EXIT -eq 0 ]; then
    PASS_COUNT=$(echo "$TEST_OUTPUT" | grep -o "PASS [0-9]*" | grep -o "[0-9]*" | head -1)
    log "✅ Deploy test PASS (${PASS_COUNT:-?} checks) cho commit ${LATEST_SHA:0:7}"
    echo "$LATEST_SHA" > "$SHA_FILE"
    # Khong gui Telegram khi PASS (user ghet spam)
else
    log "❌ Deploy test FAIL cho commit ${LATEST_SHA:0:7} (exit $TEST_EXIT)"
    log "--- output (20 dong cuoi) ---"
    echo "$TEST_OUTPUT" | tail -20 | while IFS= read -r line; do log "  $line"; done
    # Van cap nhat SHA de khong spam lai, nhung danh dau FAIL
    echo "$LATEST_SHA" > "$SHA_FILE"
    # Gui Telegram bao admin
    FAIL_LINES=$(echo "$TEST_OUTPUT" | grep "FAIL:" | head -10)
    tg_notify "$(echo -e "❌ <b>Deploy test LUỒNG TIỀN thất bại</b>\nCommit: <code>${LATEST_SHA:0:7}</code>\n\n${FAIL_LINES}\n\nChi tiết trong deploy_test.log trên VM.")"
fi

# 6. Don sach worktree
git worktree remove --force "$WORKTREE_DIR" 2>/dev/null
rm -rf "$WORKTREE_DIR" 2>/dev/null
log "Don sach worktree xong"

exit 0
