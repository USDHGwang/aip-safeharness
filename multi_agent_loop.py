import os
import re
import json
import time
import argparse
from datetime import datetime, timezone
from config.network import get_network_config

# Client 實例於 main() 取得 API key 後初始化
client = None

STATE_FILE = "state.json"
_hitl_flag = True
_aip_flag = True

_NET = get_network_config()


def _migrate_state_v1_to_v2(state: dict) -> dict:
    """v1 record 缺少 v2 欄位時，補上預設值。保留 input_from 向後相容。"""
    # rename: input_from → task_description
    if "input_from" in state and "task_description" not in state:
        state["task_description"] = state["input_from"]
    defaults = {
        "task_description": state.get("input_from", ""),
        "expected_output": "",
        "current_step": 0,
        "total_steps": 0,
        "step_status": "not_started",
        "intermediate_results": {},
    }
    for k, v in defaults.items():
        state.setdefault(k, v)
    return state


# Module-level：讓 call_llm 內部能存取當前 state context
_active_state = {
    "state_data": None,    # 整個 list
    "current_state": None  # 當前 run 的 dict ref
}


def _flush_state():
    """將 _active_state 寫入磁碟。"""
    if _active_state["state_data"] is not None:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_active_state["state_data"], f, ensure_ascii=False, indent=2)


def persist_state(func):
    def wrapper(prompt, role_system_prompt, run_id):
        agent_name = role_system_prompt.split('，')[0] if '，' in role_system_prompt else "Agent"

        # Load
        state_data = []
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                    if not isinstance(state_data, list):
                        state_data = [state_data]
            except Exception:
                state_data = []

        # 以 run_id 作為唯一主鍵，讀取時做 v1→v2 遷移
        target_state = None
        for s in state_data:
            _migrate_state_v1_to_v2(s)
            if s.get("run_id") == run_id:
                target_state = s

        if target_state:
            status = target_state.get("working_status")
            if status == "completed":
                print(f"[{agent_name}] 讀取紀錄，跳過執行 (run_id: {run_id})")
                return target_state.get("output_to")
            else:
                print(f"[{agent_name}] 從中斷狀態 ({status}) 重新啟動... (run_id: {run_id})")

        # Build v2 state
        current_state = {
            "run_id": run_id,
            "agent_id": agent_name,
            "task_description": prompt if isinstance(prompt, str) else str(prompt),
            "expected_output": "",
            "current_step": 0,
            "total_steps": 0,
            "step_status": "not_started",
            "intermediate_results": {},
            "output_to": "",
            "working_status": "running"
        }

        if target_state:
            status = target_state.get("working_status")
            if status in ("paused", "failed"):
                # Resume path: 保留歷史 intermediate_results，current_step 從 max step 繼續
                existing_results = target_state.get("intermediate_results", {})
                max_step = max(
                    (int(k.split("_")[1]) for k in existing_results
                     if k.startswith("step_") and k.split("_")[1].isdigit()),
                    default=0
                )
                current_state["intermediate_results"] = existing_results
                current_state["current_step"] = max_step
            target_state.update(current_state)
        else:
            state_data.append(current_state)
            target_state = current_state

        # Expose to call_llm
        _active_state["state_data"] = state_data
        _active_state["current_state"] = target_state
        _flush_state()

        try:
            output = func(prompt, role_system_prompt, run_id)
        except KeyboardInterrupt:
            target_state["working_status"] = "paused"
            _flush_state()
            _active_state["state_data"] = None
            _active_state["current_state"] = None
            raise
        except Exception as e:
            target_state["working_status"] = "failed"
            target_state["step_status"] = "failed"
            _flush_state()
            _active_state["state_data"] = None
            _active_state["current_state"] = None
            raise e

        target_state["output_to"] = output
        target_state["working_status"] = "completed"
        target_state["step_status"] = "done"
        _flush_state()
        _active_state["state_data"] = None
        _active_state["current_state"] = None

        return output
    return wrapper


def web_search(query: str) -> str:
    """Searches the web and returns the top 3 results."""
    print(f"[工具呼叫] web_search: {query}")
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=3):
                results.append(f"Title: {r['title']}\nURL: {r['href']}\nDescription: {r['body']}")
        if not results:
            return "No results found."
        return "\n\n".join(results)
    except ImportError:
        return "Search failed: 請先安裝套件 → pip install ddgs"
    except Exception as e:
        return f"Search failed: {e}"


# 實際呼叫 Gemini API 的函數
@persist_state
def call_llm(prompt, role_system_prompt, run_id):
    agent_name = role_system_prompt.split('，')[0] if '，' in role_system_prompt else "Agent"
    print(f"[{agent_name}] 思考中...")

    from google.genai import types

    def _generate(contents):
        """429 自動 retry，最多 3 次，每次延遲依錯誤訊息拉長，最少 15s。"""
        for attempt in range(3):
            try:
                return client.models.generate_content(
                    model='gemma-4-26b-a4b-it',
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=role_system_prompt,
                        temperature=0.7,
                        tools=[web_search],
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        )
                    )
                )
            except Exception as e:
                err = str(e)
                if '429' not in err or attempt >= 2:
                    raise
                if 'PerDay' in err or 'per_day' in err.lower():
                    print(f"[{agent_name}] 每日 quota 已耗盡，請明天再試或升級方案。")
                    raise
                import re
                match = re.search(r'retry in ([\.\d]+)s', err)
                wait = float(match.group(1)) + 5 if match else 25
                print(f"[{agent_name}] Rate limit，等待 {wait:.0f}s 後重試... (attempt {attempt+1}/3)")
                time.sleep(wait)

    response = _generate(prompt)

    # 處理 Tool call — 多輪迴圈，直到 LLM 返回純文字
    conversation = []
    if isinstance(prompt, str):
        conversation.append(prompt)
    elif isinstance(prompt, list):
        conversation.extend(prompt)

    max_tool_rounds = 10
    tool_round = 0

    while response.function_calls and tool_round < max_tool_rounds:
        tool_round += 1
        conversation.append(response.candidates[0].content)

        for tool_call in response.function_calls:
            if tool_call.name == "web_search":
                # Step tracking: before tool call
                st = _active_state["current_state"]
                if st:
                    st["current_step"] += 1
                    st["step_status"] = "in_progress"
                    _flush_state()

                query = tool_call.args.get('query', '') if isinstance(tool_call.args, dict) else getattr(tool_call.args, 'query', '')
                search_result = web_search(query)

                # Step tracking: after tool call
                if st:
                    st["intermediate_results"][f"step_{st['current_step']}"] = {
                        "tool": "web_search",
                        "query": query,
                        "result": search_result,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    st["step_status"] = "done"
                    _flush_state()

                conversation.append(types.Part.from_function_response(
                    name="web_search",
                    response={"result": search_result}
                ))

        print(f"[{agent_name}] 處理搜尋結果並繼續思考... (round {tool_round})")
        response = _generate(conversation)

    # Fallback: response.text 可能為 None（Gemma 4 有時 parts 混合 function_call 殘留）
    result = response.text
    if result is None and response.candidates:
        parts = response.candidates[0].content.parts
        text_parts = [p.text for p in parts if hasattr(p, 'text') and p.text]
        if text_parts:
            result = "\n".join(text_parts)
    if result is None:
        print(f"[{agent_name}] ⚠️ LLM 未產生文字輸出")
        result = ""
    return result


def parse_verdict(output):
    """Parse Agent B's verdict, tolerant of Markdown, whitespace, emoji, case.
    Returns "PASS", "FAIL", or None if undetermined.
    """
    if not output:
        return None
    # Remove markdown symbols *, _, `, # and surrounding whitespace
    cleaned = re.sub(r"[*_`#]+", "", output)
    # Remove common emoji ranges (basic unicode blocks)
    cleaned = re.sub(r"[\u2700-\u27BF\U0001F300-\U0001F9FF]", "", cleaned)
    cleaned = cleaned.strip().upper()
    if cleaned.startswith("PASS"):
        return "PASS"
    if cleaned.startswith("FAIL"):
        return "FAIL"
    # Fallback: search for standalone PASS/FAIL keyword anywhere
    if re.search(r"\bPASS\b", cleaned):
        return "PASS"
    if re.search(r"\bFAIL\b", cleaned):
        return "FAIL"
    return None


def parse_agent_b_output(output):
    """Parse Agent B's JSON output with fallback to regex.
    Returns dict with keys: verdict, reason, fixes, raw, parse_method.
    """
    # Strip possible markdown code fences
    cleaned = output.strip()
    # Remove leading/trailing backticks and optional language spec
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        verdict = str(parsed.get("verdict", "")).upper()
        if verdict not in ("PASS", "FAIL"):
            verdict = None
        return {
            "verdict": verdict,
            "reason": parsed.get("reason", ""),
            "fixes": parsed.get("fixes", []),
            "raw": output,
            "parse_method": "json"
        }
    except Exception:
        # Fallback to regex parsing
        return {
            "verdict": parse_verdict(output),
            "reason": output[:500],
            "fixes": [],
            "raw": output,
            "parse_method": "regex_fallback"
        }

def _build_resume_context(state_record: dict) -> str:
    """從 state record 的 intermediate_results 建構 resume context 字串。"""
    lines = ["[Resume context] This is a continuation of a previous run.",
             "Previously completed steps:"]
    ir = state_record.get("intermediate_results", {})
    # 依 step_N 數字排序
    sorted_keys = sorted(ir.keys(), key=lambda k: int(k.split("_")[1]) if "_" in k and k.split("_")[1].isdigit() else 0)
    for key in sorted_keys:
        step = ir[key]
        tool = step.get("tool", "unknown")
        query = step.get("query", "")
        result_preview = step.get("result", "")[:200]
        lines.append(f"- {key}: {tool}(query=\"{query}\") → result: \"{result_preview}...\"")
    lines.append("")
    lines.append("Do NOT repeat these searches. Continue from where you left off.")
    lines.append(f"Original task: {state_record.get('task_description', '')}")
    return "\n".join(lines)


def _load_state_data() -> list:
    """讀取 state.json 並回傳 list，含 v1→v2 遷移。"""
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                data = [data]
        for s in data:
            _migrate_state_v1_to_v2(s)
        return data
    except Exception:
        return []


def resume_agent(run_id: str) -> str | None:
    """讀取 state.json，根據 run_id 從中斷處繼續執行。"""
    state_data = _load_state_data()

    target = None
    for s in state_data:
        if s.get("run_id") == run_id:
            target = s
            break

    if target is None:
        print(f"[Resume] run_id={run_id} 不存在於 state.json。")
        return None

    status = target.get("working_status", "")

    if status == "completed":
        print(f"[Resume] run_id={run_id} 已完成，無需重新執行。")
        print(f"[Resume] 既有輸出: {target.get('output_to', '')[:200]}")
        return target.get("output_to")

    if status == "running":
        print(f'[Resume] run_id={run_id} is in "running" state. '
              f'Another process may be active. If you\'re sure it\'s stale, '
              f'manually edit state.json or run with --force-resume.')
        return None

    if status not in ("paused", "failed"):
        print(f"[Resume] run_id={run_id} 狀態為 \"{status}\"，無法恢復。")
        return None

    # Resume logic: paused / failed
    print(f"[Resume] 從 \"{status}\" 狀態恢復 run_id={run_id}")

    # 決定 agent prompt — 從 agent_id 推斷
    agent_id = target.get("agent_id", "")
    if "Agent B" in agent_id:
        role_prompt = ("你是 Agent B，一位嚴格的代碼審查員 (Reviewer)。你的任務是檢查 Agent A 的輸出。\n"
                       "當審查結果合格時，請以 'PASS:' 開頭回覆；若有缺陷，請以 'FAIL:' 開頭並給出具體修改建議。")
    else:
        role_prompt = ("你是 Agent A，一位資深開發者 (Developer)。你的任務是根據用戶需求編寫程式碼，"
                       "並能根據審查員的反饋進行修改。\n"
                       "When asked to check package versions, security vulnerabilities, or any current "
                       "technical information, you MUST use the web_search tool first before answering.")

    # 建構 resume context
    resume_context = _build_resume_context(target)
    prompt = resume_context

    # 呼叫 call_llm — persist_state 會以相同 run_id 更新 in-place
    output = call_llm(prompt=prompt, role_system_prompt=role_prompt, run_id=run_id)
    print(f"\n[Resume] 完成。最終輸出:\n{output}")
    return output


def list_resumable():
    """列出所有 paused / failed 狀態的 run，供使用者挑選恢復。"""
    state_data = _load_state_data()
    resumable = [s for s in state_data if s.get("working_status") in ("paused", "failed")]
    if not resumable:
        print("[List] 沒有可恢復的 run。")
        return
    print(f"[List] 找到 {len(resumable)} 個可恢復的 run:\n")
    for s in resumable:
        task_preview = (s.get("task_description", "") or "")[:80]
        steps = len(s.get("intermediate_results", {}))
        print(f"  run_id:  {s.get('run_id')}")
        print(f"  agent:   {s.get('agent_id')}")
        print(f"  status:  {s.get('working_status')}")
        print(f"  steps:   {steps}")
        print(f"  task:    {task_preview}")
        print()


def _init_client() -> bool:
    """Initialize Gemini client, return True on success."""
    global client
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env or environment")
    print("Using API key from environment")
    client = genai.Client(api_key=api_key)
    return True

def _trigger_0g_upload(run_id: str, status: str) -> bool:
    """Upload state snapshot to 0G Storage. Returns True on success, False on failure."""
    print(f"[0G] Uploading snapshot (state: {status})...")
    import zerog_bridge
    try:
        res = zerog_bridge.upload_state_snapshot(STATE_FILE)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "working_status": status,
            "root_hash": res.get("root_hash"),
            "tx_hash": res.get("tx_hash"),
            "tx_seq": res.get("tx_seq"),
        }
        zerog_bridge.append_index(run_id, entry)
        print(f"[0G] Upload success. Root Hash: {res.get('root_hash')}")
        return True
    except Exception as e:
        print(f"[0G] Upload FAILED (state: {status}): {e}")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "working_status": status,
            "root_hash": None,
            "storage_failed": True,
            "error": str(e)[:200],
        }
        zerog_bridge.append_index(run_id, entry)
        return False


def hitl_enabled() -> bool:
    return _hitl_flag


def aip_enabled() -> bool:
    return _aip_flag


def _trigger_0g_snapshot_upload(run_id: str, working_status: str) -> bool:
    return _trigger_0g_upload(run_id, working_status)


def _set_awaiting_human_and_upload(run_id, agent_id, task_description, output_to):
    state_data = _load_state_data()
    record = {
        "run_id": run_id,
        "agent_id": agent_id,
        "task_description": task_description,
        "expected_output": "",
        "current_step": 0,
        "total_steps": 0,
        "step_status": "done",
        "intermediate_results": {},
        "output_to": output_to,
        "working_status": "awaiting_human"
    }
    state_data.append(record)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)
    _trigger_0g_snapshot_upload(run_id, "awaiting_human")


def _mark_hitl_completed(run_id, final_output) -> bool:
    state_data = _load_state_data()
    for s in state_data:
        if s.get("run_id") == run_id:
            s["working_status"] = "completed"
            s["output_to"] = final_output
            break
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)
    return _trigger_0g_snapshot_upload(run_id, "completed")


def _mark_hitl_failed(run_id, reason):
    state_data = _load_state_data()
    for s in state_data:
        if s.get("run_id") == run_id:
            s["working_status"] = "failed"
            s["output_to"] = f"[HITL rejected/modified] {reason}"
            break
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)
    _trigger_0g_snapshot_upload(run_id, "failed")


def _update_hitl_record(run_id: str, **updates):
    state_data = _load_state_data()
    for s in state_data:
        if s.get("run_id") == run_id:
            s.update(updates)
            break
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)


def request_human_approval(state_record: dict) -> dict:
    """Prompt the human for approve/reject/modify decision."""
    print("\n" + "=" * 60)
    print("[HITL] Agent B PASS. Human review point.")
    if state_record:
        print(f"Run ID: {state_record.get('run_id')}")
        desc = state_record.get('task_description', '')
        print(f"Task: {desc[:200]}{'...' if len(desc) > 200 else ''}")
        output = state_record.get('output_to', '')
        print("\n--- Agent A Final Output ---")
        print(output[:1000])
        if len(output) > 1000:
            print("...\n")
    print("=" * 60)
    print("Options:")
    print("  [a] approve  - Accept output, mark state as completed")
    print("  [r] reject   - Reject output, mark state as failed")
    print("  [m] modify   - Provide feedback for Agent A to revise")
    print("=" * 60)
    while True:
        try:
            choice = input("Your choice [a/r/m]: ").strip().lower()
        except EOFError:
            print("[auto-approve]")
            return {"decision": "approve", "feedback": None}
        if not choice:
            continue
        if choice in ("a", "approve"):
            return {"decision": "approve", "feedback": None}
        if choice in ("r", "reject"):
            reason = input("Rejection reason (optional): ").strip()
            return {"decision": "reject", "feedback": reason or None}
        if choice in ("m", "modify"):
            feedback = input("Enter your modification feedback: ").strip()
            if not feedback:
                print("Modify requires feedback. Please choose again.")
                continue
            return {"decision": "modify", "feedback": feedback}
        print("Invalid choice. Enter a / r / m.")


def main():
    parser = argparse.ArgumentParser(description="AIP SafeHarness Multi-Agent Demo")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume from specified run_id")
    parser.add_argument("--list-resumable", action="store_true", help="List all resumable runs")
    parser.add_argument("--verify", metavar="RUN_ID", help="Verify local state against on-chain snapshot")
    parser.add_argument(
        "--hitl",
        choices=["on", "off"],
        default=None,
        help="Toggle HITL. Default reads HITL_ENABLED env var, otherwise on."
    )
    parser.add_argument(
        "--aip",
        choices=["on", "off"],
        default=None,
        help="Toggle AIP on-chain submission. Default reads AIP_ENABLED env var, otherwise on."
    )
    args = parser.parse_args()

    global _hitl_flag
    if args.hitl == "on":
        _hitl_flag = True
    elif args.hitl == "off":
        _hitl_flag = False
    else:
        _hitl_flag = os.environ.get("HITL_ENABLED", "on").lower() != "off"

    global _aip_flag
    if args.aip == "on":
        _aip_flag = True
    elif args.aip == "off":
        _aip_flag = False
    else:
        _aip_flag = os.environ.get("AIP_ENABLED", "on").lower() != "off"

    print("=" * 60)
    print(f"  AIP SafeHarness -- {_NET['network_name']}")
    print(f"  Chain ID : {_NET['chain_id']}")
    print(f"  RPC      : {_NET['rpc_url']}")
    print(f"  Explorer : {_NET['explorer']}")
    print(f"  HITL     : {'on' if _hitl_flag else 'off'}")
    print(f"  AIP      : {'on' if _aip_flag else 'off'}")
    print("=" * 60)

    if args.verify:
        run_id = args.verify
        print(f"[Verify] run_id={run_id} ...")
        state_data = _load_state_data()
        target = next((s for s in state_data if s.get("run_id") == run_id), None)
        if not target:
            print(f"[Verify] No record found for {run_id}.")
            return
        root_hash = target.get("root_hash", "")
        print(f"[Verify] Local root hash: {root_hash}")
        if root_hash:
            print(f"[Verify] On-chain: {_NET['explorer']}/storage/{root_hash}")
        return

    if args.list_resumable:
        list_resumable()
        return

    if args.resume:
        if not _init_client():
            return
        resume_agent(args.resume)
        return

    if not _init_client():
        return

    agent_a_prompt = "You are Agent A, a DeFi swap assistant. Your ONLY job is to produce a swap action plan when the user asks to swap tokens. Output a JSON object with fields: action, from, to, amount, slippage. Do NOT explain DeFi concepts or give tutorials. Just output the plan."
    agent_b_prompt = """You are Agent B, a strict swap intent reviewer. Your task is to check Agent A's output.
When the review passes, respond with a JSON object starting with "PASS:".
When it fails, respond with "FAIL:" followed by specific suggestions.

# Output format (strict)
Your final response must be a JSON object, no other text, no markdown code block:
{
  "verdict": "PASS" | "FAIL",
  "reason": "Brief explanation of why PASS or FAIL",
  "fixes": ["fix suggestion 1", "fix suggestion 2"]
}

verdict must be "PASS" or "FAIL" (uppercase).
If verdict is "PASS", fixes can be empty array [].
If verdict is "FAIL", fixes must list specific suggestions.
"""

    print("=== AIP SafeHarness Demo ===")

    user_input = input("\n[User] Enter your intent: ").strip()
    if not user_input:
        print("No intent entered. Exiting.")
        return
    print(f"[User] Intent received: {user_input}")

    agent_a_output = call_llm(prompt=user_input, role_system_prompt=agent_a_prompt, run_id="agent_a_initial")
    print(f"\n[Agent A] Output:\n{agent_a_output}")

    max_loops = 3
    current_loop = 1

    while current_loop <= max_loops:
        print(f"\n--- Round {current_loop} Review ---")

        check_prompt = f"Review the following.\nUser intent: {user_input}\nAgent A output: {agent_a_output}"
        agent_b_output = call_llm(prompt=check_prompt, role_system_prompt=agent_b_prompt, run_id=f"agent_b_loop_{current_loop}")
        print(f"[Agent B] Review result:\n{agent_b_output}")

        b_result = parse_agent_b_output(agent_b_output)
        print(f"[System] Verdict: {b_result['verdict']} (parsed via {b_result['parse_method']})")

        if b_result["verdict"] == "PASS":
            if not hitl_enabled():
                print("\n[System] Review passed! Done.")
                print(f"=== Final output for User ===\n{agent_a_output}")
                break

            print("\n[System] Agent B PASS. Entering HITL checkpoint...")
            hitl_run_id = f"hitl_review_{current_loop}"
            _set_awaiting_human_and_upload(
                run_id=hitl_run_id,
                agent_id="hitl",
                task_description=user_input,
                output_to=agent_a_output,
            )

            state_records = _load_state_data()
            review_record = next((s for s in state_records if s.get("run_id") == hitl_run_id), None)
            decision = request_human_approval(review_record)

            if decision["decision"] == "approve":
                aip_tx_hash = None
                submitting_ok = True

                if aip_enabled():
                    _update_hitl_record(hitl_run_id, working_status="submitting_onchain")
                    submitting_ok = _trigger_0g_snapshot_upload(hitl_run_id, "submitting_onchain")

                    print("\n[AIP] Submitting intent on-chain...")
                    try:
                        from aip_client import AIPClient, AIPError
                        aip_client_inst = AIPClient()
                        intent_data = aip_client_inst.compute_intent_hash(
                            agent_output=agent_a_output,
                            run_id=hitl_run_id,
                        )
                        aip_result = aip_client_inst.execute_with_aip_check(intent_data)
                        aip_tx_hash = aip_result["tx_hash"]

                        _update_hitl_record(hitl_run_id, aip_tx_hash=aip_tx_hash)
                        print(f"[AIP] On-chain success. tx: {aip_tx_hash}")
                        print(f"[AIP]    Explorer: {_NET['explorer']}/tx/{aip_tx_hash}")
                    except Exception as e:
                        _mark_hitl_failed(hitl_run_id, f"AIP onchain submission failed: {e}")
                        print(f"\n[AIP] On-chain failed: {e}")
                        break

                completed_ok = _mark_hitl_completed(hitl_run_id, agent_a_output)
                if not submitting_ok or not completed_ok:
                    _update_hitl_record(hitl_run_id, working_status="storage_partial")
                    print("\n[HITL] Approved (AIP on-chain succeeded).")
                    print("[0G] WARNING: one or more storage snapshots failed.")
                    print("[0G]   Run recorded as 'storage_partial' — not demo-grade evidence.")
                    print("[0G]   Check root_hash_index.json for storage_failed=True entries.")
                else:
                    print("\n[HITL] Approved.")
                if aip_tx_hash:
                    print(f"   AIP tx hash: {aip_tx_hash}")
                print(f"=== Final output for User ===\n{agent_a_output}")
                break

            elif decision["decision"] == "reject":
                _mark_hitl_failed(hitl_run_id, decision.get("feedback", ""))
                print("\n[HITL] Rejected. Exiting.")
                break

            elif decision["decision"] == "modify":
                _mark_hitl_failed(hitl_run_id, f"modify: {decision['feedback']}")
                print("\n[HITL] Modify. Sending back to Agent A.")
                revert_prompt = (
                    f"Human requested modification.\nFeedback: {decision['feedback']}\n"
                    f"Your previous output: {agent_a_output}"
                )
                agent_a_output = call_llm(
                    prompt=revert_prompt,
                    role_system_prompt=agent_a_prompt,
                    run_id=f"agent_a_human_modify_{current_loop}"
                )
                print(f"\n[Agent A] Revised output:\n{agent_a_output}")
                current_loop += 1
                continue
        else:
            print("\n[System] Review FAILED. Sending back to Agent A.")
            fixes_text = "\n".join(f"- {f}" for f in b_result.get("fixes", []) if f)
            feedback = b_result.get("reason", "")
            if fixes_text:
                feedback = f"{feedback}\nSuggested fixes:\n{fixes_text}"
            revert_prompt = (
                f"Your output was rejected. Revise based on this feedback.\n"
                f"Feedback: {feedback}\nYour previous output: {agent_a_output}"
            )
            agent_a_output = call_llm(
                prompt=revert_prompt,
                role_system_prompt=agent_a_prompt,
                run_id=f"agent_a_revision_{current_loop}"
            )
            print(f"\n[Agent A] Revised output:\n{agent_a_output}")

        current_loop += 1
        if current_loop > max_loops:
            print("\n[System] Max rounds reached. Force terminating workflow.")


if __name__ == "__main__":
    main()
