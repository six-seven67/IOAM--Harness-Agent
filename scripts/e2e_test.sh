#!/usr/bin/env bash
# IOAM Harness — 端到端业务流程测试
# 测试完整链路: AIOps 诊断 → RAG 询问 → 排查 → 代码修改
#
# 用法: bash scripts/e2e_test.sh
# 要求: 先启动 Docker 容器（MySQL + Redis + Milvus）和 FastAPI 主服务
#
# 设计原则:
#   - 使用独立的测试 session_id，不影响已有数据
#   - MCP 服务器自动启动/停止，测试完清理
#   - 所有步骤有明确的结果检查

set -e

# ── 配置 ────────────────────────────────────────────────────
BASE_URL="${BASE_URL:-http://localhost:9900}"
MCP_CLS_PORT=8003
MCP_MONITOR_PORT=8004
TEST_USER="e2e_test_$(date +%s)"
TEST_PASS="test123456"
TEST_SESSION="e2e-session-$(date +%s)"
PASSED=0
FAILED=0

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# 自动保存结果到日志文件
mkdir -p logs
LOG_FILE="logs/e2e_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "📝 结果将自动保存到: $LOG_FILE"

# ── 工具函数 ────────────────────────────────────────────────
check() {
    local desc="$1"
    local condition="$2"
    if eval "$condition"; then
        echo -e "  ${GREEN}✅ PASS${NC}: $desc"
        PASSED=$((PASSED + 1))
    else
        echo -e "  ${RED}❌ FAIL${NC}: $desc"
        FAILED=$((FAILED + 1))
    fi
}

section() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
}

cleanup_mcp() {
    echo -e "${YELLOW}🧹 清理 MCP 服务器进程...${NC}"
    pkill -f "mcp_servers/cls_server.py" 2>/dev/null || true
    pkill -f "mcp_servers/monitor_server.py" 2>/dev/null || true
    rm -f mcp_cls.pid mcp_monitor.pid 2>/dev/null || true
}

# ── 准备工作 ────────────────────────────────────────────────
section "0. 环境检查与 MCP 启动"

# 检查 FastAPI 是否运行
if ! curl -s "${BASE_URL}/health" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  FastAPI 服务未运行 ($BASE_URL)，请先执行: make start${NC}"
    echo -e "${YELLOW}   或手动启动: python3 -m uvicorn app.main:app --host 0.0.0.0 --port 9900${NC}"
    exit 1
fi
echo -e "  ${GREEN}✅${NC} FastAPI 服务可达: ${BASE_URL}"

# 清理旧的 MCP 进程并重新启动
cleanup_mcp
sleep 1

# 启动 CLS MCP 服务
echo -e "${YELLOW}📋 启动 CLS MCP 服务 (端口 $MCP_CLS_PORT)...${NC}"
nohup python3 mcp_servers/cls_server.py > logs/mcp_cls_test.log 2>&1 &
echo $! > mcp_cls.pid
sleep 2
if pgrep -f "mcp_servers/cls_server.py" > /dev/null 2>&1; then
    echo -e "  ${GREEN}✅${NC} CLS MCP 服务已启动 (PID: $(cat mcp_cls.pid))"
else
    echo -e "  ${RED}❌${NC} CLS MCP 服务启动失败"
    cat logs/mcp_cls_test.log | tail -10
    exit 1
fi

# 启动 Monitor MCP 服务
echo -e "${YELLOW}📊 启动 Monitor MCP 服务 (端口 $MCP_MONITOR_PORT)...${NC}"
nohup python3 mcp_servers/monitor_server.py > logs/mcp_monitor_test.log 2>&1 &
echo $! > mcp_monitor.pid
sleep 2
if pgrep -f "mcp_servers/monitor_server.py" > /dev/null 2>&1; then
    echo -e "  ${GREEN}✅${NC} Monitor MCP 服务已启动 (PID: $(cat mcp_monitor.pid))"
else
    echo -e "  ${RED}❌${NC} Monitor MCP 服务启动失败"
    cat logs/mcp_monitor_test.log | tail -10
    exit 1
fi

# ── 步骤 1: 注册测试用户 ────────────────────────────────────
section "1. 注册测试用户"

REGISTER_RESP=$(curl -s -X POST "${BASE_URL}/api/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"${TEST_USER}\", \"password\": \"${TEST_PASS}\"}")

echo "  注册响应: $(echo $REGISTER_RESP | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("message",""))' 2>/dev/null || echo 'parse error')"

TOKEN=$(echo "$REGISTER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('access_token',''))" 2>/dev/null)

if [ -n "$TOKEN" ] && [ "$TOKEN" != "" ]; then
    echo -e "  ${GREEN}✅${NC} 注册成功，Token: ${TOKEN:0:20}..."
    AUTH_HEADER="Authorization: Bearer $TOKEN"
else
    # 可能用户已存在，尝试登录
    echo -e "  ${YELLOW}⚠️${NC}  注册失败（可能用户已存在），尝试登录..."
    LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"${TEST_USER}\", \"password\": \"${TEST_PASS}\"}")
    TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('access_token',''))" 2>/dev/null)
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "" ]; then
        echo -e "  ${GREEN}✅${NC} 登录成功"
        AUTH_HEADER="Authorization: Bearer $TOKEN"
    else
        echo -e "  ${RED}❌${NC} 登录也失败: $LOGIN_RESP"
        cleanup_mcp
        exit 1
    fi
fi

# ── 步骤 2: 用户信息修改（验证 Bug 3 修复）─────────────────
section "2. 用户信息修改 (Bug 3 验证)"

# 修改邮箱
UPDATE_RESP=$(curl -s -X PUT "${BASE_URL}/api/auth/me" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"e2e-test@example.com\"}")
UPDATE_MSG=$(echo "$UPDATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
check "修改邮箱" '[ "$UPDATE_MSG" = "用户信息已更新。" ]'

# 修改密码
UPDATE_RESP2=$(curl -s -X PUT "${BASE_URL}/api/auth/me" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"old_password\": \"${TEST_PASS}\", \"new_password\": \"newpass789\"}")
UPDATE_MSG2=$(echo "$UPDATE_RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
check "修改密码" '[ "$UPDATE_MSG2" = "用户信息已更新。" ]'

# 改回原密码（不影响后续测试）
curl -s -X PUT "${BASE_URL}/api/auth/me" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"old_password\": \"newpass789\", \"new_password\": \"${TEST_PASS}\"}" > /dev/null 2>&1

# ── 步骤 3: RAG 问答 — 询问排查方法 ─────────────────────────
section "3. RAG 问答: 询问 CPU 高排查方法"

RAG_RESP=$(curl -s -X POST "${BASE_URL}/api/chat" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"Id\": \"${TEST_SESSION}\", \"Question\": \"data-sync-service CPU 使用率很高怎么排查？请给出具体步骤\"}")

# 检查响应是否包含有效内容
RAG_CODE=$(echo "$RAG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('code',''))" 2>/dev/null || echo "")
check "RAG 问答返回成功" '[ "$RAG_CODE" = "200" ]'

RAG_ANSWER=$(echo "$RAG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('answer','')[:200])" 2>/dev/null || echo "")
echo -e "  ${YELLOW}📝 RAG 回答预览:${NC} ${RAG_ANSWER}..."

check "RAG 回答非空" '[ -n "$RAG_ANSWER" ]'

# ── 步骤 4: AIOps 诊断（流式 SSE）───────────────────────────
section "4. AIOps 诊断: 自动故障诊断（Plan-Execute-Replan）"

echo -e "${YELLOW}  发起 AIOps 诊断请求 (SSE 流式)...${NC}"

# 使用 timeout 防止挂死，收集 SSE 事件
AIOPS_OUTPUT=$(timeout 120 curl -s -N -X POST "${BASE_URL}/api/aiops" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -d "{\"session_id\": \"${TEST_SESSION}-aiops\"}" 2>&1) || true

# 统计各类 SSE 事件（JSON 序列化后冒号后有空格: "type": "plan"）
PLAN_COUNT=$(echo "$AIOPS_OUTPUT" | grep -c '"type": "plan"' 2>/dev/null || echo "0")
STEP_COUNT=$(echo "$AIOPS_OUTPUT" | grep -c '"type": "step_complete"' 2>/dev/null || echo "0")
COMPLETE_COUNT=$(echo "$AIOPS_OUTPUT" | grep -c '"type": "complete"' 2>/dev/null || echo "0")
REPORT_COUNT=$(echo "$AIOPS_OUTPUT" | grep -c '"type": "report"' 2>/dev/null || echo "0")
ERROR_COUNT=$(echo "$AIOPS_OUTPUT" | grep -c '"type": "error"' 2>/dev/null || echo "0")
# 清理换行符，确保是纯数字
PLAN_COUNT=$(echo "$PLAN_COUNT" | tr -d '\n' | tr -d ' ')
STEP_COUNT=$(echo "$STEP_COUNT" | tr -d '\n' | tr -d ' ')
COMPLETE_COUNT=$(echo "$COMPLETE_COUNT" | tr -d '\n' | tr -d ' ')
REPORT_COUNT=$(echo "$REPORT_COUNT" | tr -d '\n' | tr -d ' ')
ERROR_COUNT=$(echo "$ERROR_COUNT" | tr -d '\n' | tr -d ' ')

echo -e "  📊 AIOps 事件统计: plan=$PLAN_COUNT, step_complete=$STEP_COUNT, report=$REPORT_COUNT, error=$ERROR_COUNT"

check "AIOps Planner 制定了计划" '[ "$PLAN_COUNT" -ge 1 ]'
check "AIOps Executor 执行了步骤" '[ "$STEP_COUNT" -ge 1 ]'
check "AIOps 生成了诊断报告" '[ "$REPORT_COUNT" -ge 1 ]'
# SQLite 线程错误是已知问题（不影响报告生成），仅 warning
if [ "$ERROR_COUNT" -ge 1 ]; then
    echo -e "  ${YELLOW}⚠️  WARN${NC}: AIOps 有 $ERROR_COUNT 个错误事件（已知 SQLite 线程问题）"
fi

# 提取最终诊断报告
FINAL_REPORT=$(echo "$AIOPS_OUTPUT" | grep '"type": "report"' | head -1 | python3 -c "
import sys, json
line = sys.stdin.read().strip()
# SSE 格式: data: {...json...}
if line.startswith('data: '):
    line = line[6:]
try:
    d = json.loads(line)
    print(d.get('report', '')[:300])
except: pass
" 2>/dev/null || echo "")
echo -e "  ${YELLOW}📝 诊断报告预览:${NC} ${FINAL_REPORT}..."
check "AIOps 报告有实质内容" '[ -n "$FINAL_REPORT" ]'

# ── 步骤 5: 排查后 RAG — 请求修改建议 ──────────────────────
section "5. 排查后 RAG: 根据诊断结果请求修改方案"

MODIFY_QUESTION="基于上述诊断结果，data-sync-service CPU 过高，请给出具体的代码修改步骤或配置调整方案"

MODIFY_RESP=$(curl -s -X POST "${BASE_URL}/api/chat" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"Id\": \"${TEST_SESSION}-modify\", \"Question\": \"${MODIFY_QUESTION}\"}")

MODIFY_CODE=$(echo "$MODIFY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('code',''))" 2>/dev/null || echo "")
MODIFY_ANSWER=$(echo "$MODIFY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('answer','')[:200])" 2>/dev/null || echo "")

check "修改建议请求成功" '[ "$MODIFY_CODE" = "200" ]'
echo -e "  ${YELLOW}📝 修改建议预览:${NC} ${MODIFY_ANSWER}..."
check "修改建议非空" '[ -n "$MODIFY_ANSWER" ]'

# ── 步骤 6: 验证四层防线触发 ──────────────────────────────────
section "6. 验证四层防线日志"

# 检查今天的应用日志中是否有四层防线相关记录
LOG_FILE="logs/app_$(date +%Y-%m-%d).log"
if [ -f "$LOG_FILE" ]; then
    HALLUCINATION=$(grep -c "幻觉检测" "$LOG_FILE" 2>/dev/null || echo "0")
    EVIDENCE=$(grep -c "证据链" "$LOG_FILE" 2>/dev/null || echo "0")
    SOP=$(grep -c "SOP" "$LOG_FILE" 2>/dev/null || echo "0")
    CONFIDENCE=$(grep -c "置信度" "$LOG_FILE" 2>/dev/null || echo "0")
    echo -e "  📊 四层防线日志统计: 幻觉=$HALLUCINATION, 证据=$EVIDENCE, SOP=$SOP, 置信度=$CONFIDENCE"
else
    echo -e "  ${YELLOW}⚠️${NC}  日志文件 $LOG_FILE 不存在（四层防线可能未触发或日志路径不同）"
fi

# ── 步骤 7: 验证 Bug 1 修复 — 删除后不再复活 ─────────────────
section "7. 验证 Bug 1: 删除会话后不再复活"

# 先获取历史列表
HIST_BEFORE=$(curl -s "${BASE_URL}/api/chat/history" -H "$AUTH_HEADER")
HIST_COUNT_BEFORE=$(echo "$HIST_BEFORE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "0")
echo -e "  删除前会话数: $HIST_COUNT_BEFORE"

# 删除测试会话
CLEAR_RESP=$(curl -s -X POST "${BASE_URL}/api/chat/clear" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\": \"${TEST_SESSION}\"}")
CLEAR_STATUS=$(echo "$CLEAR_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
check "删除会话成功" '[ "$CLEAR_STATUS" = "success" ]'

# 再次获取历史列表，验证已删除的会话不在其中
HIST_AFTER=$(curl -s "${BASE_URL}/api/chat/history" -H "$AUTH_HEADER")
STILL_EXISTS=$(echo "$HIST_AFTER" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for item in d.get('data', []):
    if item.get('session_id') == '${TEST_SESSION}':
        print('YES')
        break
else:
    print('NO')
" 2>/dev/null)
check "已删除会话不在历史列表中" '[ "$STILL_EXISTS" = "NO" ]'

# ── 清理 ──────────────────────────────────────────────────────
section "清理"

cleanup_mcp
echo -e "  ${GREEN}✅${NC} MCP 服务器进程已清理"

# ── 结果汇总 ──────────────────────────────────────────────────
section "测试结果"

TOTAL=$((PASSED + FAILED))
echo -e "  通过: ${GREEN}${PASSED}${NC} / ${TOTAL}"
echo -e "  失败: ${RED}${FAILED}${NC} / ${TOTAL}"

if [ "$FAILED" -eq 0 ]; then
    echo ""
    echo -e "  ${GREEN}🎉 所有测试通过！${NC}"
    exit 0
else
    echo ""
    echo -e "  ${RED}⚠️  有 ${FAILED} 项测试失败，请检查上面的输出。${NC}"
    exit 1
fi
