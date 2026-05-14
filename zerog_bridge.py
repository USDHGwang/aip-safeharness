import subprocess
import json
import os
import time
import urllib.request


def _verify_upload(root_hash: str, indexer_url: str, max_retries: int = 6, backoff: int = 10) -> None:
    """Poll GET /file?root= to confirm file data reached 0G storage nodes.
    Success: response body is real file content (not the {"code":101} not-found sentinel).
    Raises RuntimeError after max_retries failures."""
    url = f"{indexer_url.rstrip('/')}/file?root={root_hash}"
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                body = resp.read().decode(errors="replace")
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict) and parsed.get("code") == 101:
                        print(f"[0G verify] attempt {attempt}/{max_retries}: not on nodes yet (root={root_hash})")
                    else:
                        print(f"[0G verify] OK (attempt {attempt}/{max_retries}, root={root_hash})")
                        return
                except json.JSONDecodeError:
                    if body.strip():
                        print(f"[0G verify] OK (attempt {attempt}/{max_retries}, root={root_hash})")
                        return
                    print(f"[0G verify] attempt {attempt}/{max_retries}: empty response")
        except Exception as e:
            print(f"[0G verify] attempt {attempt}/{max_retries}: request error: {e}")
        if attempt < max_retries:
            time.sleep(backoff)
    raise RuntimeError(
        f"0G upload verification FAILED after {max_retries} attempts — "
        f"file not found on storage nodes (root={root_hash})"
    )


def upload_state_snapshot(state_file_path: str) -> dict:
    """Upload a state file to 0G Storage and verify data reached nodes.
    Returns {"root_hash": ..., "tx_hash": ..., "tx_seq": ...} or raises."""
    result = subprocess.run(
        ["node", "upload_to_0g.js", state_file_path],
        capture_output=True, text=True, timeout=240,
        cwd=os.path.dirname(os.path.abspath(state_file_path)) or None
    )
    if result.returncode != 0:
        raise RuntimeError(f"0G upload failed (rc={result.returncode}): {result.stderr or result.stdout}")

    stdout_text = result.stdout.strip()
    try:
        json_str = stdout_text.split('\n')[-1].strip()
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise RuntimeError(f"0G upload returned invalid JSON: {stdout_text}")

    root_hash = data.get("root_hash")
    if not root_hash:
        raise RuntimeError(f"0G upload returned no root_hash: {data}")

    from config.network import get_network_config
    cfg = get_network_config()
    _verify_upload(root_hash, cfg["storage_indexer"])

    return data


def download_state_snapshot(root_hash: str, dest_path: str) -> bool:
    """Download a state snapshot from 0G by root hash."""
    result = subprocess.run(
        ["node", "download_from_0g.js", root_hash, dest_path],
        capture_output=True, text=True, timeout=90,
        cwd=os.path.dirname(os.path.abspath(dest_path)) or None
    )
    return result.returncode == 0

INDEX_FILE = "root_hash_index.json"

def load_index() -> dict:
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def append_index(run_id: str, entry: dict) -> None:
    data = load_index()
    if run_id not in data:
        data[run_id] = []
    data[run_id].append(entry)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def latest_snapshot(run_id: str) -> dict | None:
    data = load_index()
    history = data.get(run_id, [])
    if not history:
        return None
    return history[-1]
